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


class FinancialsGateMixin:
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
        room_copay_percent = 0.0
        if line_item.room_category_claimed:
            categories_map = {
                "general ward": 1,
                "general": 1,
                "ward": 1,
                "shared accommodation": 2,
                "shared": 2,
                "single private room": 3,
                "single private": 3,
                "single room": 3,
                "suite": 4,
            }
            entitled_cat = getattr(context.policy, "room_category_entitled", "") or ""
            claimed_cat = getattr(line_item, "room_category_claimed", "") or ""
            ent_level = categories_map.get(entitled_cat.lower().strip(), 0)
            clm_level = categories_map.get(claimed_cat.lower().strip(), 0)
            
            # Category copay penalty only triggers if they stayed in a room above entitlement
            if ent_level > 0 and clm_level > 0 and clm_level > ent_level:
                room_copay_percent = self.product_memory.get_room_copay_percent(
                    variant=context.policy.variant,
                    room_category_claimed=line_item.room_category_claimed
                ) or 0.0

        has_copay = (
            (context.policy.co_payment_percent is not None and context.policy.co_payment_percent > 0) or
            state.heads_up_penalty_triggered or
            state.tiered_network_penalty_triggered or
            state.prolonged_hosp_penalty_triggered or
            room_copay_percent > 0.0
        )

        if has_copay:
            _STEP_LOCAL.current_step += 1
            # Defensive normalization: divide by 100.0 if percentage is > 1.0 (whole number)
            co_payment_percent = context.policy.co_payment_percent or 0.0
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

