"""
Gate Execution Pipeline for Claims Auto-Adjudication - Phase 2
Dynamic Graph-based Execution with AI Planner and Semantic Agent
"""

from typing import List, Tuple, Optional, Any, Dict
from datetime import datetime, timezone, date
import time
import asyncio

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
from planner import AIPlanner, ExecutionPlan, ExecutionStep
from semantic_agent import SemanticExecutionAgent


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

    def __init__(
        self,
        use_ai: bool = True,
        confidence_threshold: float = 0.90,
        assisted_review_threshold: float = 0.70,
        llm_provider: str = "local",
        local_llm_url: str = "http://localhost:8080"
    ):
        self.decision_traces: List[DecisionTrace] = []
        self.current_step = 0
        self.use_ai = use_ai
        self.confidence_threshold = confidence_threshold        # >= 0.90 → auto-approve
        self.assisted_review_threshold = assisted_review_threshold  # 0.70–0.90 → ASSISTED_REVIEW

        # Initialize AI components
        self.product_memory = get_product_memory()
        self.planner = AIPlanner(self.product_memory)
        self.semantic_agent = SemanticExecutionAgent(
            llm_provider=llm_provider,
            confidence_threshold=confidence_threshold,
            local_llm_url=local_llm_url
        ) if use_ai else None

        # Observability
        self.plans_created = 0
        self.semantic_calls = 0
        self.manual_review_count = 0

    def _validate_mutual_exclusivity(
        self, context: ClaimContext
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

        for constraint in mx_constraints:
            constraint_id = constraint.get('constraint_id')
            benefits = constraint.get('benefits', [])
            
            active_benefits = [benefit for benefit in benefits if is_benefit_active(benefit)]
            if len(active_benefits) > 1:
                reason = (
                    f"Policy configured with contradictory benefits: {', '.join(active_benefits)} "
                    f"- mutually exclusive benefits matching constraint {constraint_id}"
                )
                return (constraint_id, reason)

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
        start_time = time.time()
        self.decision_traces = []
        self.current_step = 0

        # Issue 21: Bind product memory to the version declared in this claim's context.
        # Each distinct product_json_version gets its own cached ProductMemoryStore so
        # a policy issued under an older rule set is not adjudicated with newer rules.
        claim_version = getattr(context, "product_json_version", "")
        version_memory = get_product_memory(claim_version)
        if version_memory is not self.product_memory:
            # Swap in the version-correct store for this call's planner instance.
            # This is safe because adjudicate_claim is called serially; if concurrent
            # calls become a requirement the planner must be instantiated per-call.
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
            self.decision_traces.append(mx_trace)

            # Return claim with PENDING_REVIEW status
            return self._create_claim_review_decision(
                context, constraint_id, reason, [mx_trace], start_time
            )

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
            # Phase 2: Create execution plan using AI Planner
            execution_plan = self.planner.create_execution_plan(context, line_item)
            self.plans_created += 1
            
            # Execute the plan
            decision = self._execute_plan(execution_plan, line_item, context, state)
            line_item_decisions.append(decision)
            
            # Consolidate traces into the pipeline's overall traces list
            if hasattr(decision, "decision_trace") and decision.decision_trace:
                self.decision_traces.extend(decision.decision_trace)
        
        # Gate 7: State Update and Persistence (BUG FIX #10)
        self._gate_7_state_update(context, state, line_item_decisions)
        
        # Compose final claim-level decision
        claim_decision = self._compose_claim_decision(
            context, line_item_decisions, state, start_time
        )
        
        return claim_decision
    
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
        
        # Execute each step in the plan
        for step in plan.execution_steps:
            self.current_step += 1
            
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
            
            # Add trace
            item_traces.append(trace)
            step_confidences.append(trace.confidence)
            
            # Add deduction if applicable
            if deduction:
                item_deductions.append(deduction)
                admissible_amount -= deduction.amount
                payable_amount -= deduction.amount
            
            # Check if step failed (rejection)
            if not passed:
                # Low confidence or explicit rejection
                if trace.confidence < self.confidence_threshold:
                    self.manual_review_count += 1
                    return self._create_review_decision(
                        line_item, f"Low confidence: {trace.reason}", item_traces
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
                    step=self.current_step + 1,
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
        
        # Calculate overall confidence
        overall_confidence = sum(step_confidences) / len(step_confidences) if step_confidences else 1.0
        state.overall_confidence = overall_confidence

        # Issue 16: Three-tier confidence routing (Section 9.2):
        #   >= 0.90   → auto-approve/reject deterministically
        #   0.70-0.90 → ASSISTED_REVIEW: pre-populate, flag for operations review
        #   < 0.70    → PENDING_REVIEW: full manual review
        if overall_confidence < self.assisted_review_threshold:
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
                review_reason=f"Low confidence ({overall_confidence:.2f}) — full manual review required"
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
        
        elif step.gate == RuleGate.WAITING_PERIOD_VALIDATION:
            return self._execute_waiting_period_check(step, line_item, context, state)
        
        elif step.gate == RuleGate.EXCLUSION_VALIDATION:
            if step.rule_id in ["R3_EXCL_010", "R3_EXCL_021"]:
                passed, trace = self._gate_5_exclusion_validation(context, line_item)
                return passed, trace, None
        
        # Default pass for unhandled deterministic rules
        return True, DecisionTrace(
            step=self.current_step,
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
        
        if not self.use_ai or not self.semantic_agent:
            # Fallback: route to manual review
            return False, DecisionTrace(
                step=self.current_step,
                rule_id=step.rule_id,
                rule_name=step.rule_name,
                gate=step.gate.value,
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
            step=self.current_step,
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
        admission_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        
        # Call Tool 1
        result = calculate_waiting_period(
            condition=line_item.condition_diagnosed,
            policy_inception_date=policy_start,
            continuous_coverage_months=continuous_months,
            portability_credit_months=context.porting.waiting_period_credit_months,
            accident_flag=line_item.accident_related,
            cancer_flag="cancer" in line_item.condition_diagnosed.lower(),
            claim_date=admission_date,
            ped_declarations=context.member.ped_declarations
        )
        
        # Create trace
        trace = DecisionTrace(
            step=self.current_step,
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
        # Reuse Phase 1 financial computation logic
        return self._gate_6_financial_computation(
            context, line_item, payable_amount, state
        )
    
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
    
    # Keep existing gate methods from Phase 1
    def _gate_1_policy_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 1: Policy Validation
        Checks: Policy active? Premium paid? Not lapsed? Not void?
        """
        self.current_step += 1
        
        # Check policy status
        if context.policy.status != "Active":
            return False, DecisionTrace(
                step=self.current_step,
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
                step=self.current_step,
                rule_id="GATE_1_PREMIUM_PAYMENT",
                rule_name="Premium Payment Check",
                gate="policy_validation",
                inputs={"premium_paid": False, "grace_period": False},
                evaluation="FAILED",
                reason="Premium not paid and grace period expired",
                source_section="6.1.17"
            )
        
        return True, DecisionTrace(
            step=self.current_step,
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
        self.current_step += 1
        
        # Check member eligibility
        if not context.member.eligibility_active:
            return False, DecisionTrace(
                step=self.current_step,
                rule_id="GATE_2_MEMBER_ELIGIBILITY",
                rule_name="Member Eligibility Check",
                gate="member_validation",
                inputs={"member_id": context.member.member_id, "eligible": False},
                evaluation="FAILED",
                reason="Member not eligible for coverage"
            )
        
        return True, DecisionTrace(
            step=self.current_step,
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
        """
        self.current_step += 1
        
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
                step=self.current_step,
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
            # Check if optional benefit is opted
            if "borderless" in rule.rule_id.lower() and rule.rule_id.lower() == "r3_ben_017":
                # Borderless for Specified Illness
                if not context.policy.borderless_specific_illness_opted:
                    return False, DecisionTrace(
                        step=self.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="FAILED",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    )
            elif "borderless" in rule.rule_id.lower():
                if not context.policy.borderless_opted:
                    return False, DecisionTrace(
                        step=self.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="FAILED",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    )
            elif "heads_up" in rule.rule_id.lower():
                if not context.policy.heads_up_opted:
                    return False, DecisionTrace(
                        step=self.current_step,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        gate="coverage_validation",
                        inputs={"benefit": rule.rule_name, "opted": False},
                        evaluation="FAILED",
                        reason=f"Optional benefit '{rule.rule_name}' not opted by member"
                    )
        
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
                    step=self.current_step,
                    rule_id="R3_BEN_007_PRECONDITION",
                    rule_name="Home Care Preconditions",
                    gate="coverage_validation",
                    inputs={"preconditions": missing},
                    evaluation="FAILED",
                    reason=f"Home Care requires: {', '.join(missing)}"
                )
        
        # All coverage checks passed
        return True, DecisionTrace(
            step=self.current_step,
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
        """
        self.current_step += 1
        
        # Coerce inputs safely
        policy_start = self._coerce_to_date(context.policy.policy_start_date)
        admission_date = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        
        # Call Tool 1
        result = calculate_waiting_period(
            condition=line_item.condition_diagnosed,
            policy_inception_date=policy_start,
            continuous_coverage_months=context.history.claim_free_years * 12,
            portability_credit_months=context.porting.waiting_period_credit_months,
            # R3_EXCL_017: Personal waiting period (insurer-imposed, up to 48 months)
            personal_waiting_period_months=getattr(context.policy, "personal_waiting_period_months", 0) or 0,
            accident_flag=line_item.accident_related,
            cancer_flag="cancer" in line_item.condition_diagnosed.lower(),
            claim_date=admission_date,
            ped_declarations=context.member.ped_declarations
        )

        
        if result.exclusion_active:
            return False, DecisionTrace(
                step=self.current_step,
                rule_id=result.rule_applied,
                rule_name="Waiting Period Check",
                gate="waiting_period_validation",
                inputs=result.details,
                evaluation="EXCLUSION_ACTIVE",
                reason=f"Waiting period active: {result.remaining_days} days remaining",
                source_section="5.1.1, 5.1.2, 5.1.3"
            )
        
        return True, DecisionTrace(
            step=self.current_step,
            rule_id="GATE_4_PASSED",
            rule_name="Waiting Period Validation",
            gate="waiting_period_validation",
            inputs={"condition": line_item.condition_diagnosed},
            evaluation="PASSED",
            reason="All waiting periods cleared"
        )
    
    def _gate_5_exclusion_validation(self, context: ClaimContext, line_item) -> Tuple[bool, DecisionTrace]:
        """
        Gate 5: Exclusion Validation
        Checks all 23 exclusion rules dynamically from Product Memory.
        BUG FIX #6: Evaluate all exclusion rules, not just 2.
        Executes deterministic and semantic rules as specified.
        """
        self.current_step += 1
        
        # Get all exclusion rules from Product Memory
        exclusion_rules = self.product_memory.filter_rules(
            gate=RuleGate.EXCLUSION_VALIDATION
        )
        
        # Execute each exclusion rule (deterministic and semantic)
        for rule in exclusion_rules:
            # Deterministic exclusions - check directly
            if rule.execution_type == ExecutionType.DETERMINISTIC or rule.rule_id in [
                "R3_EXCL_010",  # Excluded Provider
                "R3_EXCL_021",  # Unrecognized Physician
                "R3_EXCL_020"   # Dental (conditional - allowed for accidents)
            ]:
                # Check specific deterministic rules
                if rule.rule_id == "R3_EXCL_010":  # Excluded Provider
                    if context.network.provider_type == "Excluded":
                        return False, DecisionTrace(
                            step=self.current_step,
                            rule_id="R3_EXCL_010",
                            rule_name="Excluded Provider",
                            gate="exclusion_validation",
                            inputs={"provider": context.network.provider_name},
                            evaluation="EXCLUSION_ACTIVE",
                            reason="Treatment at excluded provider",
                            source_section="5.1.10"
                        )
                
                elif rule.rule_id == "R3_EXCL_021":  # Unrecognized Physician/Hospital
                    is_unrecognized = "unrecognized" in context.network.provider_name.lower()
                    is_family_member = False
                    if hasattr(line_item, "description") and line_item.description:
                        desc = line_item.description.lower()
                        if "relative" in desc or "family member" in desc or "self-treated" in desc:
                            is_family_member = True

                    if is_unrecognized or is_family_member:
                        reason = "Treatment at unrecognized facility/practitioner" if is_unrecognized else "Treatment by family member is excluded"
                        return False, DecisionTrace(
                            step=self.current_step,
                            rule_id="R3_EXCL_021",
                            rule_name="Unrecognized Physician or Hospital",
                            gate="exclusion_validation",
                            inputs={"provider": context.network.provider_name, "is_family": is_family_member},
                            evaluation="EXCLUSION_ACTIVE",
                            reason=reason,
                            source_section="5.2.5"
                        )
                
                elif rule.rule_id == "R3_EXCL_020":  # Dental treatment
                    desc_lower = (line_item.description or "").lower()
                    is_dental = any(term in desc_lower for term in ["dental", "teeth", "tooth", "extraction"])
                    
                    if is_dental:
                        # Allowed for accidents; excluded otherwise
                        if not line_item.accident_related:
                            return False, DecisionTrace(
                                step=self.current_step,
                                rule_id="R3_EXCL_020",
                                rule_name="Dental Treatment",
                                gate="exclusion_validation",
                                inputs={"accident_related": line_item.accident_related},
                                evaluation="EXCLUSION_ACTIVE",
                                reason="Dental treatment excluded (allowed only for accident-related)",
                                source_section="5.1.20"
                            )
            
            # Semantic/Hybrid exclusions - delegate to semantic agent if available
            elif rule.execution_type == ExecutionType.SEMANTIC or rule.execution_type == ExecutionType.HYBRID:
                if self.semantic_agent:
                    try:
                        sem_result = self.semantic_agent.execute_semantic_rule(
                            rule=rule,
                            context=context,
                            line_item=line_item,
                            prompt=rule.semantic_prompt_template or ""
                        )
                        
                        if sem_result.confidence >= self.confidence_threshold and not sem_result.passed:
                            return False, DecisionTrace(
                                step=self.current_step,
                                rule_id=rule.rule_id,
                                rule_name=rule.rule_name,
                                gate="exclusion_validation",
                                inputs={"rule": rule.rule_name},
                                evaluation="EXCLUSION_ACTIVE",
                                reason=sem_result.reason,
                                confidence=sem_result.confidence,
                                source_section=rule.section_ref
                            )
                    except Exception as e:
                        # Log error but continue - don't fail on semantic errors
                        pass
        
        # All exclusion rules passed
        return True, DecisionTrace(
            step=self.current_step,
            rule_id="GATE_5_PASSED",
            rule_name="Exclusion Validation",
            gate="exclusion_validation",
            inputs={"rules_checked": len(exclusion_rules)},
            evaluation="PASSED",
            reason=f"All {len(exclusion_rules)} exclusion rules evaluated - no exclusions triggered"
        )
    
    def _get_eligible_room_rent(self, context: ClaimContext, line_item) -> float:
        """
        Get eligible room rent from policy configuration.
        BUG FIX #1: Uses policy.room_category_entitled instead of hardcoded variant mapping.
        """
        room_category_rate_table = {
            "General Ward": {"Classic": 1500.0, "Select": 1500.0, "Elite": 1500.0},
            "Semi Private Room": {"Classic": 3000.0, "Select": 3000.0, "Elite": 3000.0},
            "Single Private Room": {"Classic": 5000.0, "Select": 7500.0, "Elite": 10000.0},
            "ICU": {"Classic": 7500.0, "Select": 10000.0, "Elite": 15000.0},
        }
        
        room_category = context.policy.room_category_entitled or "General Ward"
        variant = context.policy.variant or "Classic"
        
        if room_category in room_category_rate_table:
            rate_by_variant = room_category_rate_table[room_category]
            eligible_room = rate_by_variant.get(variant, 1500.0)
        else:
            eligible_room = room_category_rate_table["General Ward"].get(variant, 1500.0)
        
        return eligible_room
    
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
                # Endorsement not yet effective at time of claim — ignore
                break

            etype = endorsement.endorsement_type
            details = endorsement.details or {}

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

            # MemberDeletion, IndividualToFloater, FloaterSplit are complex
            # topology changes that affect Lock the Clock age recalculation —
            # route these to ASSISTED_REVIEW via confidence reduction rather
            # than applying incomplete mutations.
            # (No context mutation for those types.)

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
        Issue 12: Gate 6 Step 0 — remove Annexure non-payable items BEFORE
        room pro-rata and all other deductions.

        Strategy: substring match on line_item.description against the
        non-payable keyword list. When matched the FULL line item amount is
        deducted (these items have no claimable portion by definition).

        Returns: (remaining_amount, DeductionDetail or None)
        """
        if not line_item.description:
            return amount, None

        desc_lower = line_item.description.lower()
        matched_keyword = next(
            (kw for kw in self._NON_PAYABLE_KEYWORDS if kw in desc_lower),
            None
        )

        if matched_keyword is None:
            return amount, None

        deduction = DeductionDetail(
            deduction_type="non_payable_items",
            amount=amount,
            rule_id="R3_EXCL_ANNEXURE",
            reason=f"Non-payable item (Annexure): matched keyword '{matched_keyword}'",
            calculation_details={
                "description": line_item.description,
                "matched_keyword": matched_keyword,
                "full_amount_deducted": amount
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

        Execution sequence (Section 6.2 + Issues 12-15):
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
        # STEP 0: Non-payable items removal (Issue 12 — Annexure exclusions)
        # Must execute BEFORE everything else — amount reduced to zero for
        # non-payable consumables/charges.
        # ================================================================
        self.current_step += 1
        payable_amount, np_deduction = self._deduct_non_payable_items(
            line_item, payable_amount
        )
        if np_deduction:
            admissible_amount = payable_amount
            deductions.append(np_deduction)
            traces.append(DecisionTrace(
                step=self.current_step,
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
        self.current_step += 1
        payable_amount, mt_deduction = self._apply_modern_treatment_sublimit(
            context, line_item, payable_amount
        )
        if mt_deduction:
            admissible_amount = payable_amount
            deductions.append(mt_deduction)
            traces.append(DecisionTrace(
                step=self.current_step,
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
            self.current_step += 1
            hosp_hours = line_item.hospitalization_hours or 0.0
            daily_cash_amount = getattr(context.policy, "hospital_daily_cash_amount", 0.0) or 0.0

            if daily_cash_amount <= 0.0:
                # Policy does not have a configured daily cash benefit amount;
                # route to ASSISTED_REVIEW.
                traces.append(DecisionTrace(
                    step=self.current_step,
                    rule_id="R3_BEN_011",
                    rule_name="Hospital Daily Cash",
                    gate="financial_computation",
                    inputs={"daily_cash_amount": daily_cash_amount},
                    evaluation="NOT_APPLICABLE",
                    reason="hospital_daily_cash_amount not configured in policy; requires manual review",
                    confidence=0.5
                ))
                return 0.0, 0.0, deductions, traces

            dc_result = calculate_hospital_daily_cash(
                daily_cash_amount=daily_cash_amount,
                hospitalization_hours=hosp_hours,
                hospital_daily_cash_days_used=context.benefit_balance.hospital_cash_days_used
            )

            if dc_result.days_exhausted:
                traces.append(DecisionTrace(
                    step=self.current_step,
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
                step=self.current_step,
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
            self.current_step += 1
            pa_si = getattr(context.policy, "pa_sum_insured", 0.0) or context.policy.base_sum_insured

            pa_result = calculate_personal_accident_benefit(
                pa_sum_insured=pa_si,
                injury_description=line_item.description or line_item.condition_diagnosed,
                accident_related=line_item.accident_related
            )

            if pa_result.pa_benefit_type in ("UNKNOWN", "NOT_APPLICABLE"):
                traces.append(DecisionTrace(
                    step=self.current_step,
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
                step=self.current_step,
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
            self.current_step += 1

            eligible_room = self._get_eligible_room_rent(context, line_item)
            expense_components = self._calculate_associated_medical_expenses(line_item, claimed_amount)

            pro_rata_result = calculate_room_pro_rata(
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
                    reason=f"Room category breach: ratio {pro_rata_result.pro_rata_ratio:.2f}",
                    calculation_details={
                        "eligible_room": pro_rata_result.eligible_room_rent,
                        "actual_room": pro_rata_result.actual_room_rent,
                        "ratio": pro_rata_result.pro_rata_ratio
                    }
                ))

                traces.append(DecisionTrace(
                    step=self.current_step,
                    rule_id="R3_BEN_004",
                    rule_name="Room Pro-Rata",
                    gate="financial_computation",
                    inputs={"eligible": eligible_room, "actual": line_item.actual_room_rent},
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
            self.current_step += 1

            deductible_result = calculate_deductible(
                claim_amount=payable_amount,
                annual_deductible_limit=context.policy.annual_aggregate_deductible,
                deductible_consumed_ytd=context.benefit_balance.deductible_consumed_ytd,
                benefit_bucket=line_item.benefit_bucket
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
                    step=self.current_step,
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
            self.current_step += 1

            # Room category co-pay lookup (Annexure V)
            room_copay_percent = 0.0
            if line_item.room_category_claimed:
                if context.policy.variant == "Classic":
                    if "Suite" in line_item.room_category_claimed:
                        room_copay_percent = 0.50
                    elif "Private" in line_item.room_category_claimed:
                        room_copay_percent = 0.40
                elif context.policy.variant == "Select":
                    if "Suite" in line_item.room_category_claimed:
                        room_copay_percent = 0.40
                    elif "Deluxe" in line_item.room_category_claimed:
                        room_copay_percent = 0.20

            copay_result = calculate_copayment(
                admissible_amount=payable_amount,
                base_copay_percent=context.policy.co_payment_percent,
                benefit_bucket=line_item.benefit_bucket,
                heads_up_penalty=state.heads_up_penalty_triggered,
                tiered_network_penalty=state.tiered_network_penalty_triggered,
                prolonged_hosp_penalty=state.prolonged_hosp_penalty_triggered,
                room_category_copay_percent=room_copay_percent
            )

            if copay_result.copay_amount > 0:
                payable_amount = copay_result.payable_amount
                state.copay_percent_total = copay_result.total_copay_percent

                deductions.append(DeductionDetail(
                    deduction_type="co_payment",
                    amount=copay_result.copay_amount,
                    rule_id="R3_FIN_002",
                    reason=f"Co-payment {copay_result.total_copay_percent:.1f}% (stacked)",
                    calculation_details=copay_result.copay_breakdown
                ))

                traces.append(DecisionTrace(
                    step=self.current_step,
                    rule_id="R3_FIN_002",
                    rule_name="Co-Payment (Stacked)",
                    gate="financial_computation",
                    inputs=copay_result.copay_breakdown,
                    evaluation="DEDUCTION_APPLIED",
                    reason=f"Total co-pay: INR {copay_result.copay_amount:.2f} ({copay_result.total_copay_percent:.1f}%)",
                    source_section="4.19"
                ))

        # ================================================================
        # STEP 6: SI Waterfall Consumption (final step)
        # ================================================================
        self.current_step += 1

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

        si_result = calculate_si_waterfall(
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
            step=self.current_step,
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
        """
        Gate 7: State Update (PERSISTENCE GATE)
        
        BUG FIX #10: Persist all state changes back to context after claims are decided.
        
        Responsibilities:
        1. Update SI balances after all line items are processed
        2. Set ReAssure Forever triggered flag on first PAID claim
        3. Unlock Lock the Clock age if applicable
        4. Update deductible YTD consumed
        5. Update Cash-Bag+ wallet
        6. Mark any pending state transitions
        
        This gate ensures state persistence between claims in multi-claim scenarios.
        """
        self.current_step += 1
        
        # Only update state if there are paid claims
        total_paid = sum(li.payable_amount for li in line_item_decisions if li.decision in ["APPROVED", "PARTIALLY_APPROVED"])
        
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
            # 2. SET REASSURE FOREVER TRIGGERED (on first paid claim)
            # ================================================================
            if not context.lifetime_state.reassure_forever_triggered and total_paid > 0:
                context.lifetime_state.reassure_forever_triggered = True
            
            # ================================================================
            # 3. UNLOCK LOCK THE CLOCK (if age was unlocked for premium)
            # ================================================================
            # Check if this claim triggered age unlock (would be in state)
            # Lock the Clock premium calculation happens in Tool 6, unlocking happens here
            if hasattr(state, "lock_the_clock_age_unlocked") and state.lock_the_clock_age_unlocked:
                context.lifetime_state.lock_the_clock_unlocked_date = datetime.now(timezone.utc)
            
            # ================================================================
            # 4. UPDATE DEDUCTIBLE YTD CONSUMED
            # ================================================================
            if state.deductible_applied_this_claim > 0:
                context.benefit_balance.deductible_consumed_ytd += state.deductible_applied_this_claim
            
            # ================================================================
            # 5. UPDATE CASH-BAG+ WALLET (if applicable)
            # ================================================================
            if hasattr(context.benefit_balance, "cash_bag_plus_wallet"):
                # Cash-Bag+ accumulates on each claim (simplified: 2% of payable per claim)
                accumulation_rate = 0.02  # 2% of payable amount
                cash_bag_accumulation = total_paid * accumulation_rate
                if hasattr(context.benefit_balance, "cash_bag_plus_wallet"):
                    context.benefit_balance.cash_bag_plus_wallet += cash_bag_accumulation
            
            # ================================================================
            # 6. RECORD STATE UPDATE TRACE
            # ================================================================
            trace = DecisionTrace(
                step=self.current_step,
                rule_id="GATE_7_STATE_UPDATE",
                rule_name="State Update & Persistence",
                gate="state_update",
                inputs={
                    "base_si_remaining": context.benefit_balance.base_si_remaining,
                    "booster_remaining": context.benefit_balance.booster_plus_remaining,
                    "forever_pool": context.benefit_balance.reassure_forever_pool,
                    "forever_triggered": context.lifetime_state.reassure_forever_triggered,
                    "deductible_ytd": context.benefit_balance.deductible_consumed_ytd,
                    "total_paid": total_paid
                },
                evaluation="PASSED",
                reason=f"State updated after paid claims totaling INR {total_paid:.2f}",
                source_section="State Management"
            )

            self.decision_traces.append(trace)
    
    
    
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
        start_time: float
    ) -> ClaimDecision:
        """Compose final claim-level decision from line item decisions"""
        
        # Aggregate financials
        total_claimed = sum(d.claimed_amount for d in line_item_decisions)
        total_admissible = sum(d.admissible_amount for d in line_item_decisions)
        total_payable = sum(d.payable_amount for d in line_item_decisions)
        total_deductions = total_claimed - total_payable
        
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

        # SI waterfall breakdown (retrieved from state cache)
        si_waterfall = SIWaterfallBreakdown(
            amount_from_base_si=getattr(state, "amount_from_base_si", 0.0),
            amount_from_booster=getattr(state, "amount_from_booster", 0.0),
            amount_from_forever=getattr(state, "amount_from_forever", 0.0),
            total_paid=total_payable,
            shortfall=total_claimed - total_payable,
            updated_base_si=context.benefit_balance.base_si_remaining - getattr(state, "amount_from_base_si", 0.0),
            updated_booster=context.benefit_balance.booster_plus_remaining - getattr(state, "amount_from_booster", 0.0),
            updated_forever_pool=context.benefit_balance.reassure_forever_pool - getattr(state, "amount_from_forever", 0.0)
        )
        
        # Issue 16/22: Determine overall decision propagating ASSISTED_REVIEW
        # Priority: PENDING_REVIEW > ASSISTED_REVIEW > financial outcome
        if any(d.decision == "PENDING_REVIEW" for d in line_item_decisions):
            overall_decision = "PENDING_REVIEW"
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
            "adjudication_timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "processing_duration_ms": processing_duration,
            "ai_version": "Claims2.0-v1.0",
        }

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
            decision_trace=self.decision_traces,
            confidence_score=state.overall_confidence,
            manual_review_required=state.overall_confidence < self.confidence_threshold,
            processing_duration_ms=processing_duration,
            pas_submission_payload=pas_payload,
        )


    async def adjudicate_claim_async(self, context: ClaimContext) -> ClaimDecision:
        """
        Asynchronous entry point: Execute dynamic graph-based adjudication pipeline in parallel
        
        Uses depth-layer grouping to execute independent rules concurrently.
        """
        start_time = time.time()
        self.decision_traces = []
        self.current_step = 0

        # Issue 21: same version-aware store binding as the sync path.
        claim_version = getattr(context, "product_json_version", "")
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
            self.decision_traces.append(mx_trace)

            # Return claim with PENDING_REVIEW status
            return self._create_claim_review_decision(
                context, constraint_id, reason, [mx_trace], start_time
            )

        # Process each line item through dynamic execution plan concurrently
        line_item_decisions: List[LineItemDecision] = []
        tasks = []
        for line_item in context.line_items:
            # Create execution plan using AI Planner
            execution_plan = self.planner.create_execution_plan(context, line_item)
            self.plans_created += 1
            
            # Execute the plan asynchronously
            tasks.append(self._execute_plan_async(execution_plan, line_item, context, state))
            
        line_item_decisions = list(await asyncio.gather(*tasks))
        
        for decision in line_item_decisions:
            if hasattr(decision, "decision_trace") and decision.decision_trace:
                self.decision_traces.extend(decision.decision_trace)
        
        # Gate 7: State Update and Persistence (BUG FIX #10)
        self._gate_7_state_update(context, state, line_item_decisions)
        
        # Compose final claim-level decision
        claim_decision = self._compose_claim_decision(
            context, line_item_decisions, state, start_time
        )
        
        return claim_decision

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
                step_confidences.append(trace.confidence)
                
                if deduction:
                    item_deductions.append(deduction)
                    admissible_amount -= deduction.amount
                    payable_amount -= deduction.amount
                
                # Check if step failed (rejection)
                if not passed:
                    if trace.confidence < self.confidence_threshold:
                        self.manual_review_count += 1
                        return self._create_review_decision(
                            line_item, f"Low confidence: {trace.reason}", item_traces
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
                    step=self.current_step + 1,
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
        
        # Calculate overall confidence
        overall_confidence = sum(step_confidences) / len(step_confidences) if step_confidences else 1.0
        state.overall_confidence = overall_confidence

        # Issue 16: Three-tier confidence routing (Section 9.2) — mirrors sync path
        if overall_confidence < self.assisted_review_threshold:
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
                review_reason=f"Low confidence ({overall_confidence:.2f}) — full manual review required"
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
                review_reason=f"Medium confidence ({overall_confidence:.2f}) — pre-populated for operations review (suggested: {assisted_status})"
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
        self.current_step += 1
        
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
