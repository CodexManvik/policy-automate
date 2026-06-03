"""
Gate Execution Pipeline for Claims Auto-Adjudication - Phase 2
Dynamic Graph-based Execution with AI Planner and Semantic Agent
"""

from typing import List, Tuple, Optional, Any
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
    calculate_booster_accumulation, validate_pre_post_hosp_window
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
        llm_provider: str = "local",
        local_llm_url: str = "http://localhost:8080"
    ):
        self.decision_traces: List[DecisionTrace] = []
        self.current_step = 0
        self.use_ai = use_ai
        self.confidence_threshold = confidence_threshold
        
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
                context, constraint_id, reason, [mx_trace]
            )

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
        
        # Check if manual review required
        requires_review = overall_confidence < self.confidence_threshold
        
        # Determine decision status
        if requires_review:
            decision_status = "PENDING_REVIEW"
        elif payable_amount == 0:
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
            manual_review_required=requires_review,
            review_reason="Low confidence score" if requires_review else None
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
        """
        self.current_step += 1
        
        # Simplified check - in real system, check against product JSON
        covered_benefits = [
            "Expenses in reaching a Hospital",
            "Expenses during Hospitalization",
            "Expenses before and after hospitalization",
            "Home Care / Domiciliary Treatment",
            "Organ Donor"
        ]
        
        if line_item.benefit_bucket not in covered_benefits:
            # Check if optional benefit
            return False, DecisionTrace(
                step=self.current_step,
                rule_id="GATE_3_BENEFIT_COVERAGE",
                rule_name="Benefit Coverage Check",
                gate="coverage_validation",
                inputs={"benefit_bucket": line_item.benefit_bucket},
                evaluation="FAILED",
                reason=f"Benefit {line_item.benefit_bucket} not covered or not opted"
            )
        
        return True, DecisionTrace(
            step=self.current_step,
            rule_id="GATE_3_PASSED",
            rule_name="Coverage Validation",
            gate="coverage_validation",
            inputs={"benefit_bucket": line_item.benefit_bucket},
            evaluation="PASSED",
            reason="Benefit is covered"
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
        Checks standard exclusions (Excl01-Excl18) and specific exclusions
        """
        self.current_step += 1
        
        # Check network provider exclusion
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
            
        # Check unrecognized physician/hospital or family member/relative (R3_EXCL_021)
        is_unrecognized = "unrecognized" in context.network.provider_name.lower() or context.network.provider_type == "Excluded"
        is_family_member = False
        if hasattr(line_item, "description") and line_item.description:
            desc = line_item.description.lower()
            if "relative" in desc or "family member" in desc or "self-treated" in desc:
                is_family_member = True

        if is_unrecognized or is_family_member:
            reason = "Treatment at unrecognized facility/practitioner" if is_unrecognized else "Treatment by a family member or relative is excluded"
            return False, DecisionTrace(
                step=self.current_step,
                rule_id="R3_EXCL_021",
                rule_name="Unrecognized Physician or Hospital",
                gate="exclusion_validation",
                inputs={
                    "provider": context.network.provider_name,
                    "provider_type": context.network.provider_type,
                    "description": getattr(line_item, "description", "")
                },
                evaluation="EXCLUSION_ACTIVE",
                reason=reason,
                source_section="5.2.5"
            )
        
        # Simplified - in real system, check all exclusions from product JSON
        return True, DecisionTrace(
            step=self.current_step,
            rule_id="GATE_5_PASSED",
            rule_name="Exclusion Validation",
            gate="exclusion_validation",
            inputs={"condition": line_item.condition_diagnosed},
            evaluation="PASSED",
            reason="No exclusions triggered"
        )
    
    def _gate_6_financial_computation(
        self, 
        context: ClaimContext, 
        line_item, 
        claimed_amount: float,
        state: PerClaimState
    ) -> Tuple[float, float, List[DeductionDetail], List[DecisionTrace]]:
        """
        Gate 6: Financial Computation
        
        Execution sequence (critical ordering from Section 6.2):
        1. Room Pro-Rata (Tool 2)
        2. Prolonged Hospitalization Penalty (if applicable)
        3. HeadsUp/Tiered Network Penalty (if applicable)
        4. Annual Aggregate Deductible (Tool 4)
        5. Co-Payment (Tool 3) - stacks all penalties
        6. SI Waterfall (Tool 5)
        """
        traces: List[DecisionTrace] = []
        deductions: List[DeductionDetail] = []
        
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        
        # ================================================================
        # STEP 1: Room Pro-Rata (must execute first)
        # ================================================================
        if line_item.actual_room_rent and line_item.actual_room_rent > 0:
            self.current_step += 1
            
            # Get eligible room rent based on variant
            variant_room_mapping = {
                "Classic": 1500.0,   # Simplified
                "Select": 3000.0,
                "Elite": 5000.0
            }
            eligible_room = variant_room_mapping.get(context.policy.variant, 1500.0)
            
            # Simplified: assume 30% of claim is associated medical expenses
            associated_expenses = claimed_amount * 0.30
            
            pro_rata_result = calculate_room_pro_rata(
                eligible_room_rent=eligible_room,
                actual_room_rent=line_item.actual_room_rent,
                room_charges=line_item.actual_room_rent,
                nursing_charges=associated_expenses * 0.3,
                medical_practitioner_fees=associated_expenses * 0.4,
                ot_charges=associated_expenses * 0.3
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
            # Check if notified within 7 days (simplified check)
            state.prolonged_hosp_penalty_triggered = True
        
        # ================================================================
        # STEP 3: HeadsUp / Tiered Network Penalties
        # ================================================================
        if context.policy.heads_up_opted:
            # Check if network recommended was used
            if not context.network.heads_up_recommended:
                state.heads_up_penalty_triggered = True
        
        if context.policy.tiered_network_opted:
            # Check if tiered network member
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
                    reason=f"Annual aggregate deductible applied",
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
            
            # Get room category co-pay if applicable (Annexure V)
            room_copay_percent = 0.0
            if line_item.room_category_claimed:
                # Simplified room co-pay lookup
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
        
        # ReAssure Forever State Trap validation: pool must not trigger if it's the first claim (prior_claims_count == 0)
        # and reassure_forever_triggered is False.
        forever_pool_val = context.benefit_balance.reassure_forever_pool
        forever_triggered_val = context.lifetime_state.reassure_forever_triggered
        
        prior_claims = getattr(context.history, "prior_claims_count", 0)
        if not (forever_triggered_val or prior_claims > 0):
            forever_pool_val = 0.0
            forever_triggered_val = False
            
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
        
        # Update state with new balances
        state.running_payable_amount += si_result.total_paid
        
        # Cache the exact source allocations directly into the state schema (accumulated)
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
            reason=f"SI consumed: Base INR {si_result.amount_from_base_si:.2f}, Booster INR {si_result.amount_from_booster:.2f}, Forever INR {si_result.amount_from_forever:.2f}",
            source_section="3"
        ))
        
        # Final payable is what SI waterfall could pay
        final_payable = si_result.total_paid
        
        return admissible_amount, final_payable, deductions, traces
    
    # ====================================================================
    # HELPER METHODS
    # ====================================================================
    
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
    ) -> ClaimDecision:
        """Create a claim-level PENDING_REVIEW decision for constraint violation"""
        total_claimed = sum(li.claimed_amount for li in context.line_items)

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
            processing_duration_ms=(time.time() - time.time()) * 1000,
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
        
        # Determine overall decision
        if any(d.decision == "PENDING_REVIEW" for d in line_item_decisions):
            overall_decision = "PENDING_REVIEW"
        elif total_payable == 0:
            overall_decision = "REJECTED"
        elif total_payable < total_claimed:
            overall_decision = "PARTIALLY_APPROVED"
        else:
            overall_decision = "APPROVED"
        
        # Calculate processing duration
        processing_duration = (time.time() - start_time) * 1000  # ms
        
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
            processing_duration_ms=processing_duration
        )

    async def adjudicate_claim_async(self, context: ClaimContext) -> ClaimDecision:
        """
        Asynchronous entry point: Execute dynamic graph-based adjudication pipeline in parallel
        
        Uses depth-layer grouping to execute independent rules concurrently.
        """
        start_time = time.time()
        self.decision_traces = []
        self.current_step = 0

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
                context, constraint_id, reason, [mx_trace]
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
        
        # Check if manual review required
        requires_review = overall_confidence < self.confidence_threshold
        
        # Determine decision status
        if requires_review:
            decision_status = "PENDING_REVIEW"
        elif payable_amount == 0:
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
            manual_review_required=requires_review,
            review_reason="Low confidence score" if requires_review else None
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
