import logging
_logger = logging.getLogger("claims_adjudication_pipeline")
from typing import List, Tuple, Optional, Any, Dict, Callable
from datetime import datetime, timezone, date
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


class ExecutionMixin:
    def _process_step_result(
        self,
        step: ExecutionStep,
        passed: bool,
        trace: DecisionTrace,
        deduction: Optional[DeductionDetail],
        claimed_amount: float,
        admissible_amount: float,
        payable_amount: float,
        item_deductions: List[DeductionDetail],
        item_traces: List[DecisionTrace],
        step_confidences: List[Tuple[float, float]],
        context: ClaimContext,
        line_item
    ) -> Tuple[float, float, Optional[LineItemDecision]]:
        """
        Process the result of a single execution step.
        Updates admissible/payable amounts, appends traces/deductions/confidences,
        and returns a LineItemDecision if a fail-safe route is triggered.
        """
        item_traces.append(trace)
        step_confidences.append((trace.confidence, getattr(step, 'confidence_weight', 1.0)))
        
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
                return admissible_amount, payable_amount, self._create_review_decision(
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
                return admissible_amount, payable_amount, self._create_medical_review_decision(
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
                    return admissible_amount, payable_amount, self._create_medical_review_decision(
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
                    return admissible_amount, payable_amount, LineItemDecision(
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
                    return admissible_amount, payable_amount, self._create_rejected_decision(
                        line_item, trace.reason, item_traces
                    )
            else:
                return admissible_amount, payable_amount, self._create_rejected_decision(
                    line_item, trace.reason, item_traces
                )
        return admissible_amount, payable_amount, None

    def _finalize_line_item_decision(
        self,
        claimed_amount: float,
        admissible_amount: float,
        payable_amount: float,
        item_deductions: List[DeductionDetail],
        item_traces: List[DecisionTrace],
        step_confidences: List[Tuple[float, float]],
        context: ClaimContext,
        line_item,
        state: PerClaimState,
        tool_calls: Optional[List] = None
    ) -> LineItemDecision:
        """
        Finalize financial gates and routing decisions for a line item.
        tool_calls: accumulated ToolCallTrace entries from all calculator invocations.
        """
        _tool_calls = tool_calls or []
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
                
                AgentReasoningLogger.log_gate_evaluation(
                    claim_id=context.claim_id,
                    line_item_id=line_item.line_item_id if line_item else None,
                    gate="financial_computation",
                    rule_id=constraint_id,
                    inputs=mx_trace.inputs,
                    evaluation_status=mx_trace.evaluation,
                    reason=mx_trace.reason
                )
                
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
                    tool_calls=_tool_calls,
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
                tool_calls=_tool_calls,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Very low confidence ({overall_confidence:.2f}) — full manual review required"
            )
        elif overall_confidence < self.assisted_review_threshold:
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
                tool_calls=_tool_calls,
                confidence_score=overall_confidence,
                manual_review_required=True,
                review_reason=f"Intermediate confidence ({overall_confidence:.2f}) — clinical review required"
            )
        elif overall_confidence < self.confidence_threshold:
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
                tool_calls=_tool_calls,
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
            tool_calls=_tool_calls,
            confidence_score=overall_confidence,
            manual_review_required=False
        )

    def _execute_plan(
        self,
        plan: ExecutionPlan,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> LineItemDecision:
        """
        Execute the dynamically generated execution plan
        """
        item_traces: List[DecisionTrace] = []
        item_deductions: List[DeductionDetail] = []
        self._reset_tool_calls()  # Clear accumulator for this line item
        
        claimed_amount = line_item.claimed_amount
        admissible_amount = claimed_amount
        payable_amount = claimed_amount
        
        step_confidences = []
        
        for step in plan.execution_steps:
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
                passed, trace, deduction = self._execute_deterministic_step(
                    step, line_item, context, state
                )
            
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            session = telemetry_context.get()
            if session:
                session.record_gate_latency(gate_name, duration_ms)
                
            admissible_amount, payable_amount, fail_decision = self._process_step_result(
                step, passed, trace, deduction, claimed_amount, admissible_amount, payable_amount,
                item_deductions, item_traces, step_confidences, context, line_item
            )
            if fail_decision:
                return fail_decision
                
        return self._finalize_line_item_decision(
            claimed_amount, admissible_amount, payable_amount,
            item_deductions, item_traces, step_confidences,
            context, line_item, state,
            tool_calls=self._collect_tool_calls()
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
        self._reset_tool_calls()  # Clear accumulator for this line item
        
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
            tool_calls=self._collect_tool_calls(),
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
            passed, trace = self._gate_5_exclusion_validation(context, line_item, step.rule_id, deterministic_only=True)
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
        try:
            result = self.semantic_agent.execute_semantic_rule(
                step.rule_id,
                step.semantic_prompt,
                rule_type
            )
            passed = result.passed
            evaluation_status = "PASSED" if passed else "FAILED"
            reason = result.reason
            confidence = result.confidence
            raw_llm_resp = result.raw_response if isinstance(getattr(result, "raw_response", None), str) else None
        except Exception as semantic_err:
            passed = False
            evaluation_status = "PENDING_REVIEW"
            reason = f"Semantic agent failed/timed out: {semantic_err}"
            confidence = 0.0
            raw_llm_resp = None
        
        # Create trace
        trace = DecisionTrace(
            step=_STEP_LOCAL.current_step,
            rule_id=step.rule_id,
            rule_name=step.rule_name,
            gate=step.gate.value if hasattr(step.gate, "value") else str(step.gate),
            inputs={"semantic_prompt": step.semantic_prompt[:200] if step.semantic_prompt else ""},
            evaluation=evaluation_status,
            reason=reason,
            confidence=confidence,
            raw_llm_response=raw_llm_resp,
        )
        
        return passed, trace, None

    def _execute_step_core(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail], bool]:
        """
        Core decision logic for hybrid rule execution.
        Executes deterministic check and determines if semantic check is required.
        """
        det_passed, det_trace, det_deduction = self._execute_deterministic_step(
            step, line_item, context, state
        )
        
        is_hard_decision = det_trace.evaluation in ("PASSED", "FAILED", "EXCLUSION_ACTIVE", "NOT_APPLICABLE")
        needs_semantic = (
            not is_hard_decision 
            or det_trace.evaluation in ("AMBIGUOUS", "UNCERTAIN") 
            or det_trace.confidence == 0.0
        )
        
        return det_passed, det_trace, det_deduction, needs_semantic

    def _resolve_hybrid_decision(
        self,
        det_passed: bool,
        det_trace: DecisionTrace,
        det_deduction: Optional[DeductionDetail],
        sem_passed: bool,
        sem_trace: DecisionTrace
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Resolve final decision from deterministic and semantic inputs based on confidence"""
        if sem_trace.confidence > det_trace.confidence:
            return sem_passed, sem_trace, det_deduction
        return det_passed, det_trace, det_deduction

    def _execute_hybrid_step(
        self,
        step: ExecutionStep,
        line_item,
        context: ClaimContext,
        state: PerClaimState
    ) -> Tuple[bool, DecisionTrace, Optional[DeductionDetail]]:
        """Execute hybrid rule (deterministic + semantic)"""
        det_passed, det_trace, det_deduction, needs_semantic = self._execute_step_core(
            step, line_item, context, state
        )
        
        if needs_semantic:
            sem_passed, sem_trace, _ = self._execute_semantic_step(
                step, line_item, context, state
            )
            return self._resolve_hybrid_decision(
                det_passed, det_trace, det_deduction, sem_passed, sem_trace
            )
        
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
        policy_start = self._coerce_to_date(context.policy.policy_start_date)
        member_addition = self._coerce_to_date(context.member.date_of_addition) if getattr(context.member, "date_of_addition", None) else None
        inception_date = max(policy_start, member_addition) if member_addition else policy_start
        today = self._coerce_to_date(line_item.admission_date or line_item.expense_date)
        months_since_inception = (today.year - inception_date.year) * 12 + (today.month - inception_date.month)
        continuous_months = months_since_inception + context.porting.waiting_period_credit_months
        admission_date = today
        
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
            confidence=1.0
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


    async def _execute_plan_async(
        self,
        plan: ExecutionPlan,
        line_item,
        context: ClaimContext,
        state: PerClaimState,
        on_trace: Optional[Callable[[DecisionTrace], None]] = None
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

            # Wrap each step so on_trace fires the instant that step completes,
            # rather than waiting for every step in the layer to finish.
            # The wrapper returns the (passed, trace, deduction) tuple unchanged
            # so _process_step_result can still run sequentially afterward.
            async def _step_and_emit(
                step=None,
                _on_trace=on_trace,
            ):
                result = await self._execute_step_async(step, line_item, context, state)
                _passed, _trace, _deduction = result
                if _on_trace:
                    _on_trace(_trace)
                return result

            tasks = [_step_and_emit(step=s) for s in layer_steps]
            results = await asyncio.gather(*tasks)

            # Process results sequentially (admissible/payable are stateful)
            for step, (passed, trace, deduction) in zip(layer_steps, results):
                admissible_amount, payable_amount, fail_decision = self._process_step_result(
                    step, passed, trace, deduction, claimed_amount, admissible_amount, payable_amount,
                    item_deductions, item_traces, step_confidences, context, line_item
                )
                if fail_decision:
                    return fail_decision

                    
        return self._finalize_line_item_decision(
            claimed_amount, admissible_amount, payable_amount,
            item_deductions, item_traces, step_confidences,
            context, line_item, state
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
        det_passed, det_trace, det_deduction, needs_semantic = self._execute_step_core(
            step, line_item, context, state
        )
        
        if needs_semantic:
            sem_passed, sem_trace, _ = await asyncio.to_thread(
                self._execute_semantic_step, step, line_item, context, state
            )
            self.semantic_calls += 1
            return self._resolve_hybrid_decision(
                det_passed, det_trace, det_deduction, sem_passed, sem_trace
            )
            
        return det_passed, det_trace, det_deduction

