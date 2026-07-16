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


class StateGateMixin:
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
                context.lifetime_state.booster_plus_claim_free_years += 1
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
                        "variant": context.policy.variant,
                        "claim_free_years_count": context.lifetime_state.booster_plus_claim_free_years
                    },
                    evaluation="PASSED",
                    reason=(
                        f"Booster+ accumulated at claim-free renewal: "
                        f"INR {booster_result.growth_amount:.2f} added. "
                        f"New balance: INR {booster_result.booster_plus_new:.2f}. "
                        f"Consecutive claim-free years: {context.lifetime_state.booster_plus_claim_free_years}."
                    ),
                    source_section="4.6, R3_SUM_004"
                ))
        elif is_renewal_simulation:
            # Renewal event with claim paid in this year — reset consecutive count
            context.lifetime_state.booster_plus_claim_free_years = 0
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
                "is_claim_free_renewal": is_claim_free_renewal,
                "booster_plus_claim_free_years": context.lifetime_state.booster_plus_claim_free_years
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

