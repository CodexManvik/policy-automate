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


class ValidationGatesMixin:
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

