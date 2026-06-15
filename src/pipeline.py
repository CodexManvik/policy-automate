"""
Gate Execution Pipeline for Claims Auto-Adjudication - Phase 2
Dynamic Graph-based Execution with AI Planner and Semantic Agent
"""
import logging
_logger = logging.getLogger("claims_adjudication_pipeline")
from typing import List, Tuple, Optional, Any, Dict
from datetime import datetime, timezone, date
import time
import asyncio
import threading
# Thread-local storage for the per-call step counter.
# Replaces self.current_step, which was mutable singleton state and unsafe
# under concurrent requests. Each thread/coroutine sees its own counter.
_STEP_LOCAL: threading.local = threading.local()
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
class ClaimsAdjudicationPipeline:
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
    
    @staticmethod
    def _coerce_to_date(target: Any) -> date:
        """Coerces datetime objects or ISO-formatted text strings into standard datetime.date objects."""
        if isinstance(target, datetime):
            return target.date()
        if isinstance(target, date) and not isinstance(target, datetime): # Note: datetime is a subclass of date
            return target
        if isinstance(target, str):
            # Strip timestamp artifacts if present
            return datetime.strptime(target.split("T")[0], "%Y-%m-%d").date()
        raise ValueError(f"Unable to parse target type to date object payload: {type(target)}")
    def _execute_tool(
        self,
        context: ClaimContext,
        line_item: Optional[Any],
        tool_name: str,
        tool_func: Any,
        **kwargs: Any
    ) -> Any:
        """Helper to invoke a mathematical calculator tool and log its inputs and outputs."""
        t_start = time.perf_counter()
        result = tool_func(**kwargs)
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        
        session = telemetry_context.get()
        if session:
            session.record_tool_latency(tool_name, duration_ms)
        AgentReasoningLogger.log_tool_call(
            claim_id=context.claim_id,
            line_item_id=line_item.line_item_id if line_item else None,
            tool_name=tool_name,
            arguments=kwargs,
            output=result
        )
        return result
    def __init__(
        self,
        use_ai: bool = True,
        confidence_threshold: float = 0.90,
        assisted_review_threshold: float = 0.70,
        medical_review_threshold: float = 0.50,  # Gap 8: MEDICAL_REVIEW tier (Section 9.2)
        llm_provider: str = "mock",
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
        # Resolve provider: if 'default' was passed (legacy), probe port 8080.
        resolved_provider = llm_provider
        if resolved_provider in ("default", ""):
            from urllib.parse import urlparse
            import socket
            try:
                parsed = urlparse(local_llm_url)
                h = parsed.hostname or "127.0.0.1"
                if h == "localhost":
                    h = "127.0.0.1"
                p = parsed.port or 8080
                with socket.create_connection((h, p), timeout=1.0):
                    resolved_provider = "local"
            except Exception:
                resolved_provider = "mock"
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
    def _validate_mutual_exclusivity(
        self, context: ClaimContext, gate: str = "policy_validation"
    ) -> Optional[Tuple[str, str]]:
        """
        Validate mutual exclusivity constraints from ProductMemoryStore dynamically.
        Returns: (constraint_id, reason_msg) if violated, None otherwise
        """
        mx_constraints = self.product_memory.mutual_exclusivity_constraints
        def is_benefit_active(benefit_name: str) -> bool:
            name_lower = benefit_name.lower()
            if "co-payment" in name_lower:
                return bool(context.policy.co_payment_percent and context.policy.co_payment_percent > 0)
            if "deductible" in name_lower:
                return bool(context.policy.annual_aggregate_deductible and context.policy.annual_aggregate_deductible > 0)
            if "borderless for specific" in name_lower or "borderless for specified" in name_lower:
                return bool(context.policy.borderless_specific_illness_opted)
            if "borderless" in name_lower:
                return bool(context.policy.borderless_opted)
            if "tiered network" in name_lower:
                return bool(context.policy.tiered_network_opted)
            if "headsup" in name_lower:
                return bool(context.policy.heads_up_opted)
            
            # Check other policy flags dynamically using attribute naming pattern
            attr_name = benefit_name.split("(")[0].strip().lower().replace(" ", "_").replace("-", "_").replace("+", "_plus")
            if hasattr(context.policy, attr_name):
                return bool(getattr(context.policy, attr_name))
            return False
        violation_detected = None
        for constraint in mx_constraints:
            constraint_id = constraint.get('constraint_id')
            benefits = constraint.get('benefits', [])
            
            active_benefits = [benefit for benefit in benefits if is_benefit_active(benefit)]
            if len(active_benefits) > 1:
                reason = (
                    f"Policy configured with contradictory benefits: {', '.join(active_benefits)} "
                    f"- mutually exclusive benefits matching constraint {constraint_id}"
                )
                violation_detected = {
                    "constraint_id": constraint_id,
                    "active_benefits": active_benefits,
                    "reason": reason
                }
                break
        # Log mutual exclusivity check
        AgentReasoningLogger.log_mutual_exclusivity(
            claim_id=context.claim_id,
            gate=gate,
            constraints=mx_constraints,
            violation=violation_detected
        )
        if violation_detected:
            return (violation_detected["constraint_id"], violation_detected["reason"])
        return None
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
            version_memory = get_product_memory(claim_version)
            if version_memory is not self.product_memory:
                self.product_memory = version_memory
                self.planner.product_memory = version_memory
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
            # Issue 11: Apply all mid-term endorsements that are effective by the
            # earliest admission date across line items (or claim receipt date as fallback).
            # This mutates context in-place before any line item is processed.
            claim_event_date = self._coerce_to_date(
                min(
                    (li.admission_date or li.expense_date for li in context.line_items),
                    default=context.claim_received_at
                )
            )
            self._apply_endorsements(context, claim_event_date)
            # Process each line item through dynamic execution plan
            line_item_decisions: List[LineItemDecision] = []
            for line_item in context.line_items:

                # ---------------------------------------------------------------
                # MANDATORY STRUCTURAL PRE-CHECKS (Gates 1 & 2)
                # These checks are NOT in the product JSON rule blueprints and
                # are therefore never generated by the AIPlanner.  They MUST be
                # enforced explicitly before the dynamic plan runs so that invalid
                # policies and ineligible members are rejected unconditionally.
                # ---------------------------------------------------------------
                g1_passed, g1_trace = self._gate_1_policy_validation(context, line_item)
                if not g1_passed:
                    decision_traces.append(g1_trace)
                    decision = self._create_rejected_decision(line_item, g1_trace.reason, [g1_trace])
                    line_item_decisions.append(decision)
                    continue
                g2_passed, g2_trace = self._gate_2_member_validation(context, line_item)
                if not g2_passed:
                    decision_traces.append(g2_trace)
                    decision = self._create_rejected_decision(line_item, g2_trace.reason, [g2_trace])
                    line_item_decisions.append(decision)
                    continue
                # Phase 2: Create execution plan using AI Planner
                execution_plan = self.planner.create_execution_plan(context, line_item)
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
                decision = self._execute_plan(execution_plan, line_item, context, state)
                line_item_decisions.append(decision)
                
                # Consolidate traces into per-call local list
                if hasattr(decision, "decision_trace") and decision.decision_trace:
                    decision_traces.extend(decision.decision_trace)
            
            # Gate 7: State Update and Persistence (BUG FIX #10)
            self._gate_7_state_update(context, state, line_item_decisions)
            # Merge Gate 7 cross-cutting traces (_STEP_LOCAL.decision_traces) into
            # the claim-level decision_traces so they appear in ClaimDecision.decision_trace.
            # Gate 7 appends traces for: RF state transitions, Booster+ accumulation,
            # Cash-Bag+ wellness conversion, and one-time benefit flag persistence.
            gate_7_traces = getattr(_STEP_LOCAL, 'decision_traces', [])
            if gate_7_traces:
                decision_traces.extend(gate_7_traces)
            # Compose final claim-level decision
            claim_decision = self._compose_claim_decision(
                context, line_item_decisions, state, start_time, decision_traces
            )
            return claim_decision
        finally:
            if claim_decision:
                try:
                    engine = PipelineMetricsEngine()
                    engine.record_execution(claim_decision, session)
                except Exception:
                    pass
            telemetry_context.reset(token)
    
    def _execute_plan(
        self,
        plan: ExecutionPlan,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> LineItemDecision:
        """
        Execute the dynamically generated execution plan
        
        Phase 2: Routes each step to appropriate executor:
        - Deterministic rules → calculation tools
        - Semantic rules → AI agent
        - Hybrid rules → both
        """
        item_traces: List[DecisionTrace] = []
        item_deductions: List[DeductionDetail] = []
        
        claimed_amount = line_item.claimed_amount
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        
        # Track confidence
        step_confidences = []
        
        # Per-call step counter — local variable, not instance state
        current_step = 0
        # Execute each step in the plan
        for step in plan.execution_steps:
            current_step += 1
            
            gate_name = step.gate.value if hasattr(step.gate, "value") else str(step.gate)
            t_start = time.perf_counter()
            
            # Route based on execution type
            if step.execution_type == ExecutionType.DETERMINISTIC.value:
                passed, trace, deduction = self._execute_deterministic_step(
                    step, line_item, context, state
                )
            
            elif step.execution_type == ExecutionType.SEMANTIC.value:
                passed, trace, deduction = self._execute_semantic_step(
                    step, line_item, context, state
                )
                self.semantic_calls += 1
            
            elif step.execution_type == ExecutionType.HYBRID.value:
                passed, trace, deduction = self._execute_hybrid_step(
                    step, line_item, context, state
                )
            
            else:
                # Fallback to deterministic
                passed, trace, deduction = self._execute_deterministic_step(
                    step, line_item, context, state
                )
            
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            session = telemetry_context.get()
            if session:
                session.record_gate_latency(gate_name, duration_ms)
            
            # Add trace
            item_traces.append(trace)
            step_confidences.append((trace.confidence, getattr(step, 'confidence_weight', 1.0)))
            
            # Add deduction if applicable
            if deduction:
                item_deductions.append(deduction)
                admissible_amount -= deduction.amount
                payable_amount -= deduction.amount
            
            # Log the gate evaluation
            AgentReasoningLogger.log_gate_evaluation(
                claim_id=context.claim_id,
                line_item_id=line_item.line_item_id if line_item else None,
                gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                rule_id=step.rule_id,
                inputs=trace.inputs if trace.inputs else {},
                evaluation_status=trace.evaluation,
                reason=trace.reason
            )
            
            # ---------------------------------------------------------------
            # FAIL-SAFE ROUTING: Fail-safe, not fail-open.
            # Distinguish between:
            #   (a) Deterministic hard rejection (EXCLUSION_ACTIVE, policy
            #       lapsed, member not eligible) → REJECTED is appropriate.
            #   (b) Semantic / Hybrid ambiguity (model returns FAILED because
            #       it lacks context to rule definitively) → must NOT produce
            #       a zero-payout auto-approval. Route to ASSISTED_REVIEW.
            #   (c) Confidence below threshold on any step type → PENDING_REVIEW.
            # ---------------------------------------------------------------
            if not passed:
                is_semantic_or_hybrid = step.execution_type in (
                    ExecutionType.SEMANTIC.value, ExecutionType.HYBRID.value
                )
                exclusion_hard_stop = trace.evaluation == "EXCLUSION_ACTIVE"
                is_exclusion_gate = step.gate.value == "exclusion_validation" if hasattr(step.gate, "value") else "exclusion" in str(step.gate)
                if trace.confidence < self.medical_review_threshold:
                    self.manual_review_count += 1
                    AgentReasoningLogger.log_routing(
                        claim_id=context.claim_id,
                        line_item_id=line_item.line_item_id if line_item else None,
                        gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                        rule_id=step.rule_id,
                        confidence=trace.confidence,
                        action="PENDING_REVIEW",
                        reason=f"Very low confidence ({trace.confidence:.2f}) on failed step: {trace.reason}"
                    )
                    return self._create_review_decision(
                        line_item, f"Low confidence: {trace.reason}", item_traces
                    )
                elif exclusion_hard_stop and trace.confidence < 0.80:
                    self.manual_review_count += 1
                    AgentReasoningLogger.log_routing(
                        claim_id=context.claim_id,
                        line_item_id=line_item.line_item_id if line_item else None,
                        gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                        rule_id=step.rule_id,
                        confidence=trace.confidence,
                        action="MEDICAL_REVIEW",
                        reason=f"Low-confidence exclusion ({trace.confidence:.2f}): {trace.reason}"
                    )
                    return self._create_medical_review_decision(
                        line_item, trace.reason, item_traces, confidence=trace.confidence
                    )
                elif is_semantic_or_hybrid and not exclusion_hard_stop:
                    if is_exclusion_gate and trace.confidence < self.assisted_review_threshold:
                        self.manual_review_count += 1
                        AgentReasoningLogger.log_routing(
                            claim_id=context.claim_id,
                            line_item_id=line_item.line_item_id if line_item else None,
                            gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                            rule_id=step.rule_id,
                            confidence=trace.confidence,
                            action="MEDICAL_REVIEW",
                            reason=f"Intermediate confidence ({trace.confidence:.2f}) on exclusion gate: {trace.reason}"
                        )
                        return self._create_medical_review_decision(
                            line_item, trace.reason, item_traces, confidence=trace.confidence
                        )
                    elif trace.confidence < self.confidence_threshold:
                        self.manual_review_count += 1
                        AgentReasoningLogger.log_routing(
                            claim_id=context.claim_id,
                            line_item_id=line_item.line_item_id if line_item else None,
                            gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                            rule_id=step.rule_id,
                            confidence=trace.confidence,
                            action="ASSISTED_REVIEW",
                            reason=f"Semantic ambiguity ({trace.confidence:.2f}): {trace.reason}"
                        )
                        return LineItemDecision(
                            line_item_id=line_item.line_item_id,
                            description=line_item.description,
                            claimed_amount=claimed_amount,
                            admissible_amount=admissible_amount,
                            payable_amount=payable_amount,
                            decision="ASSISTED_REVIEW",
                            deductions=item_deductions,
                            decision_trace=item_traces,
                            confidence_score=trace.confidence,
                            manual_review_required=True,
                            review_reason=f"Semantic rule {step.rule_id} returned ambiguous evaluation: {trace.reason}"
                        )
                    else:
                        return self._create_rejected_decision(
                            line_item, trace.reason, item_traces
                        )
                else:
                    return self._create_rejected_decision(
                        line_item, trace.reason, item_traces
                    )
        
        # Calculate final payable through financial gates
        if payable_amount > 0:
            # Enforce Mutual Exclusivity Constraints before running financials
            mx_violation = self._validate_mutual_exclusivity(context, gate="financial_computation")
            if mx_violation:
                constraint_id, reason = mx_violation
                self.manual_review_count += 1
                
                mx_trace = DecisionTrace(
                    step=_STEP_LOCAL.current_step + 1,
                    rule_id=constraint_id,
                    rule_name="Mutual Exclusivity Validation",
                    gate="financial_computation",
                    inputs={
                        "co_payment_percent": context.policy.co_payment_percent,
                        "annual_aggregate_deductible": context.policy.annual_aggregate_deductible,
                    },
                    evaluation="FAILED",
                    reason=reason,
                    confidence=0.0,
                    source_section="mutual_exclusivity_constraints"
                )
                item_traces.append(mx_trace)
                
                # Log gate evaluation
                AgentReasoningLogger.log_gate_evaluation(
                    claim_id=context.claim_id,
                    line_item_id=line_item.line_item_id if line_item else None,
                    gate="financial_computation",
                    rule_id=constraint_id,
                    inputs=mx_trace.inputs,
                    evaluation_status=mx_trace.evaluation,
                    reason=mx_trace.reason
                )
                
                # Log routing action
                AgentReasoningLogger.log_routing(
                    claim_id=context.claim_id,
                    line_item_id=line_item.line_item_id if line_item else None,
                    gate="financial_computation",
                    rule_id=constraint_id,
                    confidence=0.0,
                    action="PENDING_REVIEW",
                    reason=f"Mutual exclusivity violation on financial computation: {reason}"
                )
                
                return LineItemDecision(
                    line_item_id=line_item.line_item_id,
                    description=line_item.description,
                    claimed_amount=claimed_amount,
                    admissible_amount=0.0,
                    payable_amount=0.0,
                    decision="PENDING_REVIEW",
                    deductions=[],
                    decision_trace=item_traces,
                    confidence_score=0.0,
                    manual_review_required=True,
                    review_reason=f"Mutual Exclusivity Constraint {constraint_id}: {reason}"
                )
            final_admissible, final_payable, fin_deductions, fin_traces = \
                self._execute_financial_gates(context, line_item, payable_amount, state)
            
            item_traces.extend(fin_traces)
            item_deductions.extend(fin_deductions)
            payable_amount = final_payable
            admissible_amount = final_admissible
            
            # Log financial computation gate evaluations
            for ft in fin_traces:
                AgentReasoningLogger.log_gate_evaluation(
                    claim_id=context.claim_id,
                    line_item_id=line_item.line_item_id if line_item else None,
                    gate="financial_computation",
                    rule_id=ft.rule_id,
                    inputs=ft.inputs if ft.inputs else {},
                    evaluation_status=ft.evaluation,
                    reason=ft.reason
                )
        
        # Calculate overall confidence using confidence_weight (Gap 12)
        if step_confidences:
            weighted_sum = 0.0
            weight_total = 0.0
            for entry in step_confidences:
                if isinstance(entry, tuple):
                    conf, w = entry
                else:
                    conf, w = entry, 1.0
                weighted_sum += conf * w
                weight_total += w
            overall_confidence = weighted_sum / weight_total if weight_total > 0 else 1.0
        else:
            overall_confidence = 1.0
        state.overall_confidence = overall_confidence
        # Issue 16 / Gap 8: Four-tier confidence routing (Section 9.2):
        #   >= 0.90   → auto-approve/reject deterministically
        #   0.70-0.90 → ASSISTED_REVIEW: pre-populate, flag for operations review
        #   0.50-0.70 → MEDICAL_REVIEW: clinical review for ambiguous exclusions (Gap 8)
        #   < 0.50    → PENDING_REVIEW: full manual review
        if overall_confidence < self.medical_review_threshold:
            self.manual_review_count += 1
            AgentReasoningLogger.log_routing(
                claim_id=context.claim_id,
                line_item_id=line_item.line_item_id if line_item else None,
                gate="final_adjudication",
                rule_id="OVERALL_CONFIDENCE_PENDING",
                confidence=overall_confidence,
                action="PENDING_REVIEW",
                reason=f"Overall confidence {overall_confidence:.2f} is below medical review threshold {self.medical_review_threshold:.2f}"
            )
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="PENDING_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Very low confidence ({overall_confidence:.2f}) — full manual review required"
            )
        if overall_confidence < self.assisted_review_threshold:
            self.manual_review_count += 1
            AgentReasoningLogger.log_routing(
                claim_id=context.claim_id,
                line_item_id=line_item.line_item_id if line_item else None,
                gate="final_adjudication",
                rule_id="OVERALL_CONFIDENCE_MEDICAL",
                confidence=overall_confidence,
                action="MEDICAL_REVIEW",
                reason=(
                    f"Overall confidence {overall_confidence:.2f} is between medical review "
                    f"({self.medical_review_threshold:.2f}) and assisted review threshold ({self.assisted_review_threshold:.2f})"
                )
            )
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="MEDICAL_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Intermediate confidence ({overall_confidence:.2f}) — clinical review required"
            )
        if overall_confidence < self.confidence_threshold:
            # Assisted mode: decision is pre-populated but flagged for ops review
            self.manual_review_count += 1
            if payable_amount == 0:
                assisted_status = "REJECTED"
            elif payable_amount < claimed_amount:
                assisted_status = "PARTIALLY_APPROVED"
            else:
                assisted_status = "APPROVED"
            
            AgentReasoningLogger.log_routing(
                claim_id=context.claim_id,
                line_item_id=line_item.line_item_id if line_item else None,
                gate="final_adjudication",
                rule_id="OVERALL_CONFIDENCE_ASSISTED",
                confidence=overall_confidence,
                action="ASSISTED_REVIEW",
                reason=f"Overall confidence {overall_confidence:.2f} is between {self.assisted_review_threshold:.2f} and {self.confidence_threshold:.2f}"
            )
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="ASSISTED_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Medium confidence ({overall_confidence:.2f}) — pre-populated for operations review (suggested: {assisted_status})"
            )
        # High confidence (>= 0.90): determine outcome from financials
        if payable_amount == 0:
            decision_status = "REJECTED"
        elif payable_amount < claimed_amount:
            decision_status = "PARTIALLY_APPROVED"
        else:
            decision_status = "APPROVED"
        AgentReasoningLogger.log_routing(
            claim_id=context.claim_id,
            line_item_id=line_item.line_item_id if line_item else None,
            gate="final_adjudication",
            rule_id="OVERALL_CONFIDENCE_AUTO",
            confidence=overall_confidence,
            action=decision_status,
            reason=f"Overall confidence {overall_confidence:.2f} meets auto-adjudication threshold {self.confidence_threshold:.2f}"
        )
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=claimed_amount,
            admissible_amount=admissible_amount,
            payable_amount=payable_amount,
            decision=decision_status,
            deductions=item_deductions,
            decision_trace=item_traces,
            confidence_score=overall_confidence,
            manual_review_required=False,
            review_reason=None
        )
    
    def _process_line_item(
        self, 
        line_item, 
        context: ClaimContext, 
        state: PerClaimState
    ) -> LineItemDecision:
        """Process a single line item through all gates"""
        
        item_traces: List[DecisionTrace] = []
        item_deductions: List[DeductionDetail] = []
        
        claimed_amount = line_item.claimed_amount
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        
        # ================================================================
        # GATE 1: POLICY VALIDATION
        # ================================================================
        passed, trace = self._gate_1_policy_validation(context, line_item)
        item_traces.append(trace)
        
        if not passed:
            return self._create_rejected_decision(
                line_item, "Policy validation failed", item_traces
            )
        
        state.policy_validation_passed = True
        
        # ================================================================
        # GATE 2: MEMBER VALIDATION
        # ================================================================
        passed, trace = self._gate_2_member_validation(context, line_item)
        item_traces.append(trace)
        
        if not passed:
            return self._create_rejected_decision(
                line_item, "Member validation failed", item_traces
            )
        
        state.member_validation_passed = True
        
        # ================================================================
        # GATE 3: COVERAGE VALIDATION
        # ================================================================
        passed, trace = self._gate_3_coverage_validation(context, line_item)
        item_traces.append(trace)
        
        if not passed:
            return self._create_rejected_decision(
                line_item, "Benefit not covered", item_traces
            )
        
        state.coverage_validation_passed = True
        
        # ================================================================
        # GATE 4: WAITING PERIOD VALIDATION
        # ================================================================
        passed, trace = self._gate_4_waiting_period_validation(context, line_item)
        item_traces.append(trace)
        
        if not passed:
            return self._create_rejected_decision(
                line_item, "Waiting period active", item_traces
            )
        
        state.waiting_period_passed = True
        
        # ================================================================
        # GATE 5: EXCLUSION VALIDATION
        # ================================================================
        passed, trace = self._gate_5_exclusion_validation(context, line_item)
        item_traces.append(trace)
        
        if not passed:
            return self._create_rejected_decision(
                line_item, "Exclusion applies", item_traces
            )
        
        state.exclusion_passed = True
        
        # ================================================================
        # GATE 6: FINANCIAL COMPUTATION
        # ================================================================
        admissible_amount, payable_amount, deductions, traces = \
            self._gate_6_financial_computation(
                context, line_item, claimed_amount, state
            )
        
        item_traces.extend(traces)
        item_deductions.extend(deductions)
        
        state.financial_computation_complete = True
        
        # Determine decision status
        if payable_amount == 0:
            decision_status = "REJECTED"
        elif payable_amount < claimed_amount:
            decision_status = "PARTIALLY_APPROVED"
        else:
            decision_status = "APPROVED"
        
        # Create line item decision
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=claimed_amount,
            admissible_amount=admissible_amount,
            payable_amount=payable_amount,
            decision=decision_status,
            deductions=item_deductions,
            decision_trace=item_traces,
            confidence_score=state.overall_confidence,
            manual_review_required=False
        )
    
    def _execute_deterministic_step(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Execute a deterministic rule using calculation tools"""
        
        # Map rule to appropriate gate handler
        if step.gate == RuleGate.POLICY_VALIDATION:
            passed, trace = self._gate_1_policy_validation(context, line_item)
            return passed, trace, None
        
        elif step.gate == RuleGate.MEMBER_VALIDATION:
            passed, trace = self._gate_2_member_validation(context, line_item)
            return passed, trace, None
        
        elif step.gate == RuleGate.COVERAGE_VALIDATION:
            # Gate 3: Benefit bucket coverage, duration, and one-time benefit checks.
            # This MUST run before financial gates so invalid benefits are rejected
            # immediately rather than being silently approved with a zero deduction.
            passed, trace = self._gate_3_coverage_validation(context, line_item)
            return passed, trace, None
        
        elif step.gate == RuleGate.WAITING_PERIOD_VALIDATION:
            return self._execute_waiting_period_check(step, line_item, context, state)
        
        elif step.gate == RuleGate.EXCLUSION_VALIDATION:
            passed, trace = self._gate_5_exclusion_validation(context, line_item, step.rule_id)
            return passed, trace, None
        
        # Default pass for unhandled deterministic rules
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id=step.rule_id,
            rule_name=step.rule_name,
            gate=step.gate.value,
            inputs={},
            evaluation="PASSED",
            reason="Deterministic check passed",
            confidence=1.0
        ), None
    
    def _execute_semantic_step(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Execute a semantic rule using AI agent"""
        # Guard: asyncio.to_thread dispatches to a fresh OS thread that has no
        # _STEP_LOCAL attributes. Initialize defaults for this thread if missing.
        if not hasattr(_STEP_LOCAL, "current_step"):
            _STEP_LOCAL.current_step = 0
        if not hasattr(_STEP_LOCAL, "decision_traces"):
            _STEP_LOCAL.decision_traces = []
        # For exclusion rules, run deterministic keyword checks first (Fix 6)
        if step.gate == RuleGate.EXCLUSION_VALIDATION or (hasattr(step.gate, "value") and step.gate.value == "exclusion_validation"):
            is_excluded, excl_reason = self._check_deterministic_exclusion(step.rule_id, line_item, context)
            if is_excluded:
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id=step.rule_id,
                    rule_name=step.rule_name,
                    gate="exclusion_validation",
                    inputs={"description": line_item.description, "condition": line_item.condition_diagnosed},
                    evaluation="EXCLUSION_ACTIVE",
                    reason=excl_reason,
                    confidence=1.0
                ), None
        if not self.use_ai or not self.semantic_agent:
            # Exclusion-gate semantic rules: the deterministic keyword pre-check above
            # already ran and did NOT find an exclusion.  Under use_ai=False the
            # safe answer is PASSED — we cannot apply an exclusion without evidence.
            # For non-exclusion semantic rules (coverage, etc.) where we have no
            # deterministic fallback, route to manual review.
            if step.gate == RuleGate.EXCLUSION_VALIDATION or (
                hasattr(step.gate, "value") and step.gate.value == "exclusion_validation"
            ):
                return True, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id=step.rule_id,
                    rule_name=step.rule_name,
                    gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                    inputs={"semantic_prompt": step.semantic_prompt},
                    evaluation="PASSED",
                    reason="AI disabled — deterministic keyword check found no exclusion; rule passes",
                    confidence=1.0
                ), None
            # Non-exclusion semantic rule with no deterministic fallback → manual review
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id=step.rule_id,
                rule_name=step.rule_name,
                gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                inputs={"semantic_prompt": step.semantic_prompt},
                evaluation="PENDING_REVIEW",
                reason="AI disabled - semantic rule requires manual review",
                confidence=0.0
            ), None
        
        # Determine rule type for semantic agent
        rule_type = "exclusion"
        if step.gate == RuleGate.COVERAGE_VALIDATION:
            rule_type = "coverage"
        elif step.gate == RuleGate.WAITING_PERIOD_VALIDATION:
            rule_type = "waiting_period"
        
        # Call semantic agent
        result = self.semantic_agent.execute_semantic_rule(
            step.rule_id,
            step.semantic_prompt,
            rule_type
        )
        
        # Create trace
        trace = DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id=step.rule_id,
            rule_name=step.rule_name,
            gate=step.gate.value,
            inputs={"semantic_prompt": step.semantic_prompt[:200]},
            evaluation="PASSED" if result.passed else "FAILED",
            reason=result.reason,
            confidence=result.confidence
        )
        
        return result.passed, trace, None
    
    def _execute_hybrid_step(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Execute hybrid rule (deterministic + semantic)"""
        
        # First try deterministic check
        det_passed, det_trace, det_deduction = self._execute_deterministic_step(
            step, line_item, context, state
        )
        
        # If deterministic check is uncertain, use semantic
        if det_trace.confidence < 0.95:
            sem_passed, sem_trace, _ = self._execute_semantic_step(
                step, line_item, context, state
            )
            
            # Combine results - use semantic if higher confidence
            if sem_trace.confidence > det_trace.confidence:
                return sem_passed, sem_trace, det_deduction
        
        return det_passed, det_trace, det_deduction
    
    def _execute_waiting_period_check(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """
        Execute waiting period check using Tool 1
        Phase 2: Fixed implementation with proper logic
        """
        # Calculate continuous coverage months
        continuous_months = context.history.claim_free_years * 12
        
        # Coerce inputs safely
        policy_start = self._coerce_to_date(context.policy.policy_start_date)
        member_addition = self._coerce_to_date(context.member.date_of_addition) if getattr(context.member, "date_of_addition", None) else None
        inception_date = max(policy_start, member_addition) if member_addition else policy_start
        admission_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        
        specified_diseases = None
        tbl_007 = self.product_memory.tables.get("R3_TBL_007")
        if tbl_007:
            specified_diseases = tbl_007.get("items")
        
        # Call Tool 1
        result = self._execute_tool(
            context, line_item, "calculate_waiting_period", calculate_waiting_period,
            condition=line_item.condition_diagnosed,
            policy_inception_date=inception_date,
            continuous_coverage_months=continuous_months,
            portability_credit_months=context.porting.waiting_period_credit_months,
            accident_flag=line_item.accident_related,
            cancer_flag="cancer" in line_item.condition_diagnosed.lower(),
            claim_date=admission_date,
            ped_declarations=context.member.ped_declarations,
            specified_diseases=specified_diseases
        )
        
        # Create trace
        trace = DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id=step.rule_id,
            rule_name=step.rule_name,
            gate=step.gate.value,
            inputs={
                "condition": line_item.condition_diagnosed,
                "continuous_months": continuous_months,
                "portability_credit": context.porting.waiting_period_credit_months
            },
            evaluation="EXCLUSION_ACTIVE" if result.exclusion_active else "PASSED",
            reason=result.details.get("reason", "Waiting period check complete"),
            confidence=0.95 if not result.exclusion_active else 1.0
        )
        
        return not result.exclusion_active, trace, None
    
    def _execute_financial_gates(
        self,
        context: ClaimContext,
        line_item,
        payable_amount: float,
        state: PerClaimState
    ) -> Tuple[float, float, List[DeductionDetail], List[DecisionTrace]]:
        """
        Execute financial computation gates (Phase 1 logic)
        Returns: (admissible_amount, final_payable, deductions, traces)
        """
        t_start = time.perf_counter()
        try:
            # Reuse Phase 1 financial computation logic
            return self._gate_6_financial_computation(
                context, line_item, payable_amount, state
            )
        finally:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            session = telemetry_context.get()
            if session:
                session.record_gate_latency("financial_computation", duration_ms)
    
    def _create_review_decision(
        self,
        line_item,
        reason: str,
        traces: List[DecisionTrace]
    ) -> LineItemDecision:
        """Create a decision that requires manual review"""
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=line_item.claimed_amount,
            admissible_amount=0.0,
            payable_amount=0.0,
            decision="PENDING_REVIEW",
            deductions=[],
            decision_trace=traces,
            confidence_score=0.0,
            manual_review_required=True,
            review_reason=reason
        )
    def _create_medical_review_decision(
        self,
        line_item,
        reason: str,
        traces: List[DecisionTrace],
        confidence: float = 0.65
    ) -> LineItemDecision:
        """Create a decision that requires medical review"""
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=line_item.claimed_amount,
            admissible_amount=0.0,
            payable_amount=0.0,
            decision="MEDICAL_REVIEW",
            deductions=[],
            decision_trace=traces,
            confidence_score=confidence,
            manual_review_required=True,
            review_reason=reason
        )
    
    # Keep existing gate methods from Phase 1
    def _gate_1_policy_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 1: Policy Validation
        Checks: Policy active? Premium paid? Not lapsed? Not void?
        """
        _STEP_LOCAL.current_step += 1
        
        # Check fraud flags (Fix 1)
        policy_fraud = getattr(context.policy, "fraud_flagged", False)
        claim_fraud = getattr(context, "fraud_flagged", False)
        if policy_fraud or claim_fraud:
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_1_FRAUD_FLAGGED",
                rule_name="Fraud Status Check",
                gate="policy_validation",
                inputs={"policy_fraud_flagged": policy_fraud, "claim_fraud_flagged": claim_fraud},
                evaluation="FAILED",
                reason="Policy or claim is flagged as fraudulent / blocked",
                source_section="6.1.1"
            )
        
        # Check policy status
        if context.policy.status != "Active":
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_1_POLICY_STATUS",
                rule_name="Policy Status Check",
                gate="policy_validation",
                inputs={"status": context.policy.status},
                evaluation="FAILED",
                reason=f"Policy status is {context.policy.status}, not Active",
                source_section="6.1.3"
            )
        
        # Check premium payment
        if not context.policy.premium_paid and not context.policy.grace_period_active:
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_1_PREMIUM_PAYMENT",
                rule_name="Premium Payment Check",
                gate="policy_validation",
                inputs={"premium_paid": False, "grace_period": False},
                evaluation="FAILED",
                reason="Premium not paid and grace period expired",
                source_section="6.1.17"
            )
        
        # Check claim date falls within policy period (Fix 1)
        policy_start = self._coerce_to_date(context.policy.policy_start_date)
        policy_end = self._coerce_to_date(context.policy.policy_end_date)
        claim_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        
        if not (policy_start <= claim_date <= policy_end):
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_1_DATE_RANGE",
                rule_name="Policy Date Range Check",
                gate="policy_validation",
                inputs={
                    "policy_start": policy_start.isoformat(),
                    "policy_end": policy_end.isoformat(),
                    "claim_date": claim_date.isoformat()
                },
                evaluation="FAILED",
                reason=f"Claim date {claim_date} falls outside the policy coverage period ({policy_start} to {policy_end})",
                source_section="6.1.18"
            )
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_1_PASSED",
            rule_name="Policy Validation",
            gate="policy_validation",
            inputs={"status": context.policy.status, "premium_paid": context.policy.premium_paid},
            evaluation="PASSED",
            reason="Policy is active and premium paid"
        )
    
    def _gate_2_member_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 2: Member Validation
        Checks: Member exists? Eligible? Age valid?
        """
        _STEP_LOCAL.current_step += 1
        
        # Check member eligibility (including mid-term MemberDeletion endorsement check)
        if not context.member.eligibility_active:
            claim_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
            is_deleted = any(
                e.endorsement_type == "MemberDeletion" and
                e.details.get("member_id") == context.member.member_id and
                self._coerce_to_date(e.effective_date) <= claim_date
                for e in context.endorsements
            )
            reason = "Member deleted via policy endorsement" if is_deleted else "Member not eligible for coverage"
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_2_MEMBER_ELIGIBILITY",
                rule_name="Member Eligibility Check",
                gate="member_validation",
                inputs={"member_id": context.member.member_id, "eligible": False, "deleted_by_endorsement": is_deleted},
                evaluation="FAILED",
                reason=reason
            )
        
        # Check age range eligibility (Fix 2)
        age = context.member.age
        entry_age = context.member.entry_age
        relationship = context.member.relationship
        age_eligible = True
        age_reason = ""
        
        if age < 0 or age > 120:
            age_eligible = False
            age_reason = f"Current age {age} is outside coverable range (0 to 120 years)"
        elif relationship == "Child":
            if entry_age < 0 or entry_age > 25:
                age_eligible = False
                age_reason = f"Entry age {entry_age} for Child is outside eligible range (0 to 25 years)"
        elif relationship in ("Self", "Spouse"):
            if entry_age < 18 or entry_age > 65:
                age_eligible = False
                age_reason = f"Entry age {entry_age} for {relationship} is outside eligible range (18 to 65 years)"
        elif relationship in ("Parent", "Parent-in-law"):
            if entry_age < 35 or entry_age > 75:
                age_eligible = False
                age_reason = f"Entry age {entry_age} for {relationship} is outside eligible range (35 to 75 years)"
        if not age_eligible:
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_2_AGE_ELIGIBILITY",
                rule_name="Member Age Eligibility Check",
                gate="member_validation",
                inputs={"age": age, "entry_age": entry_age, "relationship": relationship},
                evaluation="FAILED",
                reason=age_reason,
                source_section="6.1"
            )
        
        # Check member addition date (Fix 2)
        if hasattr(context.member, "date_of_addition") and context.member.date_of_addition:
            addition_date = self._coerce_to_date(context.member.date_of_addition)
            claim_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
            if claim_date < addition_date:
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="GATE_2_ADDITION_DATE",
                    rule_name="Member Addition Date Check",
                    gate="member_validation",
                    inputs={
                        "member_id": context.member.member_id,
                        "addition_date": addition_date.isoformat(),
                        "claim_date": claim_date.isoformat()
                    },
                    evaluation="FAILED",
                    reason=f"Claim date {claim_date} predates member addition date {addition_date}"
                )
        
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_2_PASSED",
            rule_name="Member Validation",
            gate="member_validation",
            inputs={"member_id": context.member.member_id, "age": context.member.age},
            evaluation="PASSED",
            reason="Member is eligible"
        )
    
    def _gate_3_coverage_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 3: Coverage Validation
        Checks: Benefit bucket covered? Variant supports? Optional benefit opted?
        
        BUG FIX #5: Dynamic coverage validation instead of hardcoded benefit list.
        Respects variant applicability, optional benefit opt-in, and benefit preconditions.
        Gap 7: One-time benefit enforcement — convalescence and critical illness are
        lifetime-once benefits. Reject a second claim if the flag is already set.
        """
        _STEP_LOCAL.current_step += 1
        # -----------------------------------------------------------------------
        # Gap 7: One-time benefit enforcement (lifetime-once flags)
        # These checks must precede all other coverage checks so they are always
        # evaluated regardless of benefit_category (mandatory vs optional).
        # -----------------------------------------------------------------------
        _CI_KEYWORDS = (
            "cancer", "heart attack", "myocardial infarction", "stroke",
            "kidney failure", "renal failure", "organ transplant", "multiple sclerosis",
            "paralysis", "coma", "coronary artery", "major organ"
        )
        description_lower = (line_item.description or "").lower()
        condition_lower = line_item.condition_diagnosed.lower()
        # Convalescence benefit (once per lifetime, Section 7.3)
        if "convalescence" in description_lower:
            if context.lifetime_state.convalescence_claimed:
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_CONVALESCENCE_ONCE",
                    rule_name="Convalescence Benefit One-Time Limit",
                    gate="coverage_validation",
                    inputs={
                        "description": line_item.description,
                        "convalescence_claimed": context.lifetime_state.convalescence_claimed
                    },
                    evaluation="FAILED",
                    reason="Convalescence benefit is a once-per-lifetime benefit; already claimed on this policy.",
                    source_section="7.3"
                )
        # Critical Illness benefit (once per lifetime, Section 7.5)
        # IMPORTANT: This gate guards the CI lump-sum BENEFIT PAYOUT only.
        # A patient who already collected the CI benefit can still file subsequent
        # hospitalization claims for treatment of the same or a different condition.
        # The block fires only when the line item is explicitly a CI benefit claim:
        #   (a) the benefit_bucket is a CI-specific payout bucket, OR
        #   (b) the description explicitly names the CI benefit payout.
        _CI_BENEFIT_BUCKETS = {"critical illness", "critical illness benefit", "ci benefit"}
        is_ci_bucket = line_item.benefit_bucket.lower().strip() in _CI_BENEFIT_BUCKETS
        _CI_BENEFIT_DESC_KEYWORDS = ("critical illness benefit", "ci benefit", "ci lump sum", "ci payout")
        is_ci_benefit_claim = (
            is_ci_bucket
            or any(kw in description_lower for kw in _CI_BENEFIT_DESC_KEYWORDS)
        )
        if is_ci_benefit_claim and context.lifetime_state.critical_illness_claimed:
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_CI_ONCE",
                rule_name="Critical Illness Benefit One-Time Limit",
                gate="coverage_validation",
                inputs={
                    "condition": line_item.condition_diagnosed,
                    "benefit_bucket": line_item.benefit_bucket,
                    "critical_illness_claimed": context.lifetime_state.critical_illness_claimed,
                    "prior_ci_type": context.lifetime_state.critical_illness_type
                },
                evaluation="FAILED",
                reason=(
                    f"Critical Illness benefit is a once-per-lifetime benefit; "
                    f"already claimed for '{context.lifetime_state.critical_illness_type}'."
                ),
                source_section="7.5"
            )
        
        # Get applicable rules for this benefit bucket
        applicable_rules = self.product_memory.filter_rules(
            gate=RuleGate.COVERAGE_VALIDATION,
            variant=context.policy.variant,
            benefit_bucket=line_item.benefit_bucket
        )
        
        # Check if any mandatory benefit rule applies to this bucket
        mandatory_benefit_rules = [
            r for r in applicable_rules 
            if r.benefit_category == "mandatory"
        ]
        
        if not mandatory_benefit_rules:
            # Benefit bucket is not covered by any mandatory rule in this variant
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="GATE_3_BENEFIT_NOT_COVERED",
                rule_name="Benefit Coverage Check",
                gate="coverage_validation",
                inputs={"benefit_bucket": line_item.benefit_bucket, "variant": context.policy.variant},
                evaluation="FAILED",
                reason=f"Benefit '{line_item.benefit_bucket}' is not covered under {context.policy.variant} variant"
            )
        
        # Check optional benefits (if applicable)
        optional_benefit_rules = [
            r for r in applicable_rules 
            if r.benefit_category == "optional"
        ]
        
        for rule in optional_benefit_rules:
            rule_id_lower = rule.rule_id.lower()
            # Check if optional benefit is opted
            if rule_id_lower == "r3_ben_019":  # Borderless for Specified Illness
                if not context.policy.borderless_specific_illness_opted:
                    _STEP_LOCAL.decision_traces.append(DecisionTrace(
                        step=_STEP_LOCAL.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="NOT_APPLICABLE",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    ))
                    continue
            elif rule_id_lower == "r3_ben_018":  # Borderless
                if not context.policy.borderless_opted:
                    _STEP_LOCAL.decision_traces.append(DecisionTrace(
                        step=_STEP_LOCAL.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="NOT_APPLICABLE",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    ))
                    continue
            elif rule_id_lower == "r3_ben_016":  # HeadsUp
                if not context.policy.heads_up_opted:
                    _STEP_LOCAL.decision_traces.append(DecisionTrace(
                        step=_STEP_LOCAL.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="NOT_APPLICABLE",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    ))
                    continue
            elif rule_id_lower == "r3_ben_017":  # Tiered Network
                if not context.policy.tiered_network_opted:
                    _STEP_LOCAL.decision_traces.append(DecisionTrace(
                        step=_STEP_LOCAL.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="NOT_APPLICABLE",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    ))
                    continue
        
        # Check benefit-specific preconditions (e.g., Home Care requires 3 conditions)
        if line_item.benefit_bucket == "Home Care / Domiciliary Treatment":
            # Per policy: Home Care requires all 3 conditions
            # 1. Doctor-advised
            # 2. Continuous line of treatment
            # 3. Daily monitoring chart
            has_preconditions = all([
                getattr(line_item, "doctor_advised", False),
                getattr(line_item, "continuous_treatment", False),
                getattr(line_item, "daily_monitoring_chart", False)
            ])
            
            if not has_preconditions:
                missing = []
                if not getattr(line_item, "doctor_advised", False):
                    missing.append("doctor-advised")
                if not getattr(line_item, "continuous_treatment", False):
                    missing.append("continuous line of treatment")
                if not getattr(line_item, "daily_monitoring_chart", False):
                    missing.append("daily monitoring chart")
                
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_007_PRECONDITION",
                    rule_name="Home Care Preconditions",
                    gate="coverage_validation",
                    inputs={"preconditions": missing},
                    evaluation="FAILED",
                    reason=f"Home Care requires: {', '.join(missing)}"
                )
        
        # Hospitalization duration and AYUSH validation (Rule R3_BEN_003) (Fix 4, 5)
        if line_item.benefit_bucket == "Expenses during Hospitalization":
            hours = getattr(line_item, "hospitalization_hours", 0.0) or 0.0
            treat_type = getattr(line_item, "treatment_type", "Allopathic") or "Allopathic"
            treat_type_lower = treat_type.lower()
            is_ayush = treat_type_lower in {"ayurveda", "yoga", "unani", "siddha", "homeopathy", "ayush"}
            min_required = 24.0 if is_ayush else 2.0
            if hours < min_required:
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_003_DURATION",
                    rule_name="Hospitalization Minimum Duration Check",
                    gate="coverage_validation",
                    inputs={
                        "hospitalization_hours": hours,
                        "treatment_type": treat_type,
                        "min_required": min_required
                    },
                    evaluation="FAILED",
                    reason=f"Hospitalization duration of {hours} hours is below the minimum required {min_required} hours for {treat_type} treatment.",
                    source_section="4.2"
                )
        # All coverage checks passed
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_3_PASSED",
            rule_name="Coverage Validation",
            gate="coverage_validation",
            inputs={"benefit_bucket": line_item.benefit_bucket, "variant": context.policy.variant},
            evaluation="PASSED",
            reason="Benefit is covered and all preconditions met"
        )
    
    def _gate_4_waiting_period_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 4: Waiting Period Validation
        Uses Tool 1: Waiting Period Calculator
        Gap 4: SI enhancement date is now extracted from endorsements and passed so
                the waiting period applies afresh only to the enhanced delta-SI portion.
        Gap 6: Critical Illness 90-day waiting period check (R3_CI_WP_001).
        """
        _STEP_LOCAL.current_step += 1
        
        # Coerce inputs safely
        policy_start = self._coerce_to_date(context.policy.policy_start_date)
        member_addition = self._coerce_to_date(context.member.date_of_addition) if getattr(context.member, "date_of_addition", None) else None
        inception_date = max(policy_start, member_addition) if member_addition else policy_start
        admission_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        
        specified_diseases = None
        tbl_007 = self.product_memory.tables.get("R3_TBL_007")
        if tbl_007:
            specified_diseases = tbl_007.get("items")
        
        # -----------------------------------------------------------------------
        # Gap 4: Extract SI enhancement date from endorsements.
        # If there's a SIEnhancement endorsement effective on or before the claim
        # admission date, pass it to Tool 1 so the 30-day wait applies only to
        # the enhanced delta portion.
        # -----------------------------------------------------------------------
        si_enhancement_date = None
        for endorsement in context.endorsements:
            if endorsement.endorsement_type == "SIEnhancement":
                eff_date = self._coerce_to_date(endorsement.effective_date)
                if eff_date <= admission_date:
                    si_enhancement_date = endorsement.effective_date
                    break  # Use the most recent prior enhancement
        # -----------------------------------------------------------------------
        # Gap 6: Detect Critical Illness condition keywords for 90-day wait.
        # -----------------------------------------------------------------------
        _CI_KEYWORDS = (
            "cancer", "heart attack", "myocardial infarction", "stroke",
            "kidney failure", "renal failure", "organ transplant", "multiple sclerosis",
            "paralysis", "coma", "coronary artery", "major organ"
        )
        condition_lower = line_item.condition_diagnosed.lower()
        critical_illness_flag = any(kw in condition_lower for kw in _CI_KEYWORDS)
        
        # Call Tool 1
        result = self._execute_tool(
            context, line_item, "calculate_waiting_period", calculate_waiting_period,
            condition=line_item.condition_diagnosed,
            policy_inception_date=inception_date,
            continuous_coverage_months=context.history.claim_free_years * 12,
            portability_credit_months=context.porting.waiting_period_credit_months,
            # R3_EXCL_017: Personal waiting period (insurer-imposed, up to 48 months)
            personal_waiting_period_months=getattr(context.policy, "personal_waiting_period_months", 0) or 0,
            accident_flag=line_item.accident_related,
            cancer_flag="cancer" in condition_lower,
            critical_illness_flag=critical_illness_flag,   # Gap 6
            si_enhancement_date=si_enhancement_date,       # Gap 4
            claim_date=admission_date,
            ped_declarations=context.member.ped_declarations,
            specified_diseases=specified_diseases
        )
        if result.exclusion_active:
            return False, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id=result.rule_applied,
                rule_name="Waiting Period Check",
                gate="waiting_period_validation",
                inputs=result.details,
                evaluation="EXCLUSION_ACTIVE",
                reason=f"Waiting period active: {result.remaining_days} days remaining",
                source_section="5.1.1, 5.1.2, 5.1.3, 5.1 (CI 90-day)"
            )
        
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_4_PASSED",
            rule_name="Waiting Period Validation",
            gate="waiting_period_validation",
            inputs={"condition": line_item.condition_diagnosed, "critical_illness_flag": critical_illness_flag},
            evaluation="PASSED",
            reason="All waiting periods cleared"
        )
    
    def _check_deterministic_exclusion(self, rule_id: str, line_item, context: ClaimContext) -> Tuple[bool, str]:
        """Evaluate deterministic exclusion rule check (Fix 6)"""
        desc = (line_item.description or "").lower()
        cond = line_item.condition_diagnosed.lower()
        combined = f"{desc} {cond}"
        # R3_EXCL_010: Excluded Provider
        if rule_id == "R3_EXCL_010":
            if context.network.provider_type == "Excluded":
                return True, f"Treatment at excluded provider: {context.network.provider_name}"
        # R3_EXCL_021: Unrecognized Physician/Hospital
        elif rule_id == "R3_EXCL_021":
            is_unrecognized = "unrecognized" in context.network.provider_name.lower()
            is_family_member = "relative" in desc or "family member" in desc or "self-treated" in desc
            if is_unrecognized:
                return True, f"Treatment at unrecognized facility/practitioner: {context.network.provider_name}"
            if is_family_member:
                return True, "Treatment by family member is excluded"
        # R3_EXCL_020: Dental Treatment
        elif rule_id == "R3_EXCL_020":
            is_dental = any(term in desc for term in ["dental", "teeth", "tooth", "extraction"])
            if is_dental and not line_item.accident_related:
                return True, "Dental treatment excluded (allowed only for accident-related)"
        # R3_EXCL_004: Investigation & Evaluation
        elif rule_id == "R3_EXCL_004":
            kws = ["diagnostic admission", "diagnostics", "evaluation only", "investigation only", "screening admission"]
            if any(kw in combined for kw in kws):
                return True, "Admission primarily for diagnostics/evaluation only is excluded"
        # R3_EXCL_005: Rest Cure
        elif rule_id == "R3_EXCL_005":
            kws = ["rest cure", "rehabilitation", "respite care", "custodial care", "enforced bed rest"]
            if any(kw in combined for kw in kws):
                return True, "Admission primarily for rest cure/rehabilitation/respite care is excluded"
        # R3_EXCL_006: Obesity/Weight Control
        elif rule_id == "R3_EXCL_006":
            if any(kw in combined for kw in ["obesity", "weight control", "bariatric", "weight loss surgery"]):
                is_exception = "medically necessary obesity" in combined or "bmi 40" in combined
                if not is_exception:
                    return True, "Obesity and weight control treatment is excluded"
        # R3_EXCL_007: Cosmetic/Plastic Surgery
        elif rule_id == "R3_EXCL_007":
            if any(kw in combined for kw in ["cosmetic", "plastic surgery", "aesthetic", "liposuction", "facelift", "rhinoplasty"]):
                is_exception = line_item.accident_related or "burn reconstruction" in combined or "cancer reconstruction" in combined
                if not is_exception:
                    return True, "Cosmetic or plastic surgery is excluded"
        # R3_EXCL_008: Adventure Sports
        elif rule_id == "R3_EXCL_008":
            kws = ["hazardous sport", "adventure sport", "scuba diving", "parajumping", "mountaineering", "motor racing", "rock climbing"]
            if any(kw in combined for kw in kws):
                return True, "Treatment due to participation in hazardous/adventure sports is excluded"
        # R3_EXCL_009: Breach of law
        elif rule_id == "R3_EXCL_009":
            kws = ["breach of law", "criminal intent", "illegal activity", "law breach"]
            if any(kw in combined for kw in kws):
                return True, "Treatment arising from committing or attempting breach of law is excluded"
        # R3_EXCL_011: Alcoholism/Substance Abuse
        elif rule_id == "R3_EXCL_011":
            kws = ["alcoholism", "drug abuse", "substance abuse", "addiction", "alcohol dependence", "drug addiction"]
            if any(kw in combined for kw in kws):
                return True, "Treatment for alcoholism, drug/substance abuse is excluded"
        # R3_EXCL_012: Spas/Hydros
        elif rule_id == "R3_EXCL_012":
            kws = ["health hydro", "nature cure", "spa", "nature clinic", "detoxification center"]
            if any(kw in combined for kw in kws):
                return True, "Treatment at health hydros, nature cure clinics, spas is excluded"
        # R3_EXCL_013: Refractive Error
        elif rule_id == "R3_EXCL_013":
            if any(kw in combined for kw in ["refractive error", "eyesight correction", "lasik", "spectacles"]):
                is_exception = "> 7.5 dioptres" in combined or "high dioptres" in combined
                if not is_exception:
                    return True, "Correction of eyesight/refractive error less than 7.5 dioptres is excluded"
        # R3_EXCL_014: Unproven Treatments
        elif rule_id == "R3_EXCL_014":
            kws = ["unproven treatment", "experimental therapy", "unorthodox treatment", "clinical trial"]
            if any(kw in combined for kw in kws):
                return True, "Unproven or experimental treatments are excluded"
        # R3_EXCL_015: Sterility/Infertility
        elif rule_id == "R3_EXCL_015":
            kws = ["infertility", "sterility", "ivf", "assisted reproduction", "surrogacy", "contraception", "sterilization reversal"]
            if any(kw in combined for kw in kws):
                return True, "Sterility and infertility treatments are excluded"
        # R3_EXCL_016: Maternity
        elif rule_id == "R3_EXCL_016":
            if any(kw in combined for kw in ["maternity", "pregnancy", "childbirth", "caesarean", "c-section", "normal delivery", "miscarriage", "abortion"]):
                is_exception = "ectopic pregnancy" in combined or (line_item.accident_related and "miscarriage" in combined)
                if not is_exception:
                    return True, "Maternity and pregnancy-related expenses are excluded"
        # R3_EXCL_018: Conflict & Disaster
        elif rule_id == "R3_EXCL_018":
            kws = ["war injury", "nuclear radiation", "terrorism injury", "rebellion", "conflict"]
            if any(kw in combined for kw in kws):
                return True, "Treatment for injuries from war, nuclear emissions, or terrorism is excluded"
        # R3_EXCL_019: External Congenital Anomaly
        elif rule_id == "R3_EXCL_019":
            kws = ["external congenital anomaly", "cleft lip", "polydactyly", "congenital defect"]
            if any(kw in combined for kw in kws):
                return True, "Treatment related to external Congenital Anomaly is excluded"
        # R3_EXCL_022: Unreasonable Costs
        elif rule_id == "R3_EXCL_022":
            kws = ["unreasonable cost", "medically unnecessary", "non-medical reason"]
            if any(kw in combined for kw in kws):
                return True, "Costs not Reasonable & Customary or not Medically Necessary are excluded"
        # R3_EXCL_023: Brain Death life maintenance
        elif rule_id == "R3_EXCL_023":
            kws = ["brain death", "vegetative state", "artificial life maintenance"]
            if any(kw in combined for kw in kws):
                return True, "Artificial life maintenance for brain dead or vegetative state is excluded"
        return False, ""
    def _gate_5_exclusion_validation(self, context: ClaimContext, line_item, target_rule_id: Optional[str] = None) -> Tuple[bool, DecisionTrace]:
        """
        Gate 5: Exclusion Validation
        Checks all 23 exclusion rules dynamically from Product Memory.
        BUG FIX #6: Evaluate all exclusion rules, not just 2.
        Executes deterministic and semantic rules as specified.
        """
        _STEP_LOCAL.current_step += 1
        
        # Get all exclusion rules from Product Memory
        exclusion_rules = self.product_memory.filter_rules(
            gate=RuleGate.EXCLUSION_VALIDATION
        )
        if target_rule_id:
            exclusion_rules = [r for r in exclusion_rules if r.rule_id == target_rule_id]
        
        # Execute each exclusion rule (deterministic and semantic)
        for rule in exclusion_rules:
            # 1. Run deterministic checks first (Fix 6)
            is_excluded, excl_reason = self._check_deterministic_exclusion(rule.rule_id, line_item, context)
            if is_excluded:
                return False, DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id=rule.rule_id,
                    rule_name=rule.rule_name,
                    gate="exclusion_validation",
                    inputs={"description": line_item.description, "condition": line_item.condition_diagnosed},
                    evaluation="EXCLUSION_ACTIVE",
                    reason=excl_reason,
                    source_section=rule.section_ref
                )
            # Skip custom check blocks for 010, 020, 021 as they are handled above by _check_deterministic_exclusion
            if rule.rule_id in ["R3_EXCL_010", "R3_EXCL_020", "R3_EXCL_021"]:
                continue
            # Semantic/Hybrid exclusions — delegate to semantic agent if available.
            # Fix 1: correct call signature; was passing wrong kwargs (rule=, context=, line_item=)
            elif rule.execution_type in (ExecutionType.SEMANTIC, ExecutionType.HYBRID):
                if self.semantic_agent:
                    try:
                        prompt = rule.semantic_prompt_template
                        if prompt:
                            replacements = {
                                "{condition}": line_item.condition_diagnosed,
                                "{treatment_description}": line_item.description,
                                "{diagnosis}": line_item.condition_diagnosed,
                                "{hospitalization_hours}": str(line_item.hospitalization_hours or 0),
                                "{treatment_type}": line_item.treatment_type,
                                "{admission_reason}": line_item.description,
                                "{procedures}": line_item.description,
                                "{icd_codes}": "[]",
                                "{doctor_notes}": "",
                                "{discharge_summary}": "",
                                "{medical_history}": str(context.member.ped_declarations),
                            }
                            for placeholder, value in replacements.items():
                                prompt = prompt.replace(placeholder, value)
                        else:
                            prompt = (
                                f"Verify exclusion rule {rule.rule_name} ({rule.rule_id}) "
                                f"for condition '{line_item.condition_diagnosed}' "
                                f"and treatment '{line_item.description}'."
                            )
                        sem_result = self.semantic_agent.execute_semantic_rule(
                            rule_id=rule.rule_id,
                            prompt=prompt,
                            rule_type="exclusion"
                        )
                        if sem_result.confidence >= self.confidence_threshold and not sem_result.passed:
                            return False, DecisionTrace(
                                step=_STEP_LOCAL.current_step,
                                rule_id=rule.rule_id,
                                rule_name=rule.rule_name,
                                gate="exclusion_validation",
                                inputs={"prompt_preview": prompt[:150]},
                                evaluation="EXCLUSION_ACTIVE",
                                reason=sem_result.reason,
                                confidence=sem_result.confidence,
                                source_section=rule.section_ref
                            )
                        elif sem_result.confidence < self.confidence_threshold:
                            # Low confidence — flag for review but do not hard-reject
                            _STEP_LOCAL.decision_traces.append(DecisionTrace(
                                step=_STEP_LOCAL.current_step,
                                rule_id=rule.rule_id,
                                rule_name=rule.rule_name,
                                gate="exclusion_validation",
                                inputs={"prompt_preview": prompt[:150]},
                                evaluation="ASSISTED_REVIEW",
                                reason=(
                                    f"Low confidence ({sem_result.confidence:.2f}) on exclusion "
                                    f"{rule.rule_id} — flagged for review"
                                ),
                                confidence=sem_result.confidence,
                                source_section=rule.section_ref
                            ))
                    except Exception as e:
                        # Never let a semantic error crash adjudication — log and continue
                        _STEP_LOCAL.decision_traces.append(DecisionTrace(
                            step=_STEP_LOCAL.current_step,
                            rule_id=rule.rule_id,
                            rule_name=rule.rule_name,
                            gate="exclusion_validation",
                            inputs={},
                            evaluation="NOT_APPLICABLE",
                            reason=f"Semantic agent error for {rule.rule_id}: {e}",
                            confidence=0.0,
                            source_section=rule.section_ref
                        ))
        
        if target_rule_id:
            # All checks for this target rule passed
            rule = next((r for r in exclusion_rules if r.rule_id == target_rule_id), None)
            rule_name = rule.rule_name if rule else "Exclusion Rule"
            sec_ref = rule.section_ref if rule else ""
            return True, DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id=target_rule_id,
                rule_name=rule_name,
                gate="exclusion_validation",
                inputs={"description": line_item.description, "condition": line_item.condition_diagnosed},
                evaluation="PASSED",
                reason=f"Exclusion rule {target_rule_id} check passed",
                source_section=sec_ref,
                confidence=1.0
            )
        # All exclusion rules passed
        return True, DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_5_PASSED",
            rule_name="Exclusion Validation",
            gate="exclusion_validation",
            inputs={"rules_checked": len(exclusion_rules)},
            evaluation="PASSED",
            reason=f"All {len(exclusion_rules)} exclusion rules evaluated - no exclusions triggered"
        )
    
    def _get_eligible_room_rent(
        self,
        context: "ClaimContext",
        line_item
    ) -> Optional[float]:
        """
        Return the INR room rent ceiling for this policy from PolicyData.
        Source of truth: context.policy.room_rent_limit (populated by Policy API
        or UI configuration).
        
        DYNAMIC PARAMETER FALLBACKS:
        If context.policy.room_rent_limit is missing or evaluates to None,
        inject a default fallback value based on variant: 4000.0 if "Select", else 3000.0.
        """
        limit = getattr(context.policy, "room_rent_limit", None)
        if limit is None:
            variant = getattr(context.policy, "variant", None)
            limit = 4000.0 if variant == "Select" else 3000.0
        return limit
    
    def _calculate_associated_medical_expenses(self, line_item, claimed_amount: float) -> Dict[str, float]:
        """
        Calculate Associated Medical Expenses from actual line item components.
        BUG FIX #2: Uses actual line item breakdown, not 30% of claimed amount.
        """
        components = {
            "room_charges": getattr(line_item, "room_charges", 0.0) or 0.0,
            "nursing_charges": getattr(line_item, "nursing_charges", 0.0) or 0.0,
            "medical_practitioner_fees": getattr(line_item, "medical_practitioner_fees", 0.0) or 0.0,
            "ot_charges": getattr(line_item, "ot_charges", 0.0) or 0.0,
        }
        
        if components["room_charges"] == 0.0 and line_item.actual_room_rent:
            components["room_charges"] = line_item.actual_room_rent
        return components
    # ================================================================
    # ISSUE 11: ENDORSEMENT PROCESSOR
    # ================================================================
    def _apply_endorsements(
        self,
        context: "ClaimContext",
        claim_event_date: "date"
    ) -> None:
        """
        Issue 11: Apply all mid-term endorsements whose effective_date <=
        claim_event_date to the ClaimContext in-place.
        Processing order: ascending effective_date (Section 8.2).
        Only endorsements effective on or before the claim admission date
        are applied; future endorsements are ignored.
        Mutates context.policy and context.member directly so all downstream
        gates operate on the post-endorsement policy state.
        """
        if not context.endorsements:
            return
        # Sort endorsements chronologically
        sorted_endorsements = sorted(
            context.endorsements,
            key=lambda e: self._coerce_to_date(e.effective_date)
        )
        for endorsement in sorted_endorsements:
            eff_date = self._coerce_to_date(endorsement.effective_date)
            if eff_date > claim_event_date:
                # Fix 11: use continue, not break — endorsements may not be sorted
                # in strict chronological order; skipping one future-dated entry
                # must not prevent earlier entries from being processed.
                continue
            etype = endorsement.endorsement_type
            details = endorsement.details or {}
            # Log endorsement mutation
            AgentReasoningLogger.log_endorsement(
                claim_id=context.claim_id,
                endorsement_type=etype,
                effective_date=endorsement.effective_date,
                mutations=details
            )
            if etype == "SIEnhancement":
                # Fresh waiting period applies to the *enhanced portion* only.
                # Store the enhancement date in policy so the waiting period
                # calculator can split original vs. enhanced amount.
                # The enhanced SI itself updates base_sum_insured.
                new_si = details.get("new_sum_insured")
                if new_si is not None:
                    context.policy.base_sum_insured = float(new_si)
                # Persist the endorsement effective date so Tool 1 can reset
                # waiting periods correctly on the delta amount.
                context.policy.__dict__["si_enhancement_date"] = endorsement.effective_date
            elif etype == "SIReduction":
                new_si = details.get("new_sum_insured")
                if new_si is not None:
                    context.policy.base_sum_insured = float(new_si)
            elif etype == "MemberAddition":
                # Newly added members get fresh waiting periods.
                # We detect this by comparing the member's date_of_addition
                # to the endorsement effective_date. If they match, override
                # continuous_coverage_months to 0 for that member.
                added_member_id = details.get("member_id")
                if (added_member_id and
                        context.member.member_id == added_member_id):
                    # Reset continuous coverage — fresh waiting periods apply
                    context.member.date_of_addition = endorsement.effective_date
                    context.history.claim_free_years = 0
                    context.history.total_utilized_si = 0.0
            elif etype == "PlanUpgrade":
                # Variant change affects room entitlement and modern treatment
                # sub-limit eligibility (R3_BEN_005A).
                new_variant = details.get("new_variant")
                if new_variant and new_variant in ("Classic", "Select", "Elite"):
                    context.policy.variant = new_variant
                new_room_cat = details.get("room_category_entitled")
                if new_room_cat:
                    context.policy.room_category_entitled = new_room_cat
                modern_plus = details.get("modern_treatments_plus_opted")
                if modern_plus is not None:
                    context.policy.modern_treatments_plus_opted = bool(modern_plus)
            elif etype == "RiderAddition":
                # Map rider names to policy flag fields where possible
                rider_name = (details.get("rider_name") or "").lower()
                if "borderless" in rider_name and "specific" in rider_name:
                    context.policy.borderless_specific_illness_opted = True
                elif "borderless" in rider_name:
                    context.policy.borderless_opted = True
                elif "unlimited" in rider_name:
                    context.policy.unlimited_si_opted = True
                elif "modern" in rider_name and "plus" in rider_name:
                    context.policy.modern_treatments_plus_opted = True
                elif "air ambulance" in rider_name and "plus" in rider_name:
                    context.policy.air_ambulance_plus_opted = True
            elif etype == "IndividualToFloater":
                # Individual-to-Floater Conversion:
                # Loop through all individual member profiles in details, find the lowest booster_plus balance,
                # and assign it to the floater context booster_plus pools.
                members = details.get("members", [])
                if members:
                    booster_balances = [float(m.get("booster_plus", 0.0)) for m in members]
                    min_booster = min(booster_balances)
                    context.benefit_balance.booster_plus_remaining = round(min_booster, 4)
                    context.lifetime_state.booster_plus_accumulated = round(min_booster, 4)
            elif etype == "FloaterSplit":
                # Floater Split:
                # Divide the accumulated booster_plus balance proportionally across the newly decoupled
                # individual policies based on their new relative Sum Insured ratios.
                current_member_id = context.member.member_id
                accumulated_booster = context.benefit_balance.booster_plus_remaining
                
                ratio = None
                new_policies = details.get("new_policies", [])
                if new_policies:
                    total_si = sum(float(p.get("new_sum_insured", 0.0)) for p in new_policies)
                    member_si = next((float(p.get("new_sum_insured", 0.0)) for p in new_policies if p.get("member_id") == current_member_id), None)
                    if total_si > 0.0 and member_si is not None:
                        ratio = member_si / total_si
                
                if ratio is None:
                    ratios = details.get("new_sum_insured_ratios", {})
                    if current_member_id in ratios:
                        ratio = float(ratios[current_member_id])
                
                if ratio is None:
                    ratio = details.get("ratio")
                    if ratio is not None:
                        ratio = float(ratio)
                        
                if ratio is not None:
                    new_booster = accumulated_booster * ratio
                    context.benefit_balance.booster_plus_remaining = round(new_booster, 4)
                    context.lifetime_state.booster_plus_accumulated = round(new_booster, 4)
            elif etype == "MemberDeletion":
                # MemberDeletion does not change booster but flags member as inactive (Fix 3)
                deleted_member_id = details.get("member_id")
                if (deleted_member_id and
                        context.member.member_id == deleted_member_id):
                    context.member.eligibility_active = False
    # ================================================================
    # ISSUE 12: NON-PAYABLE ITEMS DEDUCTION (ANNEXURE)
    # ================================================================
    # Keyword list for Annexure non-payable consumables and charges.
    # Detection is conservative — only obvious matches are auto-deducted;
    # borderline cases remain for the adjudicator under ASSISTED_REVIEW.
    _NON_PAYABLE_KEYWORDS: List[str] = [
        "registration charge", "registration fee", "admission fee",
        "administrative charge", "administration fee",
        "attendant charge", "bystander charge", "visitor charge",
        "telephone charge", "tv charge", "internet charge",
        "cable charge", "wifi charge",
        "toiletry", "toiletries", "personal comfort",
        "laundry charge",
        "convenience fee", "service charge",
        "surcharge", "handling charge",
        "documentation charge", "file charge",
        "food supplement", "dietary supplement", "vitamins",
        "cosmetic", "beauty product",
        "newspaper", "magazine",
        "alcohol", "tobacco",
    ]
    def _deduct_non_payable_items(
        self,
        line_item,
        amount: float
    ) -> Tuple[float, Optional[DeductionDetail]]:
        """
        Fix 12: Gate 6 Step 0 — remove Annexure non-payable items BEFORE
        room pro-rata and all other deductions.
        Uses word-boundary regex matching to avoid false positives on compound
        medical descriptions like 'Anaesthesia administration charges for
        appendectomy' (which contains 'administration' but is NOT a non-payable
        admin fee — it's a clinical procedure charge).
        Returns: (remaining_amount, DeductionDetail or None)
        """
        import re as _re
        if not line_item.description:
            return amount, None
        desc_lower = line_item.description.lower()
        matched_keyword = None
        for kw in self._NON_PAYABLE_KEYWORDS:
            # Word-boundary anchors: 'charge' won't match 'surcharge';
            # 'fee' won't match 'coffee' or 'fever'.
            # Handle optional plural 's' at the end of keywords ending with a letter.
            pattern_str = _re.escape(kw)
            if kw[-1].isalpha():
                pattern_str += r's?'
            pattern = r'(?<!\w)' + pattern_str + r'(?!\w)'
            if _re.search(pattern, desc_lower):
                matched_keyword = kw
                break
        if matched_keyword is None:
            return amount, None
        deduction = DeductionDetail(
            deduction_type="non_payable_items",
            amount=amount,
            rule_id="R3_EXCL_ANNEXURE",
            reason=f"Non-payable item (Annexure): matched '{matched_keyword}'",
            calculation_details={
                "description": line_item.description,
                "matched_keyword": matched_keyword,
            }
        )
        return 0.0, deduction
    # ================================================================
    # ISSUE 13: MODERN TREATMENT SUB-LIMIT
    # ================================================================
    # 12 whitelisted modern procedures per R3_BEN_005.
    # Sub-limit = 50% of base SI (standard interpretation, pending SME confirmation).
    # Sub-limit is REMOVED for Classic/Select variants when modern_treatments_plus_opted.
    _MODERN_TREATMENT_KEYWORDS: List[str] = [
        "robotic surgery", "robotic",
        "oral chemotherapy",
        "immunotherapy",
        "stem cell therapy", "stem cell",
        "balloon sinuplasty", "sinuplasty",
        "deep brain stimulation",
        "bronchial thermoplasty", "thermoplasty",
        "vaporisation of prostate", "vaporization of prostate",
        "intraoperative neuromonitoring", "ionm",
        "stereotactic radiosurgery", "stereotactic radio surgery", "stereotactic",
        "intra vitreal injection", "intravitreal injection",
        "uterine artery embolisation", "uterine artery embolization",
    ]
    _MODERN_TREATMENT_SUBLIMIT_FRACTION: float = 0.50  # 50% of base SI
    def _apply_modern_treatment_sublimit(
        self,
        context: "ClaimContext",
        line_item,
        amount: float
    ) -> Tuple[float, Optional[DeductionDetail]]:
        """
        Issue 13: Apply Modern Treatment sub-limit (R3_BEN_005 / R3_BEN_005A).
        The sub-limit applies ONLY when:
        - benefit_bucket is "Expenses during Hospitalization"
        - treatment description matches one of the 12 whitelisted procedures
        - modern_treatments_plus_opted is False (R3_BEN_005A removes the cap)
        Sub-limit = 50% of base_sum_insured per policy year.
        Returns: (capped_amount, DeductionDetail or None)
        """
        # Only applies to hospitalization expenses
        if line_item.benefit_bucket != "Expenses during Hospitalization":
            return amount, None
        # Check description for modern treatment keywords
        desc_lower = (line_item.description or "").lower()
        treatment_lower = (line_item.treatment_type or "").lower()
        combined = f"{desc_lower} {treatment_lower}"
        matched_procedure = next(
            (kw for kw in self._MODERN_TREATMENT_KEYWORDS if kw in combined),
            None
        )
        if matched_procedure is None:
            return amount, None
        # R3_BEN_005A: sub-limit is removed if modern_treatments_plus_opted
        if context.policy.modern_treatments_plus_opted:
            return amount, None
        sub_limit = context.policy.base_sum_insured * self._MODERN_TREATMENT_SUBLIMIT_FRACTION
        if amount <= sub_limit:
            # Already within sub-limit — no deduction needed
            return amount, None
        deduction_amount = amount - sub_limit
        deduction = DeductionDetail(
            deduction_type="sublimit",
            amount=deduction_amount,
            rule_id="R3_BEN_005",
            reason=(
                f"Modern Treatment sub-limit applied: 50% of base SI "
                f"(INR {sub_limit:.2f}) for '{matched_procedure}'"
            ),
            calculation_details={
                "matched_procedure": matched_procedure,
                "base_sum_insured": context.policy.base_sum_insured,
                "sublimit_fraction": self._MODERN_TREATMENT_SUBLIMIT_FRACTION,
                "sublimit_amount": sub_limit,
                "claimed_amount": amount,
                "deduction": deduction_amount,
                "modern_treatments_plus_opted": context.policy.modern_treatments_plus_opted
            }
        )
        return sub_limit, deduction
    def _gate_6_financial_computation(
        self,
        context: ClaimContext,
        line_item,
        claimed_amount: float,
        state: PerClaimState
    ) -> Tuple[float, float, List[DeductionDetail], List[DecisionTrace]]:
        """
        Gate 6: Financial Computation
        Execution sequence (Section 6.2 + Issues 12-15 + Gap 5):
        -1.  Pre/Post Hospitalization Window Validation (Tool 8) — Gap 5
               [Returns REJECTED immediately if expense is outside window]
        0.   Non-payable items removed (Annexure) — Issue 12
        0.5  Modern Treatment sub-limit applied — Issue 13
             [Hospital Daily Cash branch: Tool 9, returns here] — Issue 14
             [Personal Accident branch: Tool 10, returns here] — Issue 15
        1.   Room Pro-Rata (Tool 2)
        2.   Prolonged Hospitalization Penalty (if applicable)
        3.   HeadsUp/Tiered Network Penalty (if applicable)
        4.   Annual Aggregate Deductible (Tool 4)
        5.   Co-Payment (Tool 3) — stacks all penalties
        6.   SI Waterfall (Tool 5)
        """
        traces: List[DecisionTrace] = []
        deductions: List[DeductionDetail] = []
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        # ================================================================
        # STEP -1: Pre/Post Hospitalization Window Validation (Gap 5 — Tool 8)
        # Only applies to "Expenses before and after hospitalization" bucket.
        # Must validate that:
        #   - Pre-hosp expenses fall within 60 days before admission
        #   - Post-hosp expenses fall within 180 days after discharge
        # Both require the related hospitalization admission/discharge dates.
        # If dates are missing, route to ASSISTED_REVIEW (unconfigured context).
        # ================================================================
        if line_item.benefit_bucket == "Expenses before and after hospitalization":
            _STEP_LOCAL.current_step += 1
            has_hosp_dates = bool(line_item.admission_date and line_item.discharge_date)
            
            if not has_hosp_dates:
                # Cannot validate without hospitalization reference dates
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_006_MISSING_DATES",
                    rule_name="Pre/Post Hospitalization Window",
                    gate="financial_computation",
                    inputs={
                        "expense_date": line_item.expense_date.isoformat() if line_item.expense_date else None,
                        "admission_date": None,
                        "discharge_date": None
                    },
                    evaluation="ASSISTED_REVIEW",
                    reason=(
                        "Pre/Post hospitalization claim is missing the related hospitalization "
                        "admission/discharge dates. Cannot validate window — routed to assisted review."
                    ),
                    confidence=0.5,
                    source_section="R3_BEN_006"
                ))
                return 0.0, 0.0, deductions, traces
            window_result = self._execute_tool(
                context, line_item, "validate_pre_post_hosp_window",
                validate_pre_post_hosp_window,
                expense_date=line_item.expense_date,
                admission_date=line_item.admission_date,
                discharge_date=line_item.discharge_date,
                pre_hosp_days_limit=60,    # Section 6.4: 60 days pre-hospitalization
                post_hosp_days_limit=180,  # Section 6.4: 180 days post-hospitalization
                expense_condition=line_item.condition_diagnosed,
                hospitalization_condition=line_item.condition_diagnosed
            )
            _STEP_LOCAL.current_step += 1
            if not window_result.eligible:
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_006_WINDOW_BREACH",
                    rule_name="Pre/Post Hospitalization Window",
                    gate="financial_computation",
                    inputs={
                        "expense_date": line_item.expense_date.isoformat(),
                        "admission_date": line_item.admission_date.isoformat(),
                        "discharge_date": line_item.discharge_date.isoformat(),
                        "window_type": window_result.window_type,
                        "days_from_event": window_result.days_from_event,
                        "pre_limit_days": 60,
                        "post_limit_days": 180
                    },
                    evaluation="FAILED",
                    reason=(
                        f"Expense is outside the eligible window: "
                        f"{window_result.days_from_event} days {window_result.window_type} hospitalization "
                        f"(limit: {'60' if window_result.window_type == 'pre' else '180'} days)."
                    ),
                    source_section="R3_BEN_006"
                ))
                return 0.0, 0.0, deductions, traces
            
            # Window validated — log and continue to rest of financial gates
            traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_BEN_006_PASSED",
                rule_name="Pre/Post Hospitalization Window",
                gate="financial_computation",
                inputs={
                    "window_type": window_result.window_type,
                    "days_from_event": window_result.days_from_event
                },
                evaluation="PASSED",
                reason=(
                    f"Expense is within the eligible {window_result.window_type}-hospitalization window "
                    f"({window_result.days_from_event} days from event)."
                ),
                source_section="R3_BEN_006"
            ))
        # ================================================================
        # STEP 0: Non-payable items removal (Issue 12 — Annexure exclusions)
        # Must execute BEFORE everything else — amount reduced to zero for
        # non-payable consumables/charges.
        # ================================================================
        _STEP_LOCAL.current_step += 1
        payable_amount, np_deduction = self._deduct_non_payable_items(
            line_item, payable_amount
        )
        if np_deduction:
            admissible_amount = payable_amount
            deductions.append(np_deduction)
            traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_EXCL_ANNEXURE",
                rule_name="Non-Payable Items Removal",
                gate="financial_computation",
                inputs={"description": line_item.description},
                evaluation="DEDUCTION_APPLIED",
                reason=np_deduction.reason,
                source_section="Annexure"
            ))
            # Non-payable items: nothing more to compute — return immediately
            return admissible_amount, payable_amount, deductions, traces
        # ================================================================
        # STEP 0.5: Modern Treatment sub-limit (Issue 13 — R3_BEN_005)
        # ================================================================
        _STEP_LOCAL.current_step += 1
        payable_amount, mt_deduction = self._apply_modern_treatment_sublimit(
            context, line_item, payable_amount
        )
        if mt_deduction:
            admissible_amount = payable_amount
            deductions.append(mt_deduction)
            traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_BEN_005",
                rule_name="Modern Treatment Sub-Limit",
                gate="financial_computation",
                inputs={
                    "description": line_item.description,
                    "base_sum_insured": context.policy.base_sum_insured,
                    "sublimit_fraction": self._MODERN_TREATMENT_SUBLIMIT_FRACTION,
                },
                evaluation="DEDUCTION_APPLIED",
                reason=mt_deduction.reason,
                source_section="R3_BEN_005"
            ))
        # ================================================================
        # ISSUE 14: Hospital Daily Cash — dedicated branch (R3_BEN_011)
        # Co-pay and deductible exempt; only the daily cash formula applies.
        # ================================================================
        if line_item.benefit_bucket == "Hospital Daily Cash":
            _STEP_LOCAL.current_step += 1
            hosp_hours = line_item.hospitalization_hours or 0.0
            daily_cash_amount = getattr(context.policy, "hospital_daily_cash_amount", 0.0) or 0.0
            if daily_cash_amount <= 0.0:
                # Policy does not have a configured daily cash benefit amount;
                # route to ASSISTED_REVIEW.
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_011",
                    rule_name="Hospital Daily Cash",
                    gate="financial_computation",
                    inputs={"daily_cash_amount": daily_cash_amount},
                    evaluation="NOT_APPLICABLE",
                    reason="hospital_daily_cash_amount not configured in policy; requires manual review",
                    confidence=0.5
                ))
                return 0.0, 0.0, deductions, traces
            dc_result = self._execute_tool(
                context, line_item, "calculate_hospital_daily_cash", calculate_hospital_daily_cash,
                daily_cash_amount=daily_cash_amount,
                hospitalization_hours=hosp_hours,
                hospital_daily_cash_days_used=context.benefit_balance.hospital_cash_days_used
            )
            if dc_result.days_exhausted:
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_011",
                    rule_name="Hospital Daily Cash",
                    gate="financial_computation",
                    inputs={"days_used": context.benefit_balance.hospital_cash_days_used},
                    evaluation="FAILED",
                    reason="30-day annual cap for Hospital Daily Cash already exhausted",
                    source_section="R3_BEN_011"
                ))
                return 0.0, 0.0, deductions, traces
            final_cash_benefit = dc_result.total_cash_benefit
            traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_BEN_011",
                rule_name="Hospital Daily Cash",
                gate="financial_computation",
                inputs={
                    "hospitalization_hours": hosp_hours,
                    "eligible_days": dc_result.eligible_days,
                    "daily_cash_amount": daily_cash_amount,
                    "days_already_used": dc_result.days_already_used
                },
                evaluation="PASSED",
                reason=(
                    f"Hospital Daily Cash: {dc_result.eligible_days} days "
                    f"× INR {daily_cash_amount:.2f} = INR {final_cash_benefit:.2f}"
                ),
                source_section="R3_BEN_011"
            ))
            # Update state so Gate 7 can persist the new days consumed
            context.benefit_balance.hospital_cash_days_used += dc_result.eligible_days
            return final_cash_benefit, final_cash_benefit, deductions, traces
        # ================================================================
        # ISSUE 15: Personal Accident benefit — dedicated branch (R3_PA_001/002)
        # Co-pay and deductible exempt. Benefit is table-based, not SI-waterfall.
        # ================================================================
        if line_item.benefit_bucket == "Personal Accident":
            _STEP_LOCAL.current_step += 1
            pa_si = getattr(context.policy, "pa_sum_insured", 0.0) or context.policy.base_sum_insured
            pa_result = self._execute_tool(
                context, line_item, "calculate_personal_accident_benefit", calculate_personal_accident_benefit,
                pa_sum_insured=pa_si,
                injury_description=line_item.description or line_item.condition_diagnosed,
                accident_related=line_item.accident_related
            )
            if pa_result.pa_benefit_type in ("UNKNOWN", "NOT_APPLICABLE"):
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_PA_001",
                    rule_name="Personal Accident Benefit",
                    gate="financial_computation",
                    inputs={
                        "description": line_item.description,
                        "accident_related": line_item.accident_related
                    },
                    evaluation="PENDING_REVIEW" if pa_result.pa_benefit_type == "UNKNOWN" else "NOT_APPLICABLE",
                    reason=(
                        "PA benefit type could not be determined from injury description — manual review required"
                        if pa_result.pa_benefit_type == "UNKNOWN"
                        else "Claim is not accident-related; PA benefit not applicable"
                    ),
                    confidence=0.5
                ))
                return 0.0, 0.0, deductions, traces
            pa_payout = pa_result.pa_payout_amount
            traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="R3_PA_001",
                rule_name="Personal Accident Benefit",
                gate="financial_computation",
                inputs={
                    "pa_sum_insured": pa_si,
                    "benefit_type": pa_result.pa_benefit_type,
                    "payout_percent": pa_result.pa_payout_percent
                },
                evaluation="PASSED",
                reason=(
                    f"PA benefit ({pa_result.pa_benefit_type}): "
                    f"{pa_result.pa_payout_percent * 100:.0f}% of PA SI "
                    f"= INR {pa_payout:.2f}; co-pay and deductible exempt"
                ),
                source_section="R3_PA_001"
            ))
            return pa_payout, pa_payout, deductions, traces
        # ================================================================
        # STEP 1: Room Pro-Rata (must execute first)
        # ================================================================
        if line_item.actual_room_rent and line_item.actual_room_rent > 0:
            _STEP_LOCAL.current_step += 1
            eligible_room = self._get_eligible_room_rent(context, line_item)
            if eligible_room is None:
                # Fix 3: room_rent_limit not configured — cannot compute pro-rata
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_BEN_004",
                    rule_name="Room Pro-Rata",
                    gate="financial_computation",
                    inputs={"actual_room_rent": line_item.actual_room_rent},
                    evaluation="NOT_APPLICABLE",
                    reason=(
                        "room_rent_limit not configured in PolicyData — "
                        "pro-rata cannot be computed; flagged for ASSISTED_REVIEW"
                    ),
                    confidence=0.5,
                    source_section="6.2.4(d)"
                ))
                # Reduce overall confidence so the line item routes to ASSISTED_REVIEW
                state.overall_confidence = min(
                    state.overall_confidence, 0.5
                )
            else:
                expense_components = self._calculate_associated_medical_expenses(
                    line_item, claimed_amount
                )
                if all(v == 0.0 for v in expense_components.values()):
                    # No itemised breakdown available — skip pro-rata, log notice
                    traces.append(DecisionTrace(
                        step=_STEP_LOCAL.current_step,
                        rule_id="R3_BEN_004",
                        rule_name="Room Pro-Rata",
                        gate="financial_computation",
                        inputs={"note": "breakdown fields absent — using claimed_amount as proxy"},
                        evaluation="NOT_APPLICABLE",
                        reason=(
                            "room_charges / nursing_charges / medical_practitioner_fees / "
                            "ot_charges not provided in LineItemData — pro-rata skipped; "
                            "populate from hospital bill or route to ASSISTED_REVIEW"
                        ),
                        confidence=0.5,
                        source_section="6.2.4(d)"
                    ))
                else:
                    pro_rata_result = self._execute_tool(
                        context, line_item, "calculate_room_pro_rata", calculate_room_pro_rata,
                        eligible_room_rent=eligible_room,
                        actual_room_rent=line_item.actual_room_rent,
                        room_charges=expense_components["room_charges"],
                        nursing_charges=expense_components["nursing_charges"],
                        medical_practitioner_fees=expense_components["medical_practitioner_fees"],
                        ot_charges=expense_components["ot_charges"]
                    )
                    if pro_rata_result.deduction > 0:
                        admissible_amount -= pro_rata_result.deduction
                        payable_amount -= pro_rata_result.deduction
                        state.room_pro_rata_ratio = pro_rata_result.pro_rata_ratio
                        deductions.append(DeductionDetail(
                            deduction_type="room_pro_rata",
                            amount=pro_rata_result.deduction,
                            rule_id="R3_BEN_004",
                            reason=(
                                f"Room category breach: ratio "
                                f"{pro_rata_result.pro_rata_ratio:.4f}"
                            ),
                            calculation_details={
                                "eligible_room": pro_rata_result.eligible_room_rent,
                                "actual_room": pro_rata_result.actual_room_rent,
                                "ratio": pro_rata_result.pro_rata_ratio,
                                "associated_expenses": (
                                    pro_rata_result.associated_medical_expenses
                                )
                            }
                        ))
                        traces.append(DecisionTrace(
                            step=_STEP_LOCAL.current_step,
                            rule_id="R3_BEN_004",
                            rule_name="Room Pro-Rata",
                            gate="financial_computation",
                            inputs={
                                "eligible": eligible_room,
                                "actual": line_item.actual_room_rent,
                                "ratio": pro_rata_result.pro_rata_ratio
                            },
                            evaluation="DEDUCTION_APPLIED",
                            reason=f"Pro-rata deduction: INR {pro_rata_result.deduction:.2f}",
                            source_section="6.2.4(d)"
                        ))
        # ================================================================
        # STEP 2: Prolonged Hospitalization Penalty (if applicable)
        # ================================================================
        if line_item.hospitalization_hours and line_item.hospitalization_hours > 168:  # > 7 days
            state.prolonged_hosp_penalty_triggered = True
        # ================================================================
        # STEP 3: HeadsUp / Tiered Network Penalties
        # ================================================================
        if context.policy.heads_up_opted:
            if not context.network.heads_up_recommended:
                state.heads_up_penalty_triggered = True
        if context.policy.tiered_network_opted:
            if not context.network.tiered_network_member:
                state.tiered_network_penalty_triggered = True
        # ================================================================
        # STEP 4: Annual Aggregate Deductible (before co-payment)
        # ================================================================
        if context.policy.annual_aggregate_deductible and context.policy.annual_aggregate_deductible > 0:
            _STEP_LOCAL.current_step += 1
            deductible_rule = self.product_memory.get_rule("R3_FIN_001")
            deductible_exempt = deductible_rule.not_applicable_to if deductible_rule else None
            deductible_result = self._execute_tool(
                context, line_item, "calculate_deductible", calculate_deductible,
                claim_amount=payable_amount,
                annual_deductible_limit=context.policy.annual_aggregate_deductible,
                deductible_consumed_ytd=context.benefit_balance.deductible_consumed_ytd,
                benefit_bucket=line_item.benefit_bucket,
                exempt_benefits=deductible_exempt
            )
            if deductible_result.deductible_applied > 0:
                payable_amount = deductible_result.payable_amount
                state.deductible_applied_this_claim = deductible_result.deductible_applied
                deductions.append(DeductionDetail(
                    deduction_type="deductible",
                    amount=deductible_result.deductible_applied,
                    rule_id="R3_FIN_001",
                    reason="Annual aggregate deductible applied",
                    calculation_details={
                        "deductible_limit": context.policy.annual_aggregate_deductible,
                        "consumed_ytd": context.benefit_balance.deductible_consumed_ytd,
                        "applied_this_claim": deductible_result.deductible_applied
                    }
                ))
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_FIN_001",
                    rule_name="Annual Aggregate Deductible",
                    gate="financial_computation",
                    inputs={"deductible_limit": context.policy.annual_aggregate_deductible},
                    evaluation="DEDUCTION_APPLIED",
                    reason=f"Deductible: INR {deductible_result.deductible_applied:.2f}",
                    source_section="4.18"
                ))
        # ================================================================
        # STEP 5: Co-Payment (stacks all penalties)
        # ================================================================
        if context.policy.co_payment_percent and context.policy.co_payment_percent > 0:
            _STEP_LOCAL.current_step += 1
            # Fix 6: Room category co-pay lookup via ProductMemory (R3_TBL_005 / Annexure V).
            # Replaces the incomplete partial hardcode that missed several combinations.
            room_copay_percent = 0.0
            if line_item.room_category_claimed:
                room_copay_percent = self.product_memory.get_room_copay_percent(
                    variant=context.policy.variant,
                    room_category_claimed=line_item.room_category_claimed
                )
            # Defensive normalization: divide by 100.0 if percentage is > 1.0 (whole number)
            co_payment_percent = context.policy.co_payment_percent
            if co_payment_percent > 1.0:
                co_payment_percent /= 100.0
            copay_rule = self.product_memory.get_rule("R3_FIN_002")
            copay_exempt = copay_rule.not_applicable_to if copay_rule else None
            copay_result = self._execute_tool(
                context, line_item, "calculate_copayment", calculate_copayment,
                admissible_amount=payable_amount,
                base_copay_percent=co_payment_percent,
                benefit_bucket=line_item.benefit_bucket,
                heads_up_penalty=state.heads_up_penalty_triggered,
                tiered_network_penalty=state.tiered_network_penalty_triggered,
                prolonged_hosp_penalty=state.prolonged_hosp_penalty_triggered,
                room_category_copay_percent=room_copay_percent,
                exempt_benefits=copay_exempt
            )
            if copay_result.copay_amount > 0:
                copay_amt = copay_result.copay_amount
                wallet_bal = getattr(context.benefit_balance, "cash_bag_plus_wallet", 0.0) or 0.0
                used_offset = getattr(state, "cash_bag_copay_offset", 0.0) or 0.0
                available_wallet = max(0.0, wallet_bal - used_offset)
                
                offset = min(copay_amt, available_wallet)
                net_copay = copay_amt - offset
                
                state.cash_bag_copay_offset = used_offset + offset
                # Deduct only the net co-payment from the payable_amount (Fix 7)
                payable_amount = payable_amount - net_copay
                state.copay_percent_total = copay_result.total_copay_percent
                deductions.append(DeductionDetail(
                    deduction_type="co_payment",
                    amount=net_copay,
                    rule_id="R3_FIN_002",
                    reason=f"Co-payment {copay_result.total_copay_percent:.1f}% (stacked) offset by INR {offset:.2f} from Cash-Bag+",
                    calculation_details={
                        **copay_result.copay_breakdown,
                        "cash_bag_offset": offset,
                        "original_copay": copay_amt,
                        "net_copay": net_copay
                    }
                ))
                traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_FIN_002",
                    rule_name="Co-Payment (Stacked)",
                    gate="financial_computation",
                    inputs=copay_result.copay_breakdown,
                    evaluation="DEDUCTION_APPLIED",
                    reason=f"Total co-pay: INR {copay_amt:.2f} offset by INR {offset:.2f} from Cash-Bag+. Net deduction: INR {net_copay:.2f} ({copay_result.total_copay_percent:.1f}%)",
                    source_section="4.19"
                ))
        # ================================================================
        # STEP 6: SI Waterfall Consumption (final step)
        # ================================================================
        _STEP_LOCAL.current_step += 1
        # BUG FIX #4: Use lifetime_state.reassure_forever_triggered exclusively.
        # DO NOT infer from prior_claims_count — a REJECTED claim does not trigger Forever.
        forever_pool_val = context.benefit_balance.reassure_forever_pool
        forever_triggered_val = context.lifetime_state.reassure_forever_triggered
        if not forever_triggered_val:
            forever_pool_val = 0.0
        # Deduct already allocated amounts from previous line items to prevent double-spending
        base_si_rem = max(0.0, context.benefit_balance.base_si_remaining - state.amount_from_base_si)
        booster_rem = max(0.0, context.benefit_balance.booster_plus_remaining - state.amount_from_booster)
        forever_pool_rem = max(0.0, forever_pool_val - state.amount_from_forever)
        # Defensive bounds enforcement: ensure running state and current payable amount are not negative
        state.running_payable_amount = max(0.0, state.running_payable_amount)
        payable_amount = max(0.0, payable_amount)
        si_result = self._execute_tool(
            context, line_item, "calculate_si_waterfall", calculate_si_waterfall,
            payable_amount=payable_amount,
            base_si_remaining=base_si_rem,
            booster_plus_remaining=booster_rem,
            reassure_forever_pool=forever_pool_rem,
            reassure_forever_triggered=forever_triggered_val,
            unlimited_si_opted=context.policy.unlimited_si_opted,
            base_si_original=context.policy.base_sum_insured
        )
        # Accumulate state allocations
        state.running_payable_amount += si_result.total_paid
        state.amount_from_base_si += si_result.amount_from_base_si
        state.amount_from_booster += si_result.amount_from_booster
        state.amount_from_forever += si_result.amount_from_forever
        if si_result.shortfall > 0:
            deductions.append(DeductionDetail(
                deduction_type="si_cap",
                amount=si_result.shortfall,
                rule_id="R3_SUM_001",
                reason=f"Sum Insured exhausted - shortfall INR {si_result.shortfall:.2f}",
                calculation_details={
                    "from_base_si": si_result.amount_from_base_si,
                    "from_booster": si_result.amount_from_booster,
                    "from_forever": si_result.amount_from_forever,
                    "shortfall": si_result.shortfall
                }
            ))
        traces.append(DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="R3_SUM_001",
            rule_name="SI Waterfall Consumption",
            gate="financial_computation",
            inputs={
                "payable_requested": payable_amount,
                "base_si_available": context.benefit_balance.base_si_remaining,
                "booster_available": context.benefit_balance.booster_plus_remaining
            },
            evaluation="DEDUCTION_APPLIED" if si_result.shortfall > 0 else "PASSED",
            reason=(
                f"SI consumed: Base INR {si_result.amount_from_base_si:.2f}, "
                f"Booster INR {si_result.amount_from_booster:.2f}, "
                f"Forever INR {si_result.amount_from_forever:.2f}"
            ),
            source_section="3"
        ))
        final_payable = si_result.total_paid
        return admissible_amount, final_payable, deductions, traces
    
    def _gate_7_state_update(
        self, 
        context: ClaimContext,
        state: PerClaimState,
        line_item_decisions: List[LineItemDecision]
    ) -> None:
        t_start = time.perf_counter()
        try:
            self._gate_7_state_update_internal(context, state, line_item_decisions)
        finally:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            session = telemetry_context.get()
            if session:
                session.record_gate_latency("state_update", duration_ms)
    def _gate_7_state_update_internal(
        self, 
        context: ClaimContext,
        state: PerClaimState,
        line_item_decisions: List[LineItemDecision]
    ) -> None:
        """
        Gate 7: State Update (PERSISTENCE GATE)
        Responsibilities (updated with Gap 1, 2, 3, 7 fixes):
        1. Update SI balances after all line items are processed
        2. ReAssure Forever state machine (NOT_TRIGGERED → TRIGGERED → ACTIVE → LAPSED)
        3. Lock the Clock — floater (any member) vs individual (this member only) [Gap 3]
        4. Update deductible YTD consumed
        5. Cash-Bag+ wellness conversion
        6. Booster+ accumulation at claim-free renewal via Tool 7 [Gap 1]
        7. Persist one-time benefit flags (CI, convalescence) [Gap 7]
        8. Record state update trace
        """
        _STEP_LOCAL.current_step += 1
        # ================================================================
        # Determine claim payment outcome for this adjudication call
        # ================================================================
        total_paid = sum(
            li.payable_amount for li in line_item_decisions
            if li.decision in ("APPROVED", "PARTIALLY_APPROVED")
        )
        trigger_buckets = {
            "Expenses in reaching a Hospital", "Expenses during Hospitalization",
            "Expenses before and after hospitalization", "Home Care / Domiciliary Treatment",
            "Organ Donor", "Borderless", "Borderless for Specified Illness"
        }
        decision_by_id = {dec.line_item_id: dec.decision for dec in line_item_decisions}
        has_trigger_bucket_claim = any(
            decision_by_id.get(li.line_item_id) in ("APPROVED", "PARTIALLY_APPROVED")
            and li.benefit_bucket in trigger_buckets
            for li in context.line_items
        )
        claim_event_date = self._coerce_to_date(
            min(
                (li.admission_date or li.expense_date for li in context.line_items),
                default=context.claim_received_at
            )
        )
        is_renewal_simulation = bool(
            getattr(context, "renewal_event_simulation", False) or
            getattr(context.policy, "renewal_event_simulation", False) or
            (
                context.policy.policy_end_date and
                (self._coerce_to_date(context.policy.policy_end_date) - claim_event_date).days <= 30
            )
        )
        if total_paid > 0:
            # ================================================================
            # 1. UPDATE SI BALANCES (Base, Booster, Forever)
            # ================================================================
            if state.amount_from_base_si > 0:
                context.benefit_balance.base_si_remaining -= state.amount_from_base_si
            if state.amount_from_booster > 0:
                context.benefit_balance.booster_plus_remaining -= state.amount_from_booster
            if state.amount_from_forever > 0:
                context.benefit_balance.reassure_forever_pool -= state.amount_from_forever
        # ================================================================
        # 2. REASSURE FOREVER — FULL 4-STATE MACHINE (Gap 2)
        #    NOT_TRIGGERED → TRIGGERED (first PAID claim in trigger bucket)
        #    TRIGGERED → ACTIVE (claim-free renewal after trigger)
        #    ACTIVE → LAPSED (break in policy continuity > 30 days)
        #    ACTIVE: replenish pool += base_si each renewal (capped at 2x base_si)
        # ================================================================
        rf_state = getattr(context.lifetime_state, "reassure_forever_state", "NOT_TRIGGERED")
        break_in = getattr(context.lifetime_state, "break_in_policy_detected", False)
        if rf_state == "NOT_TRIGGERED" and total_paid > 0 and has_trigger_bucket_claim:
            # First eligible paid claim → transition to TRIGGERED
            context.lifetime_state.reassure_forever_triggered = True
            context.lifetime_state.reassure_forever_triggered_date = datetime.now(timezone.utc)
            context.lifetime_state.reassure_forever_triggered_claim_id = context.claim_id
            context.lifetime_state.reassure_forever_state = "TRIGGERED"
            _STEP_LOCAL.decision_traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="RF_TRIGGERED",
                rule_name="ReAssure Forever — Triggered",
                gate="state_update",
                inputs={"claim_id": context.claim_id, "total_paid": total_paid},
                evaluation="PASSED",
                reason="ReAssure Forever triggered: first eligible paid claim. State: NOT_TRIGGERED → TRIGGERED.",
                source_section="4.6, Section 7"
            ))
        elif rf_state == "TRIGGERED" and is_renewal_simulation and total_paid == 0:
            # Claim-free renewal after being triggered → transition to ACTIVE
            context.lifetime_state.reassure_forever_state = "ACTIVE"
            context.lifetime_state.reassure_forever_last_renewal_date = datetime.now(timezone.utc)
            # Replenish pool at renewal: add base_si, cap at 2x base_si
            pool_cap = context.policy.base_sum_insured * 2.0
            current_pool = context.benefit_balance.reassure_forever_pool
            new_pool = min(current_pool + context.policy.base_sum_insured, pool_cap)
            context.benefit_balance.reassure_forever_pool = round(new_pool, 2)
            _STEP_LOCAL.decision_traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="RF_ACTIVATED",
                rule_name="ReAssure Forever — Activated",
                gate="state_update",
                inputs={
                    "is_renewal": is_renewal_simulation,
                    "prior_pool": current_pool,
                    "new_pool": new_pool,
                    "pool_cap": pool_cap
                },
                evaluation="PASSED",
                reason=(
                    f"ReAssure Forever activated: claim-free renewal. "
                    f"Pool replenished from INR {current_pool:.2f} to INR {new_pool:.2f}. "
                    "State: TRIGGERED → ACTIVE."
                ),
                source_section="4.6, Section 7"
            ))
        elif rf_state == "ACTIVE" and is_renewal_simulation:
            if break_in:
                # Break in policy continuity → LAPSED
                context.lifetime_state.reassure_forever_state = "LAPSED"
                context.lifetime_state.reassure_forever_lapsed_date = datetime.now(timezone.utc)
                _STEP_LOCAL.decision_traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="RF_LAPSED",
                    rule_name="ReAssure Forever — Lapsed",
                    gate="state_update",
                    inputs={"break_in_policy": break_in},
                    evaluation="FAILED",
                    reason=(
                        "ReAssure Forever lapsed: break-in-policy detected. "
                        "Pool is forfeited. State: ACTIVE → LAPSED."
                    ),
                    source_section="4.6, Section 7"
                ))
            elif total_paid == 0:
                # Claim-free renewal with ACTIVE state → replenish pool
                context.lifetime_state.reassure_forever_last_renewal_date = datetime.now(timezone.utc)
                pool_cap = context.policy.base_sum_insured * 2.0
                current_pool = context.benefit_balance.reassure_forever_pool
                new_pool = min(current_pool + context.policy.base_sum_insured, pool_cap)
                context.benefit_balance.reassure_forever_pool = round(new_pool, 2)
                _STEP_LOCAL.decision_traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="RF_REPLENISHED",
                    rule_name="ReAssure Forever — Pool Replenished",
                    gate="state_update",
                    inputs={
                        "prior_pool": current_pool,
                        "replenishment": context.policy.base_sum_insured,
                        "new_pool": new_pool
                    },
                    evaluation="PASSED",
                    reason=(
                        f"ReAssure Forever pool replenished at renewal: "
                        f"INR {current_pool:.2f} + INR {context.policy.base_sum_insured:.2f} = INR {new_pool:.2f} (cap: {pool_cap:.2f})."
                    ),
                    source_section="4.6, Section 7"
                ))
        # ================================================================
        # 3. LOCK THE CLOCK — FLOATER vs INDIVIDUAL DISTINCTION (Gap 3)
        #    Spec Section 7.2: on a floater, ANY member's paid claim unlocks
        #    the entire policy. On individual, only the claiming member.
        # ================================================================
        if not getattr(context.lifetime_state, "lock_the_clock_unlocked_date", None):
            entry_age = context.lifetime_state.lock_the_clock_entry_age or context.member.entry_age or context.member.age
            current_age = context.member.age
            policy_type = context.policy.policy_type or "individual"
            # Gap 3: Floater unlock is triggered by any member's approved claim
            if policy_type == "floater":
                # Any approved trigger-bucket claim unlocks the entire floater policy
                ltc_claim_paid_flag = has_trigger_bucket_claim
                ltc_member_claiming = "ANY_FLOATER_MEMBER"
            else:
                # Individual: only this member's approved claim triggers unlock
                ltc_claim_paid_flag = (context.history.prior_claims_count > 0) or has_trigger_bucket_claim
                ltc_member_claiming = context.member.member_id
            ltc_result = self._execute_tool(
                context, None, "calculate_lock_the_clock", calculate_lock_the_clock,
                entry_age=entry_age,
                current_age=current_age,
                claim_paid_flag=ltc_claim_paid_flag,
                policy_type=policy_type,
                policy_term_years=context.policy.policy_term_years or 1,
                claim_in_year=getattr(context.policy, "claim_in_year", 1) or 1,
                member_claiming=ltc_member_claiming
            )
            state.lock_the_clock_age_unlocked = ltc_result.age_unlocked
            context.lifetime_state.lock_the_clock_age_locked = ltc_result.age_locked
            context.lifetime_state.lock_the_clock_current_premium_age = ltc_result.age_for_premium
            state.lock_the_clock_premium_delta = ltc_result.additional_premium_delta
        if state.lock_the_clock_age_unlocked:
            context.lifetime_state.lock_the_clock_unlocked_date = datetime.now(timezone.utc)
        # ================================================================
        # 4. UPDATE DEDUCTIBLE YTD CONSUMED
        # ================================================================
        if state.deductible_applied_this_claim > 0:
            context.benefit_balance.deductible_consumed_ytd += state.deductible_applied_this_claim
        # ================================================================
        # Deduct Cash-Bag+ offset used for co-payment (Fix 7)
        # ================================================================
        if state.cash_bag_copay_offset > 0:
            context.benefit_balance.cash_bag_plus_wallet = round(
                max(0.0, context.benefit_balance.cash_bag_plus_wallet - state.cash_bag_copay_offset), 4
            )
            if hasattr(context.lifetime_state, "cash_bag_plus"):
                context.lifetime_state.cash_bag_plus.balance = round(
                    max(0.0, context.lifetime_state.cash_bag_plus.balance - state.cash_bag_copay_offset), 4
                )
            _STEP_LOCAL.decision_traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="CASH_BAG_PLUS_COPAY_OFFSET_DEDUCTION",
                rule_name="Cash-Bag+ Co-payment Offset Deduction",
                gate="state_update",
                inputs={"offset_deducted": state.cash_bag_copay_offset},
                evaluation="PASSED",
                reason=f"Deducted INR {state.cash_bag_copay_offset:.2f} from Cash-Bag+ wallet balance to cover co-payment offset"
            ))
        # ================================================================
        # 5. CASH-BAG+ WALLET — WELLNESS CONVERSION (Section 7.4)
        # ================================================================
        if is_renewal_simulation and hasattr(context.lifetime_state, "live_healthy") and context.lifetime_state.live_healthy.current_points > 0:
            current_points = context.lifetime_state.live_healthy.current_points
            wallet_credit = round(float(current_points * 0.25), 4)
            context.lifetime_state.cash_bag_plus.balance = round(
                context.lifetime_state.cash_bag_plus.balance + wallet_credit, 4
            )
            context.lifetime_state.cash_bag_plus.last_credited = datetime.now(timezone.utc)
            context.benefit_balance.cash_bag_plus_wallet = round(
                context.benefit_balance.cash_bag_plus_wallet + wallet_credit, 4
            )
            context.lifetime_state.live_healthy.current_points = 0
            _STEP_LOCAL.decision_traces.append(DecisionTrace(
                step=_STEP_LOCAL.current_step,
                rule_id="CASH_BAG_PLUS_ACCRUAL",
                rule_name="Cash-Bag+ Wellness Conversion",
                gate="state_update",
                inputs={
                    "wellness_points_converted": current_points,
                    "conversion_rate": 0.25,
                    "wallet_credit": wallet_credit
                },
                evaluation="PASSED",
                reason=f"Successfully converted {current_points} wellness points to cash wallet credit: INR {wallet_credit:.4f}",
                source_section="4.9, 4.10"
            ))
        # ================================================================
        # 6. BOOSTER+ ACCUMULATION AT CLAIM-FREE RENEWAL (Gap 1 — Tool 7)
        #    Tool 7 is called ONLY when:
        #    - This is a renewal simulation event
        #    - No claims were paid in this policy year (claim-free)
        #    Variant max multipliers: Classic=2x, Select=5x, Elite=10x (Section 4.6)
        # ================================================================
        _VARIANT_BOOSTER_MULTIPLIERS = {"Classic": 2, "Select": 5, "Elite": 10}
        is_claim_free_renewal = is_renewal_simulation and (total_paid == 0)
        if is_claim_free_renewal:
            booster_result = self._execute_tool(
                context, None, "calculate_booster_accumulation", calculate_booster_accumulation,
                base_si=context.policy.base_sum_insured,
                booster_plus_current=context.benefit_balance.booster_plus_remaining,
                claim_free_year=True,
                variant_max_multiplier=_VARIANT_BOOSTER_MULTIPLIERS.get(context.policy.variant, 2)
            )
            if booster_result.accumulation_applied:
                context.benefit_balance.booster_plus_remaining = booster_result.booster_plus_new
                context.lifetime_state.booster_plus_accumulated = booster_result.booster_plus_new
                context.lifetime_state.booster_plus_last_updated = datetime.now(timezone.utc)
                _STEP_LOCAL.decision_traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_SUM_004_BOOSTER_RENEWAL",
                    rule_name="Booster+ Accumulation at Renewal",
                    gate="state_update",
                    inputs={
                        "base_si": context.policy.base_sum_insured,
                        "prior_booster": booster_result.booster_plus_new - booster_result.growth_amount,
                        "growth_amount": booster_result.growth_amount,
                        "new_booster": booster_result.booster_plus_new,
                        "variant": context.policy.variant
                    },
                    evaluation="PASSED",
                    reason=(
                        f"Booster+ accumulated at claim-free renewal: "
                        f"INR {booster_result.growth_amount:.2f} added. "
                        f"New balance: INR {booster_result.booster_plus_new:.2f}."
                    ),
                    source_section="4.6, R3_SUM_004"
                ))
        # ================================================================
        # 7. ONE-TIME BENEFIT FLAGS — PERSIST AFTER PAID CLAIMS (Gap 7)
        #    Set convalescence_claimed and critical_illness_claimed only if
        #    a paid line item matches these benefit types.
        # ================================================================
        _CI_KEYWORDS = (
            "cancer", "heart attack", "myocardial infarction", "stroke",
            "kidney failure", "renal failure", "organ transplant", "multiple sclerosis",
            "paralysis", "coma", "coronary artery", "major organ"
        )
        for li_dec in line_item_decisions:
            if li_dec.decision not in ("APPROVED", "PARTIALLY_APPROVED"):
                continue
            matching_li = next(
                (li for li in context.line_items if li.line_item_id == li_dec.line_item_id),
                None
            )
            if not matching_li:
                continue
            desc_lower = (matching_li.description or "").lower()
            cond_lower = matching_li.condition_diagnosed.lower()
            # Convalescence: once per lifetime
            if "convalescence" in desc_lower and not context.lifetime_state.convalescence_claimed:
                context.lifetime_state.convalescence_claimed = True
                _STEP_LOCAL.decision_traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_CONVALESCENCE_SET",
                    rule_name="Convalescence Benefit — Flag Set",
                    gate="state_update",
                    inputs={"line_item_id": li_dec.line_item_id, "description": matching_li.description},
                    evaluation="PASSED",
                    reason="convalescence_claimed flag set after first approved convalescence benefit payment.",
                    source_section="7.3"
                ))
            # Critical Illness: once per lifetime
            is_ci_payment = any(kw in cond_lower for kw in _CI_KEYWORDS)
            if is_ci_payment and not context.lifetime_state.critical_illness_claimed:
                context.lifetime_state.critical_illness_claimed = True
                context.lifetime_state.critical_illness_type = matching_li.condition_diagnosed
                _STEP_LOCAL.decision_traces.append(DecisionTrace(
                    step=_STEP_LOCAL.current_step,
                    rule_id="R3_CI_FLAG_SET",
                    rule_name="Critical Illness Benefit — Flag Set",
                    gate="state_update",
                    inputs={
                        "line_item_id": li_dec.line_item_id,
                        "condition": matching_li.condition_diagnosed
                    },
                    evaluation="PASSED",
                    reason=f"critical_illness_claimed flag set for '{matching_li.condition_diagnosed}'.",
                    source_section="7.5"
                ))
        # ================================================================
        # 8. RECORD STATE UPDATE SUMMARY TRACE
        # ================================================================
        trace = DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id="GATE_7_STATE_UPDATE",
            rule_name="State Update & Persistence",
            gate="state_update",
            inputs={
                "base_si_remaining": context.benefit_balance.base_si_remaining,
                "booster_remaining": context.benefit_balance.booster_plus_remaining,
                "forever_pool": context.benefit_balance.reassure_forever_pool,
                "forever_state": context.lifetime_state.reassure_forever_state,
                "deductible_ytd": context.benefit_balance.deductible_consumed_ytd,
                "total_paid": total_paid,
                "is_renewal_simulation": is_renewal_simulation,
                "is_claim_free_renewal": is_claim_free_renewal
            },
            evaluation="PASSED",
            reason=f"State updated after paid claims totaling INR {total_paid:.2f}",
            source_section="State Management"
        )
        _STEP_LOCAL.decision_traces.append(trace)
        AgentReasoningLogger.log_gate_evaluation(
            claim_id=context.claim_id,
            line_item_id=None,
            gate="state_update",
            rule_id="GATE_7_STATE_UPDATE",
            inputs=trace.inputs,
            evaluation_status=trace.evaluation,
            reason=trace.reason
        )
    
    
    
    def _create_rejected_decision(
        self,
        line_item,
        reason: str,
        traces: List[DecisionTrace]
    ) -> LineItemDecision:
        """Create a rejected line item decision"""
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=line_item.claimed_amount,
            admissible_amount=0.0,
            payable_amount=0.0,
            decision="REJECTED",
            deductions=[],
            decision_trace=traces,
            confidence_score=1.0,
            manual_review_required=False,
            review_reason=reason
        )
    def _create_claim_review_decision(
        self,
        context: ClaimContext,
        constraint_id: str,
        reason: str,
        traces: List[DecisionTrace],
        start_time: float,
    ) -> ClaimDecision:
        """Create a claim-level PENDING_REVIEW decision for constraint violation"""
        total_claimed = sum(li.claimed_amount for li in context.line_items)
        processing_duration = (time.time() - start_time) * 1000  # BUG FIX #8: Use actual start_time
        return ClaimDecision(
            claim_id=context.claim_id,
            claim_decision="PENDING_REVIEW",
            total_claimed=total_claimed,
            total_admissible=0.0,
            total_payable=0.0,
            total_deductions=0.0,
            deduction_breakdown=DeductionBreakdown(),
            si_waterfall_breakdown=SIWaterfallBreakdown(
                amount_from_base_si=0.0,
                amount_from_booster=0.0,
                amount_from_forever=0.0,
                total_paid=0.0,
                shortfall=total_claimed,
                updated_base_si=context.benefit_balance.base_si_remaining,
                updated_booster=context.benefit_balance.booster_plus_remaining,
                updated_forever_pool=context.benefit_balance.reassure_forever_pool,
            ),
            line_items=[],
            decision_trace=traces,
            confidence_score=0.0,
            manual_review_required=True,
            review_reasons=[f"Mutual Exclusivity Constraint {constraint_id}: {reason}"],
            pas_submission_payload={},
            processing_duration_ms=processing_duration,
        )
    def _compose_claim_decision(
        self,
        context: ClaimContext,
        line_item_decisions: List[LineItemDecision],
        state: PerClaimState,
        start_time: float,
        decision_traces: Optional[List[DecisionTrace]] = None,
    ) -> ClaimDecision:
        """Compose final claim-level decision from line item decisions"""
        
        # Aggregate financials
        total_claimed = sum(d.claimed_amount for d in line_item_decisions)
        total_admissible = sum(d.admissible_amount for d in line_item_decisions)
        
        # Aggregate deduction breakdown
        deduction_breakdown = DeductionBreakdown()
        for decision in line_item_decisions:
            for deduction in decision.deductions:
                if deduction.deduction_type == "room_pro_rata":
                    deduction_breakdown.room_pro_rata += deduction.amount
                elif deduction.deduction_type == "co_payment":
                    deduction_breakdown.co_payment += deduction.amount
                elif deduction.deduction_type == "deductible":
                    deduction_breakdown.deductible += deduction.amount
                elif deduction.deduction_type == "si_cap":
                    deduction_breakdown.si_cap += deduction.amount
                elif deduction.deduction_type == "non_payable_items":
                    deduction_breakdown.non_payable_items += deduction.amount
                elif deduction.deduction_type == "sublimit":
                    deduction_breakdown.sublimits += deduction.amount
                elif "penalty" in deduction.deduction_type:
                    deduction_breakdown.penalties += deduction.amount
        # Populate lock_the_clock_premium_delta from state
        deduction_breakdown.lock_the_clock_premium_delta = getattr(state, "lock_the_clock_premium_delta", 0.0)
        total_payable = sum(d.payable_amount for d in line_item_decisions)
        total_payable = max(0.0, total_payable - deduction_breakdown.lock_the_clock_premium_delta)
        total_deductions = total_claimed - total_payable
        # Fix 4C: Gate 7 has already subtracted amounts from context.benefit_balance.
        # Read the already-updated values directly — do NOT subtract again.
        si_waterfall = SIWaterfallBreakdown(
            amount_from_base_si=getattr(state, "amount_from_base_si", 0.0),
            amount_from_booster=getattr(state, "amount_from_booster", 0.0),
            amount_from_forever=getattr(state, "amount_from_forever", 0.0),
            total_paid=total_payable,
            shortfall=total_claimed - total_payable,
            updated_base_si=context.benefit_balance.base_si_remaining,
            updated_booster=context.benefit_balance.booster_plus_remaining,
            updated_forever_pool=context.benefit_balance.reassure_forever_pool
        )
        
        # Issue 16/22: Determine overall decision propagating ASSISTED_REVIEW
        # Priority: PENDING_REVIEW > MEDICAL_REVIEW > ASSISTED_REVIEW > financial outcome
        if any(d.decision == "PENDING_REVIEW" for d in line_item_decisions):
            overall_decision = "PENDING_REVIEW"
        elif any(d.decision == "MEDICAL_REVIEW" for d in line_item_decisions):
            overall_decision = "MEDICAL_REVIEW"
        elif any(d.decision == "ASSISTED_REVIEW" for d in line_item_decisions):
            overall_decision = "ASSISTED_REVIEW"
        elif total_payable == 0:
            overall_decision = "REJECTED"
        elif total_payable < total_claimed:
            overall_decision = "PARTIALLY_APPROVED"
        else:
            overall_decision = "APPROVED"
        # Calculate processing duration
        processing_duration = (time.time() - start_time) * 1000  # ms
        # Issue 22: Build structured PAS submission payload (Section 4.7).
        # Previously always returned {}; now contains the full decision record
        # needed by PAS for independent revalidation and concordance checking.
        pas_payload = {
            "claim_id": context.claim_id,
            "policy_id": context.policy.policy_id,
            "member_id": context.member.member_id,
            "product_code": context.policy.product_code,
            "variant": context.policy.variant,
            "product_json_version": getattr(context, "product_json_version", "R3_v1.0"),
            "adjudication_decision": overall_decision,
            "confidence_score": state.overall_confidence,
            "total_claimed": total_claimed,
            "total_admissible": total_admissible,
            "total_payable": total_payable,
            "total_deductions": total_claimed - total_payable,
            "deduction_breakdown": {
                "room_pro_rata": deduction_breakdown.room_pro_rata,
                "co_payment": deduction_breakdown.co_payment,
                "non_payable_items": deduction_breakdown.non_payable_items,
                "deductible": deduction_breakdown.deductible,
                "si_cap": deduction_breakdown.si_cap,
                "sublimits": deduction_breakdown.sublimits,
                "penalties": deduction_breakdown.penalties,
            },
            "si_sourcing": {
                "from_base_si": si_waterfall.amount_from_base_si,
                "from_booster": si_waterfall.amount_from_booster,
                "from_forever": si_waterfall.amount_from_forever,
            },
            "line_item_dispositions": [
                {
                    "line_item_id": d.line_item_id,
                    "description": d.description,
                    "claimed": d.claimed_amount,
                    "admissible": d.admissible_amount,
                    "payable": d.payable_amount,
                    "decision": d.decision,
                }
                for d in line_item_decisions
            ],
            "manual_review_required": state.overall_confidence < self.confidence_threshold,
            "adjudication_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "processing_duration_ms": processing_duration,
            "ai_version": "Claims2.0-v1.0",
        }
        # Build review_reasons: aggregate all non-None review_reason values from line
        # item decisions (PENDING_REVIEW / ASSISTED_REVIEW), plus any FAILED or
        # EXCLUSION_ACTIVE trace entries from the combined decision trace.
        review_reasons: List[str] = []
        for d in line_item_decisions:
            if d.review_reason:
                entry = f"[{d.line_item_id}] {d.review_reason}"
                if entry not in review_reasons:
                    review_reasons.append(entry)
        active_traces: List[DecisionTrace] = decision_traces or []
        for t in active_traces:
            if t.evaluation in ("FAILED", "EXCLUSION_ACTIVE") and t.reason:
                entry = f"[{t.rule_id}] {t.reason}"
                if entry not in review_reasons:
                    review_reasons.append(entry)
        return ClaimDecision(
            claim_id=context.claim_id,
            claim_decision=overall_decision,
            total_claimed=total_claimed,
            total_admissible=total_admissible,
            total_payable=total_payable,
            total_deductions=total_deductions,
            deduction_breakdown=deduction_breakdown,
            si_waterfall_breakdown=si_waterfall,
            line_items=line_item_decisions,
            decision_trace=active_traces,
            confidence_score=state.overall_confidence,
            manual_review_required=state.overall_confidence < self.confidence_threshold,
            review_reasons=review_reasons,
            processing_duration_ms=processing_duration,
            pas_submission_payload=pas_payload,
        )
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
            version_memory = get_product_memory(claim_version)
            if version_memory is not self.product_memory:
                self.product_memory = version_memory
                self.planner.product_memory = version_memory
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

            # Process each line item through dynamic execution plan concurrently.
            # Gate 1 (policy validity) and Gate 2 (member eligibility) are structural
            # pre-conditions not covered by the AIPlanner; they must always run first.
            line_item_decisions: List[LineItemDecision] = []
            tasks = []
            for line_item in context.line_items:
                # Mandatory structural pre-checks before handing off to the async plan.
                g1_passed, g1_trace = self._gate_1_policy_validation(context, line_item)
                if not g1_passed:
                    line_item_decisions.append(
                        self._create_rejected_decision(line_item, g1_trace.reason, [g1_trace])
                    )
                    continue
                g2_passed, g2_trace = self._gate_2_member_validation(context, line_item)
                if not g2_passed:
                    line_item_decisions.append(
                        self._create_rejected_decision(line_item, g2_trace.reason, [g2_trace])
                    )
                    continue
                # Create execution plan using AI Planner
                execution_plan = self.planner.create_execution_plan(context, line_item)
                self.plans_created += 1
                
                # Execute the plan asynchronously
                tasks.append(self._execute_plan_async(execution_plan, line_item, context, state))


            if tasks:
                line_item_decisions.extend(list(await asyncio.gather(*tasks)))
            
            for decision in line_item_decisions:
                if hasattr(decision, "decision_trace") and decision.decision_trace:
                    decision_traces.extend(decision.decision_trace)
            
            # Gate 7: State Update and Persistence (BUG FIX #10)
            self._gate_7_state_update(context, state, line_item_decisions)
            # Merge Gate 7 cross-cutting traces (_STEP_LOCAL.decision_traces) into
            # the claim-level decision_traces so they appear in ClaimDecision.decision_trace.
            # Gate 7 appends traces for: RF state transitions, Booster+ accumulation,
            # Cash-Bag+ wellness conversion, and one-time benefit flag persistence.
            gate_7_traces = getattr(_STEP_LOCAL, 'decision_traces', [])
            if gate_7_traces:
                decision_traces.extend(gate_7_traces)
            # Compose final claim-level decision
            claim_decision = self._compose_claim_decision(
                context, line_item_decisions, state, start_time, decision_traces
            )
            return claim_decision
        finally:
            if claim_decision:
                try:
                    engine = PipelineMetricsEngine()
                    engine.record_execution(claim_decision, session)
                except Exception:
                    pass
            telemetry_context.reset(token)
    async def _execute_plan_async(
        self,
        plan: ExecutionPlan,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> LineItemDecision:
        """
        Execute the dynamically generated execution plan asynchronously.
        Groups execution steps by DAG depth layer to execute independent steps concurrently.
        """
        item_traces: List[DecisionTrace] = []
        item_deductions: List[DeductionDetail] = []
        
        claimed_amount = line_item.claimed_amount
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        
        # Track confidence
        step_confidences = []
        
        # Step 1: Compute execution depth for each step in the plan
        step_by_id = {step.rule_id: step for step in plan.execution_steps}
        step_depths = {}
        
        def get_step_depth(rule_id: str) -> int:
            if rule_id in step_depths:
                return step_depths[rule_id]
            
            step = step_by_id.get(rule_id)
            if not step or not step.depends_on:
                step_depths[rule_id] = 0
                return 0
            
            # Find dependencies that are actually present in the current plan
            plan_deps = [dep for dep in step.depends_on if dep in step_by_id]
            if not plan_deps:
                step_depths[rule_id] = 0
                return 0
                
            dep_depth = 1 + max(get_step_depth(dep) for dep in plan_deps)
            step_depths[rule_id] = dep_depth
            return dep_depth
            
        for step in plan.execution_steps:
            get_step_depth(step.rule_id)
            
        # Step 2: Group steps by depth layers
        from collections import defaultdict
        layers = defaultdict(list)
        for step in plan.execution_steps:
            depth = step_depths[step.rule_id]
            layers[depth].append(step)
            
        sorted_depths = sorted(layers.keys())
        
        # Step 3: Execute layer by layer
        for depth in sorted_depths:
            layer_steps = layers[depth]
            
            # Execute all steps in the current layer concurrently
            tasks = []
            for step in layer_steps:
                tasks.append(self._execute_step_async(step, line_item, context, state))
                
            results = await asyncio.gather(*tasks)
            
            # Process results for this layer
            for passed, trace, deduction in results:
                item_traces.append(trace)
                step_confidences.append((trace.confidence, getattr(step, 'confidence_weight', 1.0)))
                
                if deduction:
                    item_deductions.append(deduction)
                    admissible_amount -= deduction.amount
                    payable_amount -= deduction.amount
                
                # ---------------------------------------------------------------
                # FAIL-SAFE ROUTING (async path) — mirrors sync logic exactly.
                # ---------------------------------------------------------------
                if not passed:
                    is_semantic_or_hybrid = step.execution_type in (
                        ExecutionType.SEMANTIC.value, ExecutionType.HYBRID.value
                    )
                    exclusion_hard_stop = trace.evaluation == "EXCLUSION_ACTIVE"
                    is_exclusion_gate = step.gate.value == "exclusion_validation" if hasattr(step.gate, "value") else "exclusion" in str(step.gate)
                    if trace.confidence < self.medical_review_threshold:
                        self.manual_review_count += 1
                        AgentReasoningLogger.log_routing(
                            claim_id=context.claim_id,
                            line_item_id=line_item.line_item_id if line_item else None,
                            gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                            rule_id=step.rule_id,
                            confidence=trace.confidence,
                            action="PENDING_REVIEW",
                            reason=f"Very low confidence ({trace.confidence:.2f}) on failed step: {trace.reason}"
                        )
                        return self._create_review_decision(
                            line_item, f"Low confidence: {trace.reason}", item_traces
                        )
                    elif exclusion_hard_stop and trace.confidence < 0.80:
                        self.manual_review_count += 1
                        AgentReasoningLogger.log_routing(
                            claim_id=context.claim_id,
                            line_item_id=line_item.line_item_id if line_item else None,
                            gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                            rule_id=step.rule_id,
                            confidence=trace.confidence,
                            action="MEDICAL_REVIEW",
                            reason=f"Low-confidence exclusion ({trace.confidence:.2f}): {trace.reason}"
                        )
                        return self._create_medical_review_decision(
                            line_item, trace.reason, item_traces, confidence=trace.confidence
                        )
                    elif is_semantic_or_hybrid and not exclusion_hard_stop:
                        if is_exclusion_gate and trace.confidence < self.assisted_review_threshold:
                            self.manual_review_count += 1
                            AgentReasoningLogger.log_routing(
                                claim_id=context.claim_id,
                                line_item_id=line_item.line_item_id if line_item else None,
                                gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                                rule_id=step.rule_id,
                                confidence=trace.confidence,
                                action="MEDICAL_REVIEW",
                                reason=f"Intermediate confidence ({trace.confidence:.2f}) on exclusion gate: {trace.reason}"
                            )
                            return self._create_medical_review_decision(
                                line_item, trace.reason, item_traces, confidence=trace.confidence
                            )
                        elif trace.confidence < self.confidence_threshold:
                            self.manual_review_count += 1
                            AgentReasoningLogger.log_routing(
                                claim_id=context.claim_id,
                                line_item_id=line_item.line_item_id if line_item else None,
                                gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
                                rule_id=step.rule_id,
                                confidence=trace.confidence,
                                action="ASSISTED_REVIEW",
                                reason=f"Semantic ambiguity ({trace.confidence:.2f}): {trace.reason}"
                            )
                            return LineItemDecision(
                                line_item_id=line_item.line_item_id,
                                description=line_item.description,
                                claimed_amount=claimed_amount,
                                admissible_amount=admissible_amount,
                                payable_amount=payable_amount,
                                decision="ASSISTED_REVIEW",
                                deductions=item_deductions,
                                decision_trace=item_traces,
                                confidence_score=trace.confidence,
                                manual_review_required=True,
                                review_reason=f"Semantic rule {step.rule_id} returned ambiguous evaluation: {trace.reason}"
                            )
                        else:
                            return self._create_rejected_decision(
                                line_item, trace.reason, item_traces
                            )
                    else:
                        return self._create_rejected_decision(
                            line_item, trace.reason, item_traces
                        )
        
        # Calculate final payable through financial gates
        if payable_amount > 0:
            # Enforce Mutual Exclusivity Constraints before running financials
            mx_violation = self._validate_mutual_exclusivity(context)
            if mx_violation:
                constraint_id, reason = mx_violation
                self.manual_review_count += 1
                
                mx_trace = DecisionTrace(
                    step=_STEP_LOCAL.current_step + 1,
                    rule_id=constraint_id,
                    rule_name="Mutual Exclusivity Validation",
                    gate="financial_computation",
                    inputs={
                        "co_payment_percent": context.policy.co_payment_percent,
                        "annual_aggregate_deductible": context.policy.annual_aggregate_deductible,
                    },
                    evaluation="FAILED",
                    reason=reason,
                    confidence=0.0,
                    source_section="mutual_exclusivity_constraints"
                )
                item_traces.append(mx_trace)
                
                return LineItemDecision(
                    line_item_id=line_item.line_item_id,
                    description=line_item.description,
                    claimed_amount=claimed_amount,
                    admissible_amount=0.0,
                    payable_amount=0.0,
                    decision="PENDING_REVIEW",
                    deductions=[],
                    decision_trace=item_traces,
                    confidence_score=0.0,
                    manual_review_required=True,
                    review_reason=f"Mutual Exclusivity Constraint {constraint_id}: {reason}"
                )
            final_admissible, final_payable, fin_deductions, fin_traces = \
                self._execute_financial_gates(context, line_item, payable_amount, state)
            
            item_traces.extend(fin_traces)
            item_deductions.extend(fin_deductions)
            payable_amount = final_payable
            admissible_amount = final_admissible
        
        # Calculate overall confidence using confidence_weight (Gap 12)
        if step_confidences:
            weighted_sum = 0.0
            weight_total = 0.0
            for entry in step_confidences:
                if isinstance(entry, tuple):
                    conf, w = entry
                else:
                    conf, w = entry, 1.0
                weighted_sum += conf * w
                weight_total += w
            overall_confidence = weighted_sum / weight_total if weight_total > 0 else 1.0
        else:
            overall_confidence = 1.0
        state.overall_confidence = overall_confidence
        # Issue 16 / Gap 8: Four-tier confidence routing (Section 9.2):
        if overall_confidence < self.medical_review_threshold:
            self.manual_review_count += 1
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="PENDING_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Very low confidence ({overall_confidence:.2f}) - full manual review required"
            )
        if overall_confidence < self.assisted_review_threshold:
            self.manual_review_count += 1
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="MEDICAL_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Intermediate confidence ({overall_confidence:.2f}) - clinical review required"
            )
        if overall_confidence < self.confidence_threshold:
            self.manual_review_count += 1
            if payable_amount == 0:
                assisted_status = "REJECTED"
            elif payable_amount < claimed_amount:
                assisted_status = "PARTIALLY_APPROVED"
            else:
                assisted_status = "APPROVED"
            return LineItemDecision(
                line_item_id=line_item.line_item_id,
                description=line_item.description,
                claimed_amount=claimed_amount,
                admissible_amount=admissible_amount,
                payable_amount=payable_amount,
                decision="ASSISTED_REVIEW",
                deductions=item_deductions,
                decision_trace=item_traces,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Medium confidence ({overall_confidence:.2f}) - pre-populated for operations review (suggested: {assisted_status})"
            )
        # High confidence (>= 0.90)
        if payable_amount == 0:
            decision_status = "REJECTED"
        elif payable_amount < claimed_amount:
            decision_status = "PARTIALLY_APPROVED"
        else:
            decision_status = "APPROVED"
        return LineItemDecision(
            line_item_id=line_item.line_item_id,
            description=line_item.description,
            claimed_amount=claimed_amount,
            admissible_amount=admissible_amount,
            payable_amount=payable_amount,
            decision=decision_status,
            deductions=item_deductions,
            decision_trace=item_traces,
            confidence_score=overall_confidence,
            manual_review_required=False,
            review_reason=None
        )
    async def _execute_step_async(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Route step execution to sync or async/thread-pool executor based on type"""
        _STEP_LOCAL.current_step += 1
        
        gate_name = step.gate.value if hasattr(step.gate, "value") else str(step.gate)
        t_start = time.perf_counter()
        
        try:
            if step.execution_type == ExecutionType.DETERMINISTIC.value:
                # Deterministic steps are pure calculations (no network), run synchronously
                return self._execute_deterministic_step(step, line_item, context, state)
                
            elif step.execution_type == ExecutionType.SEMANTIC.value:
                # Semantic steps call LLM (network blocking), run in separate thread
                result = await asyncio.to_thread(
                    self._execute_semantic_step, step, line_item, context, state
                )
                self.semantic_calls += 1
                return result
                
            elif step.execution_type == ExecutionType.HYBRID.value:
                # Hybrid steps are deterministic but fallback to semantic (LLM) if low confidence
                return await self._execute_hybrid_step_async(step, line_item, context, state)
                
            else:
                return self._execute_deterministic_step(step, line_item, context, state)
        finally:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            session = telemetry_context.get()
            if session:
                session.record_gate_latency(gate_name, duration_ms)
    async def _execute_hybrid_step_async(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Execute hybrid step, calling semantic LLM in thread pool if deterministic confidence is low"""
        det_passed, det_trace, det_deduction = self._execute_deterministic_step(
            step, line_item, context, state
        )
        
        if det_trace.confidence < 0.95:
            sem_passed, sem_trace, _ = await asyncio.to_thread(
                self._execute_semantic_step, step, line_item, context, state
            )
            self.semantic_calls += 1
            
            if sem_trace.confidence > det_trace.confidence:
                return sem_passed, sem_trace, det_deduction
                
        return det_passed, det_trace, det_deduction
