import logging
_logger = logging.getLogger("claims_adjudication_pipeline")
from typing import List, Tuple, Optional, Any, Dict
from datetime import datetime, timezone, date
import time
import asyncio
import threading

from pipeline_modules.shared import _STEP_LOCAL

from schemas import (
    ClaimContext, ClaimDecision, LineItemDecision, DeductionDetail,
    DecisionTrace, PerClaimState, DeductionBreakdown, SIWaterfallBreakdown,
    ToolCallTrace
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


class PipelineHelpersMixin:
    # Class variables
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

    # Class variables
    _MODERN_TREATMENT_SUBLIMIT_FRACTION: float = 0.50  # 50% of base SI

    # Class variables
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
        """Invoke a mathematical calculator tool, log inputs/outputs, and emit a ToolCallTrace."""
        t_start = time.perf_counter()
        error_msg: Optional[str] = None
        result: Any = None
        success = True
        result_summary = ""

        try:
            result = tool_func(**kwargs)
            # Build a compact summary of the most useful numeric fields in the result
            if hasattr(result, "__dict__"):
                numeric_fields = {
                    k: v for k, v in vars(result).items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                }
                result_summary = ", ".join(
                    f"{k}={v:.2f}" for k, v in list(numeric_fields.items())[:5]
                ) or str(result)
            else:
                result_summary = str(result)
        except Exception as exc:
            success = False
            error_msg = f"{type(exc).__name__}: {exc}"
            result_summary = f"FAILED: {error_msg}"
            _logger.error(
                "Tool call FAILED | claim=%s | tool=%s | error=%s",
                context.claim_id, tool_name, error_msg
            )

        duration_ms = (time.perf_counter() - t_start) * 1000.0
        session = telemetry_context.get()
        if session:
            session.record_tool_latency(tool_name, duration_ms)

        # Emit ToolCallTrace and append to the current-line-item accumulator
        trace = ToolCallTrace(
            tool_name=tool_name,
            arguments={k: (str(v) if not isinstance(v, (bool, int, float, str, type(None))) else v)
                       for k, v in kwargs.items()},
            result_summary=result_summary,
            success=success,
            error_message=error_msg,
        )
        if not hasattr(self, "_current_tool_calls"):
            self._current_tool_calls: List[ToolCallTrace] = []
        self._current_tool_calls.append(trace)

        AgentReasoningLogger.log_tool_call(
            claim_id=context.claim_id,
            line_item_id=line_item.line_item_id if line_item else None,
            tool_name=tool_name,
            arguments=kwargs,
            output=result
        )

        if not success:
            raise RuntimeError(error_msg)  # Re-raise so the calling gate can handle it
        return result

    def _reset_tool_calls(self) -> None:
        """Clear the per-line-item tool call accumulator before processing each line item."""
        self._current_tool_calls: List[ToolCallTrace] = []

    def _collect_tool_calls(self) -> List[ToolCallTrace]:
        """Return and clear the accumulated ToolCallTrace list."""
        traces = list(getattr(self, "_current_tool_calls", []))
        self._current_tool_calls = []
        return traces

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

