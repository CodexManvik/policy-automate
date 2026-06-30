import logging
_logger = logging.getLogger("claims_adjudication_pipeline")
from typing import List, Tuple, Optional, Any, Dict
from datetime import datetime, timezone, date
import copy
import time
import asyncio
import threading

from pipeline_modules.shared import _STEP_LOCAL

from schemas import (
    ClaimContext, ClaimDecision, LineItemDecision, DeductionDetail,
    DecisionTrace, PerClaimState, DeductionBreakdown, SIWaterfallBreakdown
)
from calculators import (
    calculate_waiting_period, calculate_room_pro_rata, calculate_copayment,
    calculate_deductible, calculate_si_waterfall, calculate_lock_the_clock,
    calculate_booster_accumulation, validate_pre_post_hosp_window,
    calculate_hospital_daily_cash, calculate_personal_accident_benefit
)
from product_memory import get_product_memory, RuleGate, ExecutionType
from config import settings
from planner import AIPlanner, ExecutionPlan, ExecutionStep
from semantic_agent import SemanticExecutionAgent
from agent_reasoning import AgentReasoningLogger
from metrics import telemetry_context, TelemetrySession, PipelineMetricsEngine

from pipeline_modules.helpers import PipelineHelpersMixin
from pipeline_modules.gates_validation import ValidationGatesMixin
from pipeline_modules.gate_financials import FinancialsGateMixin
from pipeline_modules.gate_state import StateGateMixin
from pipeline_modules.execution import ExecutionMixin

class ClaimsAdjudicationPipeline(
    PipelineHelpersMixin,
    ValidationGatesMixin,
    FinancialsGateMixin,
    StateGateMixin,
    ExecutionMixin
):
    """
Dynamic Graph-Based Execution Pipeline - Phase 2

Evolution from Phase 1:
- Uses AI Planner to construct DAG from rule dependencies
- Dynamically routes execution based on rule type (deterministic vs semantic)
- Integrates Semantic Agent for exclusion/coverage reasoning
- Confidence-based routing to manual review

Gate Sequence (now dynamic based on plan):
1. Policy Validation
2. Member Validation
3. Coverage Validation (AI-enhanced)
4. Waiting Period Validation (AI-enhanced)
5. Exclusion Validation (AI-driven)
6. Financial Computation (deterministic tools)
7. State Update
    """

    def __init__(
        self,
        use_ai: bool = True,
        confidence_threshold: float = 0.90,
        assisted_review_threshold: float = 0.70,
        medical_review_threshold: float = 0.50,  # Gap 8: MEDICAL_REVIEW tier (Section 9.2)
        llm_provider: str = "local",
        local_llm_url: str = "http://127.0.0.1:8080"
    ):
        # NOTE: decision_traces and current_step are NOT stored on the instance.
        # They are per-call local variables initialized inside adjudicate_claim()
        # and _execute_plan() to ensure thread safety when the singleton pipeline
        # handles concurrent requests.
        self.use_ai = use_ai
        self.confidence_threshold = confidence_threshold        # >= 0.90 → auto-approve
        self.assisted_review_threshold = assisted_review_threshold  # 0.70–0.90 → ASSISTED_REVIEW
        self.medical_review_threshold = medical_review_threshold    # 0.50–0.70 → MEDICAL_REVIEW (Gap 8)
        
        # Resolve provider: if 'default' or blank was passed, default to 'local'.
        resolved_provider = llm_provider
        if resolved_provider in ("default", ""):
            resolved_provider = "local"

        # Validate local LLM connectivity at startup if AI is enabled and using local LLM
        if use_ai and resolved_provider == "local":
            from urllib.parse import urlparse
            import socket
            try:
                parsed = urlparse(local_llm_url)
                h = parsed.hostname or "127.0.0.1"
                if h == "localhost":
                    h = "127.0.0.1"
                p = parsed.port or 8080
                with socket.create_connection((h, p), timeout=2.0):
                    pass
            except Exception as exc:
                raise RuntimeError(
                    f"Local LLM is not running or unreachable at {local_llm_url}. "
                    f"Connection error: {exc}"
                ) from exc

        _logger.info(
            "Initializing pipeline: provider=%s url=%s ai=%s",
            resolved_provider, local_llm_url, use_ai
        )
        # Initialize AI components
        self.product_memory = get_product_memory()
        self.planner = AIPlanner(self.product_memory)
        self.semantic_agent = SemanticExecutionAgent(
            llm_provider=resolved_provider,
            confidence_threshold=confidence_threshold,
            local_llm_url=local_llm_url
        ) if use_ai else None
        # Observability counters (safe: only ever incremented, never read during a call)
        self.plans_created = 0
        self.semantic_calls = 0
        self.manual_review_count = 0

    def adjudicate_claim(self, context: ClaimContext) -> ClaimDecision:
        """
        Main entry point: Execute dynamic graph-based adjudication pipeline
        
        Phase 2 Enhancements:
        - AI Planner creates execution plan with DAG
        - Dynamic routing based on execution type
        - Semantic agent for non-deterministic rules
        - Confidence tracking and auto-routing
        
        Args:
            context: Complete claim context from Context Builder
        
        Returns:
            ClaimDecision with full audit trail and confidence score
        """
        session = TelemetrySession()
        token = telemetry_context.set(session)
        
        claim_decision = None
        try:
            start_time = time.time()
            # Initialize thread-local step counter and trace buffer for this call's gate methods
            _STEP_LOCAL.current_step = 0
            _STEP_LOCAL.decision_traces = []
            # Per-call local state — thread-safe by design (not stored on self)
            decision_traces: List[DecisionTrace] = []
            current_step = 0
            # Gap 11: Context staleness guard (Section 4.6 / API contract).
            _max_age_minutes = settings.context_max_age_minutes
            _assembled_at = getattr(context, 'context_assembled_at', None)
            if _assembled_at is not None:
                _now = datetime.now(timezone.utc)
                if _assembled_at.tzinfo is None:
                    _assembled_at = _assembled_at.replace(tzinfo=timezone.utc)
                _age_minutes = (_now - _assembled_at).total_seconds() / 60
                if _age_minutes > _max_age_minutes:
                    raise ValueError(
                        f"Stale ClaimContext for claim {context.claim_id}: assembled "
                        f"{_age_minutes:.1f}m ago (max allowed: {_max_age_minutes}m). "
                        "Re-assemble context before submitting for adjudication."
                    )
            # Issue 21 & Gap 10: Bind product memory to the version declared in this claim's context.
            claim_version = getattr(context, "product_json_version", "")
            if not claim_version:
                try:
                    product_id = context.policy.product_code
                    variant = context.policy.variant
                    policy_date = context.policy.policy_start_date
                    if isinstance(policy_date, datetime):
                        policy_date = policy_date.date()
                    elif isinstance(policy_date, str):
                        policy_date = date.fromisoformat(policy_date.split("T")[0])
                    
                    from product_memory import resolve_product_version
                    resolved_entry = resolve_product_version(product_id, variant, policy_date)
                    if resolved_entry:
                        claim_version = resolved_entry["version_id"]
                except Exception as e:
                    _logger.warning("Failed to auto-resolve product version from policy details: %s", e)
            # Fix 1: Resolve product memory for this claim version as a LOCAL variable.
            # Never write the resolved version back to self.product_memory or
            # self.planner.product_memory — doing so is a data race when two concurrent
            # claims carry different product_json_version values, because they share the
            # same singleton instance.
            call_product_memory = get_product_memory(claim_version)
            if call_product_memory is not self.product_memory:
                # Version differs from the singleton default.  Create a call-scoped planner
                # that uses the correct version memory without touching self.
                from planner import AIPlanner as _AIPlanner
                call_planner = _AIPlanner(call_product_memory)
            else:
                call_planner = self.planner
            # Initialize per-claim state
            state = PerClaimState(claim_id=context.claim_id)
            # Validate mutual exclusivity constraints before processing
            mx_violation = self._validate_mutual_exclusivity(context, gate="policy_validation")
            if mx_violation:
                constraint_id, reason = mx_violation
                self.manual_review_count += 1
                # Log trace for mutual exclusivity violation
                mx_trace = DecisionTrace(
                    step=0,
                    rule_id=constraint_id,
                    rule_name="Mutual Exclusivity Validation",
                    gate="policy_validation",
                    inputs={
                        "co_payment_percent": context.policy.co_payment_percent,
                        "annual_aggregate_deductible": context.policy.annual_aggregate_deductible,
                        "borderless_opted": context.policy.borderless_opted,
                        "borderless_specific_illness_opted": context.policy.borderless_specific_illness_opted,
                        "tiered_network_opted": context.policy.tiered_network_opted,
                        "heads_up_opted": context.policy.heads_up_opted,
                    },
                    evaluation="PENDING_REVIEW",
                    reason=reason,
                    confidence=1.0,
                    source_section="Extraction JSON: mutual_exclusivity_constraints",
                )
                decision_traces.append(mx_trace)
                # Log gate evaluation
                AgentReasoningLogger.log_gate_evaluation(
                    claim_id=context.claim_id,
                    line_item_id=None,
                    gate="policy_validation",
                    rule_id=constraint_id,
                    inputs=mx_trace.inputs,
                    evaluation_status=mx_trace.evaluation,
                    reason=mx_trace.reason
                )
                # Return claim with PENDING_REVIEW status
                claim_decision = self._create_claim_review_decision(
                    context, constraint_id, reason, decision_traces, start_time
                )
                return claim_decision
            # Fix 3: Deep-copy the ClaimContext before applying endorsements so that
            # the original caller-supplied object is never mutated.  If the pipeline
            # raises mid-claim after an endorsement is applied, the caller’s context
            # remains in its original state and any retry or audit replay will produce
            # a consistent result against the unmodified context.
            context_for_adjudication = copy.deepcopy(context)
            # Issue 11: Apply all mid-term endorsements that are effective by the
            # earliest admission date across line items (or claim receipt date as fallback).
            claim_event_date = self._coerce_to_date(
                min(
                    (li.admission_date or li.expense_date for li in context_for_adjudication.line_items),
                    default=context_for_adjudication.claim_received_at
                )
            )
            self._apply_endorsements(context_for_adjudication, claim_event_date)
            # Process each line item through dynamic execution plan
            line_item_decisions: List[LineItemDecision] = []
            for line_item in context_for_adjudication.line_items:

                # ---------------------------------------------------------------
                # MANDATORY STRUCTURAL PRE-CHECKS (Gates 1 & 2)
                # These checks are NOT in the product JSON rule blueprints and
                # are therefore never generated by the AIPlanner.  They MUST be
                # enforced explicitly before the dynamic plan runs so that invalid
                # policies and ineligible members are rejected unconditionally.
                # ---------------------------------------------------------------
                g1_passed, g1_trace = self._gate_1_policy_validation(context_for_adjudication, line_item)
                if not g1_passed:
                    decision_traces.append(g1_trace)
                    decision = self._create_rejected_decision(line_item, g1_trace.reason, [g1_trace])
                    line_item_decisions.append(decision)
                    continue
                g2_passed, g2_trace = self._gate_2_member_validation(context_for_adjudication, line_item)
                if not g2_passed:
                    decision_traces.append(g2_trace)
                    decision = self._create_rejected_decision(line_item, g2_trace.reason, [g2_trace])
                    line_item_decisions.append(decision)
                    continue
                # Phase 2: Create execution plan using call-scoped planner (Fix 1)
                execution_plan = call_planner.create_execution_plan(context_for_adjudication, line_item)
                self.plans_created += 1
                
                # Log plan compilation
                steps_serialized = []
                for step in execution_plan.execution_steps:
                    step_dict = {
                        "step_number": step.step_number,
                        "rule_id": step.rule_id,
                        "rule_name": step.rule_name,
                        "gate": step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                        "priority": step.priority,
                        "reason": step.reason,
                        "execution_type": step.execution_type,
                        "tool_required": step.tool_required,
                        "semantic_prompt": step.semantic_prompt[:100] + "..." if step.semantic_prompt else None,
                        "depends_on": step.depends_on
                    }
                    steps_serialized.append(step_dict)
                AgentReasoningLogger.log_planning(
                    claim_id=context.claim_id,
                    line_item_id=line_item.line_item_id,
                    variant=context.policy.variant,
                    benefit_bucket=line_item.benefit_bucket,
                    steps=steps_serialized,
                    depth=execution_plan.dependency_depth
                )
                
                # Execute the plan
                decision = self._execute_plan(execution_plan, line_item, context_for_adjudication, state)
                line_item_decisions.append(decision)
                
                # Consolidate traces into per-call local list
                if hasattr(decision, "decision_trace") and decision.decision_trace:
                    decision_traces.extend(decision.decision_trace)
            
            # Gate 7: State Update and Persistence (BUG FIX #10)
            self._gate_7_state_update(context_for_adjudication, state, line_item_decisions)
            # Merge Gate 7 cross-cutting traces (_STEP_LOCAL.decision_traces) into
            # the claim-level decision_traces so they appear in ClaimDecision.decision_trace.
            gate_7_traces = getattr(_STEP_LOCAL, 'decision_traces', [])
            if gate_7_traces:
                decision_traces.extend(gate_7_traces)
            # Compose final claim-level decision
            claim_decision = self._compose_claim_decision(
                context_for_adjudication, line_item_decisions, state, start_time, decision_traces
            )
            try:
                from pipeline_modules.graph_exporter import save_adjudication_graph
                save_adjudication_graph(claim_decision, context)
            except Exception as e:
                _logger.warning("Failed to save adjudication debug graph: %s", e)
            return claim_decision
        finally:
            if claim_decision:
                try:
                    engine = PipelineMetricsEngine(settings.telemetry_file)
                    engine.record_execution(claim_decision, session)
                except Exception:
                    pass
            telemetry_context.reset(token)


    async def adjudicate_claim_async(self, context: ClaimContext) -> ClaimDecision:
        """
        Asynchronous entry point: Execute dynamic graph-based adjudication pipeline in parallel
        
        Uses depth-layer grouping to execute independent rules concurrently.
        """
        session = TelemetrySession()
        token = telemetry_context.set(session)
        
        claim_decision = None
        try:
            start_time = time.time()
            # Initialize thread-local step counter and trace buffer for this call's gate methods
            _STEP_LOCAL.current_step = 0
            _STEP_LOCAL.decision_traces = []
            # Per-call local state — thread-safe by design (not stored on self)
            decision_traces: List[DecisionTrace] = []
            current_step = 0
            # Gap 11: Context staleness guard (Section 4.6 / API contract).
            _max_age_minutes = settings.context_max_age_minutes
            _assembled_at = getattr(context, 'context_assembled_at', None)
            if _assembled_at is not None:
                _now = datetime.now(timezone.utc)
                if _assembled_at.tzinfo is None:
                    _assembled_at = _assembled_at.replace(tzinfo=timezone.utc)
                _age_minutes = (_now - _assembled_at).total_seconds() / 60
                if _age_minutes > _max_age_minutes:
                    raise ValueError(
                        f"Stale ClaimContext for claim {context.claim_id}: assembled "
                        f"{_age_minutes:.1f}m ago (max allowed: {_max_age_minutes}m). "
                        "Re-assemble context before submitting for adjudication."
                    )
            # Issue 21 & Gap 10: Bind product memory to the version declared in this claim's context.
            claim_version = getattr(context, "product_json_version", "")
            if not claim_version:
                try:
                    product_id = context.policy.product_code
                    variant = context.policy.variant
                    policy_date = context.policy.policy_start_date
                    if isinstance(policy_date, datetime):
                        policy_date = policy_date.date()
                    elif isinstance(policy_date, str):
                        policy_date = date.fromisoformat(policy_date.split("T")[0])
                    
                    from product_memory import resolve_product_version
                    resolved_entry = resolve_product_version(product_id, variant, policy_date)
                    if resolved_entry:
                        claim_version = resolved_entry["version_id"]
                except Exception as e:
                    _logger.warning("Failed to auto-resolve product version from policy details: %s", e)
            # Fix 1: Same race-free pattern as adjudicate_claim — keep version memory local.
            call_product_memory = get_product_memory(claim_version)
            if call_product_memory is not self.product_memory:
                from planner import AIPlanner as _AIPlanner
                call_planner = _AIPlanner(call_product_memory)
            else:
                call_planner = self.planner
            # Initialize per-claim state
            state = PerClaimState(claim_id=context.claim_id)
            # Validate mutual exclusivity constraints before processing
            mx_violation = self._validate_mutual_exclusivity(context)
            if mx_violation:
                constraint_id, reason = mx_violation
                self.manual_review_count += 1
                # Log trace for mutual exclusivity violation
                mx_trace = DecisionTrace(
                    step=0,
                    rule_id=constraint_id,
                    rule_name="Mutual Exclusivity Validation",
                    gate="policy_validation",
                    inputs={
                        "co_payment_percent": context.policy.co_payment_percent,
                        "annual_aggregate_deductible": context.policy.annual_aggregate_deductible,
                        "borderless_opted": context.policy.borderless_opted,
                        "borderless_specific_illness_opted": context.policy.borderless_specific_illness_opted,
                        "tiered_network_opted": context.policy.tiered_network_opted,
                        "heads_up_opted": context.policy.heads_up_opted,
                    },
                    evaluation="PENDING_REVIEW",
                    reason=reason,
                    confidence=1.0,
                    source_section="Extraction JSON: mutual_exclusivity_constraints",
                )
                decision_traces.append(mx_trace)
                # Return claim with PENDING_REVIEW status
                claim_decision = self._create_claim_review_decision(
                    context, constraint_id, reason, [mx_trace], start_time
                )
                return claim_decision

            # Fix 3: Deep-copy context before applying endorsements (same as sync path).
            # Each concurrent async adjudication gets its own mutated copy of the context
            # so endorsements applied for one in-flight claim never bleed into another.
            context_for_adjudication = copy.deepcopy(context)
            # Apply mid-term endorsements to the isolated copy.
            claim_event_date_async = self._coerce_to_date(
                min(
                    (li.admission_date or li.expense_date for li in context_for_adjudication.line_items),
                    default=context_for_adjudication.claim_received_at
                )
            )
            self._apply_endorsements(context_for_adjudication, claim_event_date_async)
            # Gate 1 (policy validity) and Gate 2 (member eligibility) are structural
            # pre-conditions not covered by the AIPlanner; they must always run first.
            line_item_decisions: List[LineItemDecision] = []
            tasks = []
            for line_item in context_for_adjudication.line_items:
                # Mandatory structural pre-checks before handing off to the async plan.
                g1_passed, g1_trace = self._gate_1_policy_validation(context_for_adjudication, line_item)
                if not g1_passed:
                    line_item_decisions.append(
                        self._create_rejected_decision(line_item, g1_trace.reason, [g1_trace])
                    )
                    continue
                g2_passed, g2_trace = self._gate_2_member_validation(context_for_adjudication, line_item)
                if not g2_passed:
                    line_item_decisions.append(
                        self._create_rejected_decision(line_item, g2_trace.reason, [g2_trace])
                    )
                    continue
                # Create execution plan using call-scoped planner (Fix 1)
                execution_plan = call_planner.create_execution_plan(context_for_adjudication, line_item)
                self.plans_created += 1

                # Execute the plan asynchronously
                tasks.append(self._execute_plan_async(execution_plan, line_item, context_for_adjudication, state))


            if tasks:
                line_item_decisions.extend(list(await asyncio.gather(*tasks)))
            
            for decision in line_item_decisions:
                if hasattr(decision, "decision_trace") and decision.decision_trace:
                    decision_traces.extend(decision.decision_trace)
            
            # Gate 7: State Update and Persistence (BUG FIX #10)
            self._gate_7_state_update(context_for_adjudication, state, line_item_decisions)
            # Merge Gate 7 cross-cutting traces into the claim-level decision_traces.
            gate_7_traces = getattr(_STEP_LOCAL, 'decision_traces', [])
            if gate_7_traces:
                decision_traces.extend(gate_7_traces)
            # Compose final claim-level decision
            claim_decision = self._compose_claim_decision(
                context_for_adjudication, line_item_decisions, state, start_time, decision_traces
            )
            try:
                from pipeline_modules.graph_exporter import save_adjudication_graph
                save_adjudication_graph(claim_decision, context)
            except Exception as e:
                _logger.warning("Failed to save adjudication debug graph: %s", e)
            return claim_decision
        finally:
            if claim_decision:
                try:
                    engine = PipelineMetricsEngine(settings.telemetry_file)
                    engine.record_execution(claim_decision, session)
                except Exception:
                    pass
            telemetry_context.reset(token)

