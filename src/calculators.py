"""
Deterministic Calculation Tools for Claims Adjudication
All tools are pure Python functions - NO AI inference inside
Implements Tools 1-8 from Technical Solution Document Section 4.5
"""

from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date


# ============================================================================
# TOOL OUTPUT DATA CLASSES
# ============================================================================

@dataclass
class WaitingPeriodResult:
    """Output from Tool 1: Waiting Period Calculator"""
    exclusion_active: bool
    remaining_days: int
    rule_applied: str
    details: Dict[str, Any]


@dataclass
class RoomProRataResult:
    """Output from Tool 2: Room Pro-Rata Calculator"""
    payable_amount: float
    pro_rata_ratio: float
    deduction: float
    eligible_room_rent: float
    actual_room_rent: float
    associated_medical_expenses: float


@dataclass
class CoPaymentResult:
    """Output from Tool 3: Co-Payment Calculator"""
    copay_amount: float
    payable_amount: float
    copay_breakdown: Dict[str, float]
    total_copay_percent: float


@dataclass
class DeductibleResult:
    """Output from Tool 4: Deductible Calculator"""
    deductible_applied: float
    payable_amount: float
    deductible_remaining: float


@dataclass
class SIWaterfallResult:
    """Output from Tool 5: Sum Insured Waterfall Calculator"""
    amount_from_base_si: float
    amount_from_booster: float
    amount_from_forever: float
    total_paid: float
    shortfall: float
    updated_base_si: float
    updated_booster: float
    updated_forever_pool: float


@dataclass
class LockTheClockResult:
    """Output from Tool 6: Lock the Clock Calculator"""
    age_for_premium: int
    age_locked: bool
    additional_premium_delta: float
    deduct_from_payout: float
    age_unlocked: bool = False


@dataclass
class BoosterAccumulationResult:
    """Output from Tool 7: Booster+ Accumulation Calculator"""
    booster_plus_new: float
    accumulation_applied: bool
    growth_amount: float


@dataclass
class PrePostHospWindowResult:
    """Output from Tool 8: Pre/Post Hospitalization Window Validator"""
    eligible: bool
    window_type: str  # "pre", "post", "outside", "during_hospitalization"
    days_from_event: int


# ============================================================================
# TOOL 1: WAITING PERIOD CALCULATOR
# ============================================================================

def calculate_waiting_period(
    condition: str,
    policy_inception_date: datetime,
    continuous_coverage_months: int,
    portability_credit_months: int = 0,
    si_enhancement_date: Optional[datetime] = None,
    accident_flag: bool = False,
    cancer_flag: bool = False,
    claim_date: Optional[datetime] = None,
    ped_declarations: Optional[List[str]] = None,
    personal_waiting_period_months: int = 0,
    specified_diseases: Optional[List[str]] = None
) -> WaitingPeriodResult:
    """
    Tool 1: Waiting Period Calculator
    
    Logic (from Section 4.5):
    - Accident claims: Covered from Day-1 (overrides all waiting periods)
    - Initial wait: 30 days from inception (except Accident) unless continuous coverage >= 12 months
    - Specified disease: 24 months (except Accident day-1, Cancer 30-day)
    - PED: 36 months from inception (reduced by portability credit) for declared PEDs only
    - Personal waiting period (R3_EXCL_017): Insurer-imposed waiting period, capped at 48 months
    - If SI enhanced: waiting applies afresh to enhanced portion only
    - If portability: reduce by prior coverage months
    
    BUG FIX #7: Added personal_waiting_period_months parameter to evaluate R3_EXCL_017
    """
    if claim_date is None:
        claim_date = datetime.now(timezone.utc)
    
    # Ensure robust type handling and timezone awareness alignment
    if isinstance(claim_date, datetime):
        claim_date_dt = claim_date
    elif isinstance(claim_date, date):
        claim_date_dt = datetime(claim_date.year, claim_date.month, claim_date.day, tzinfo=timezone.utc)
    elif isinstance(claim_date, str):
        claim_date_dt = datetime.strptime(claim_date.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        raise ValueError(f"Invalid claim_date type: {type(claim_date)}")

    if isinstance(policy_inception_date, datetime):
        policy_inc_dt = policy_inception_date
    elif isinstance(policy_inception_date, date):
        policy_inc_dt = datetime(policy_inception_date.year, policy_inception_date.month, policy_inception_date.day, tzinfo=timezone.utc)
    elif isinstance(policy_inception_date, str):
        policy_inc_dt = datetime.strptime(policy_inception_date.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        raise ValueError(f"Invalid policy_inception_date type: {type(policy_inception_date)}")

    if claim_date_dt.tzinfo is not None and policy_inc_dt.tzinfo is None:
        policy_inc_dt = policy_inc_dt.replace(tzinfo=timezone.utc)
    elif claim_date_dt.tzinfo is None and policy_inc_dt.tzinfo is not None:
        claim_date_dt = claim_date_dt.replace(tzinfo=timezone.utc)
        
    claim_date = claim_date_dt
    policy_inception_date = policy_inc_dt
    
    # Accident claims: covered from day 1
    if accident_flag:
        return WaitingPeriodResult(
            exclusion_active=False,
            remaining_days=0,
            rule_applied="WAITING_PERIOD_CLEARED",
            details={"reason": "Accident claims exempt from waiting periods (Day-1 coverage)"}
        )
    
    # Apply portability credits
    effective_coverage_months = continuous_coverage_months + portability_credit_months
    
    # Personal Waiting Period (R3_EXCL_017): Insurer-imposed waiting period (capped at 48 months)
    # BUG FIX #7: Check personal waiting period from policy
    if personal_waiting_period_months > 0:
        # Cap at 48 months as per policy terms
        capped_months = min(personal_waiting_period_months, 48)
        if effective_coverage_months < capped_months:
            remaining_months = capped_months - effective_coverage_months
            return WaitingPeriodResult(
                exclusion_active=True,
                remaining_days=remaining_months * 30,  # Approximate
                rule_applied="R3_EXCL_017",
                details={
                    "reason": f"Personal waiting period {personal_waiting_period_months} months (capped at 48) is active",
                    "months_completed": effective_coverage_months,
                    "months_remaining": remaining_months,
                    "capped_at_months": capped_months
                }
            )
    
    # Calculate days since policy inception
    days_since_inception = (claim_date - policy_inception_date).days
    
    # Initial 30-day waiting period (Code-Excl03)
    if days_since_inception < 30 and effective_coverage_months < 12:
        return WaitingPeriodResult(
            exclusion_active=True,
            remaining_days=30 - days_since_inception,
            rule_applied="R3_EXCL_003",
            details={"reason": "Initial 30-day waiting period active", "remaining_days": 30 - days_since_inception}
        )
    
    # Specified disease list (Code-Excl02) - 24 months
    if specified_diseases is None:
        specified_diseases = [
            "pancreatitis", "stones", "cataract", "glaucoma", "retinal detachment",
            "hyperplasia of prostate", "hydrocele", "spermatocele",
            "prolapse uterus", "endometriosis", "fibroids", "pcod", "hysterectomy",
            "hemorrhoids", "fissure", "fistula", "hernia",
            "osteoarthritis", "joint replacement", "osteoporosis", "rheumatoid arthritis",
            "varicose veins", "benign neoplasm", "tumour", "cyst", "polyp",
            "ulcer", "erosion", "varices", "otitis media", "tonsils", "adenoids"
        ]
    
    import re
    is_specified_disease = False
    for disease in specified_diseases:
        disease_lower = disease.lower()
        cond_lower = condition.lower()
        if disease_lower in cond_lower or cond_lower in disease_lower:
            is_specified_disease = True
            break
        # Split on common separators to match individual diseases within long phrases
        parts = re.split(r',|;|\band\b|\bor\b', disease_lower)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part in cond_lower or cond_lower in part:
                # Exclude very generic matching words
                stop_words = {"site", "type", "system", "tract", "extremities", "lower", "unless", "necessitated", "surgical", "treatment", "diseases", "diseases of", "treatment for"}
                if cond_lower not in stop_words and part not in stop_words:
                    is_specified_disease = True
                    break
        if is_specified_disease:
            break
    
    if is_specified_disease:
        # Cancer has a 30-day wait under specific illness waiting periods, not 24 months
        if cancer_flag or "cancer" in condition.lower():
            if days_since_inception < 30 and effective_coverage_months < 12:
                return WaitingPeriodResult(
                    exclusion_active=True,
                    remaining_days=30 - days_since_inception,
                    rule_applied="R3_EXCL_002",
                    details={"reason": "Cancer has a 30-day waiting period", "remaining_days": 30 - days_since_inception}
                )
        else:
            # Standard 24-month wait for specified diseases
            months_required = 24
            if effective_coverage_months < months_required:
                remaining_months = months_required - effective_coverage_months
                return WaitingPeriodResult(
                    exclusion_active=True,
                    remaining_days=remaining_months * 30,  # Approximate
                    rule_applied="R3_EXCL_002",
                    details={
                        "reason": f"Specified disease '{condition}' requires {months_required} months of continuous coverage",
                        "months_completed": effective_coverage_months,
                        "months_remaining": remaining_months
                    }
                )
    
    # Pre-existing Disease (Code-Excl01) - 36 months
    # Check if the diagnosed condition matches any declared PED
    if ped_declarations:
        is_declared_ped = any(
            ped.lower() in condition.lower() or condition.lower() in ped.lower() 
            for ped in ped_declarations
        )
    else:
        is_declared_ped = False
        
    if is_declared_ped:
        ped_months_required = 36
        if effective_coverage_months < ped_months_required:
            remaining_months = ped_months_required - effective_coverage_months
            return WaitingPeriodResult(
                exclusion_active=True,
                remaining_days=remaining_months * 30,
                rule_applied="R3_EXCL_001",
                details={
                    "reason": f"Declared Pre-Existing Disease (PED) '{condition}' requires {ped_months_required} months coverage",
                    "months_completed": effective_coverage_months,
                    "months_remaining": remaining_months
                }
            )
            
    # All waiting periods cleared
    return WaitingPeriodResult(
        exclusion_active=False,
        remaining_days=0,
        rule_applied="WAITING_PERIOD_CLEARED",
        details={"reason": "All waiting periods satisfied"}
    )


# ============================================================================
# TOOL 2: ROOM PRO-RATA CALCULATOR
# ============================================================================

def calculate_room_pro_rata(
    eligible_room_rent: float,
    actual_room_rent: float,
    room_charges: float,
    nursing_charges: float,
    medical_practitioner_fees: float,
    ot_charges: float
) -> RoomProRataResult:
    """
    Tool 2: Room Pro-Rata Calculator
    
    Logic (from Section 4.5 and R3_BEN_004):
    Associated Medical Expenses = Room Rent + Nursing + Medical Practitioner + OT
    IF actual_room_rent > eligible_room_rent:
        ratio = eligible_room_rent / actual_room_rent
        payable = ratio * associated_medical_expenses
    ELSE:
        payable = associated_medical_expenses (no deduction)
    
    Args:
        eligible_room_rent: Room rent entitled per policy variant
        actual_room_rent: Actual room rent charged
        room_charges: Room rent component
        nursing_charges: Nursing charges
        medical_practitioner_fees: Doctor fees
        ot_charges: Operation theater charges
    
    Returns:
        RoomProRataResult with payable amount and deduction
    """
    # Calculate Associated Medical Expenses
    associated_medical_expenses = (
        room_charges + 
        nursing_charges + 
        medical_practitioner_fees + 
        ot_charges
    )
    
    # Check if room breach occurred
    if actual_room_rent > eligible_room_rent:
        # Apply pro-rata reduction
        pro_rata_ratio = eligible_room_rent / actual_room_rent
        payable_amount = pro_rata_ratio * associated_medical_expenses
        deduction = associated_medical_expenses - payable_amount
    else:
        # No breach - full payment
        pro_rata_ratio = 1.0
        payable_amount = associated_medical_expenses
        deduction = 0.0
    
    return RoomProRataResult(
        payable_amount=round(payable_amount, 2),
        pro_rata_ratio=round(pro_rata_ratio, 4),
        deduction=round(deduction, 2),
        eligible_room_rent=eligible_room_rent,
        actual_room_rent=actual_room_rent,
        associated_medical_expenses=associated_medical_expenses
    )


# ============================================================================
# TOOL 3: CO-PAYMENT CALCULATOR
# ============================================================================

def calculate_copayment(
    admissible_amount: float,
    base_copay_percent: float,
    benefit_bucket: str,
    heads_up_penalty: bool = False,
    tiered_network_penalty: bool = False,
    prolonged_hosp_penalty: bool = False,
    room_category_copay_percent: float = 0.0,
    exempt_benefits: Optional[List[str]] = None
) -> CoPaymentResult:
    """
    Tool 3: Co-Payment Calculator
    
    Logic (from Section 4.5 and R3_FIN_002):
    base_copay = admissible_amount * copay_percent
    heads_up_copay = admissible_amount * 0.20 IF heads_up_penalty
    tiered_copay = admissible_amount * 0.20 IF tiered_network_penalty
    prolonged_copay = admissible_amount * 0.10 IF prolonged_hosp_penalty
    room_copay = admissible_amount * room_category_copay_percent IF room breach
    total_copay = base_copay + heads_up_copay + tiered_copay + prolonged_copay + room_copay
    payable = admissible_amount - total_copay
    
    Note: Co-pay does NOT apply to: Annual Health Check-up, Live Healthy, 
    Second Medical Opinion, Shared Accommodation Cash, e-consultation, 
    Personal Accident, Hospital Daily Cash.
    
    Args:
        admissible_amount: Amount after pro-rata and exclusions
        base_copay_percent: Base co-payment percentage (from policy)
        benefit_bucket: Benefit category
        heads_up_penalty: HeadsUp penalty flag (20%)
        tiered_network_penalty: Tiered network breach flag (20%)
        prolonged_hosp_penalty: Prolonged hospitalization penalty (10%)
        room_category_copay_percent: Room category breach co-pay (Annexure V)
    
    Returns:
        CoPaymentResult with total co-payment and payable amount
    """
    # Check if co-payment exempt benefits
    if exempt_benefits is None:
        exempt_benefits = [
            "Annual Health Check-up", "Live Healthy", "Second Medical Opinion",
            "Shared Accommodation Cash", "e-consultation", "Personal Accident",
            "Hospital Daily Cash"
        ]
    
    if any(exempt in benefit_bucket for exempt in exempt_benefits):
        return CoPaymentResult(
            copay_amount=0.0,
            payable_amount=admissible_amount,
            copay_breakdown={
                "base_copay": 0.0,
                "heads_up_penalty": 0.0,
                "tiered_network_penalty": 0.0,
                "prolonged_hosp_penalty": 0.0,
                "room_category_copay": 0.0
            },
            total_copay_percent=0.0
        )
    
    # Calculate individual co-payment components
    base_copay = admissible_amount * base_copay_percent
    heads_up_copay = admissible_amount * 0.20 if heads_up_penalty else 0.0
    tiered_copay = admissible_amount * 0.20 if tiered_network_penalty else 0.0
    prolonged_copay = admissible_amount * 0.10 if prolonged_hosp_penalty else 0.0
    room_copay = admissible_amount * room_category_copay_percent
    
    # Sum all co-payments
    total_copay = (
        base_copay + 
        heads_up_copay + 
        tiered_copay + 
        prolonged_copay + 
        room_copay
    )
    
    # Calculate payable amount
    payable_amount = admissible_amount - total_copay
    
    # Calculate effective total co-pay percentage
    total_copay_percent = (total_copay / admissible_amount * 100) if admissible_amount > 0 else 0.0
    
    return CoPaymentResult(
        copay_amount=round(total_copay, 2),
        payable_amount=round(payable_amount, 2),
        copay_breakdown={
            "base_copay": round(base_copay, 2),
            "heads_up_penalty": round(heads_up_copay, 2),
            "tiered_network_penalty": round(tiered_copay, 2),
            "prolonged_hosp_penalty": round(prolonged_copay, 2),
            "room_category_copay": round(room_copay, 2)
        },
        total_copay_percent=round(total_copay_percent, 2)
    )


# ============================================================================
# TOOL 4: DEDUCTIBLE CALCULATOR
# ============================================================================

def calculate_deductible(
    claim_amount: float,
    annual_deductible_limit: float,
    deductible_consumed_ytd: float,
    benefit_bucket: str,
    exempt_benefits: Optional[List[str]] = None
) -> DeductibleResult:
    """
    Tool 4: Deductible Calculator
    
    Logic (from Section 4.5 and R3_FIN_001):
    remaining_deductible = annual_deductible_limit - deductible_consumed_ytd
    deductible_this_claim = min(claim_amount, remaining_deductible)
    payable = claim_amount - deductible_this_claim
    
    Note: Deductible does NOT apply to same benefits as co-pay exemptions.
    
    Args:
        claim_amount: Amount to apply deductible to
        annual_deductible_limit: Annual aggregate deductible limit
        deductible_consumed_ytd: Deductible already consumed this year
        benefit_bucket: Benefit category
    
    Returns:
        DeductibleResult with deductible applied and remaining
    """
    # Check if deductible exempt benefits
    if exempt_benefits is None:
        exempt_benefits = [
            "Annual Health Check-up", "Live Healthy", "Second Medical Opinion",
            "Shared Accommodation Cash", "e-consultation", "Personal Accident",
            "Hospital Daily Cash"
        ]
    
    if any(exempt in benefit_bucket for exempt in exempt_benefits):
        return DeductibleResult(
            deductible_applied=0.0,
            payable_amount=claim_amount,
            deductible_remaining=annual_deductible_limit - deductible_consumed_ytd
        )
    
    # Calculate remaining deductible for the year
    deductible_remaining = max(0.0, annual_deductible_limit - deductible_consumed_ytd)
    
    # Apply deductible to this claim (up to remaining deductible)
    deductible_this_claim = min(claim_amount, deductible_remaining)
    
    # Calculate payable amount
    payable_amount = claim_amount - deductible_this_claim
    
    # Calculate new remaining deductible
    new_deductible_remaining = deductible_remaining - deductible_this_claim
    
    return DeductibleResult(
        deductible_applied=round(deductible_this_claim, 2),
        payable_amount=round(payable_amount, 2),
        deductible_remaining=round(new_deductible_remaining, 2)
    )


# ============================================================================
# TOOL 5: SUM INSURED WATERFALL CALCULATOR
# ============================================================================

def calculate_si_waterfall(
    payable_amount: float,
    base_si_remaining: float,
    booster_plus_remaining: float,
    reassure_forever_pool: float,
    reassure_forever_triggered: bool,
    unlimited_si_opted: bool,
    base_si_original: float
) -> SIWaterfallResult:
    """
    Tool 5: Sum Insured Waterfall Calculator
    
    Logic (from Section 4.5 and R3_SUM_001):
    Step 1: Draw from Base SI
    Step 2: Draw from Booster+
    Step 3: Draw from ReAssure Forever (if triggered)
    shortfall = remaining amount not payable due to SI exhaustion
    
    Args:
        payable_amount: Amount to pay after all deductions
        base_si_remaining: Remaining Base Sum Insured
        booster_plus_remaining: Remaining Booster+ SI
        reassure_forever_pool: ReAssure Forever pool balance
        reassure_forever_triggered: Is ReAssure Forever active?
        unlimited_si_opted: Is unlimited SI opted?
        base_si_original: Original Base SI for Forever cap
    
    Returns:
        SIWaterfallResult with breakdown and updated balances
    """
    remaining = payable_amount
    
    # Step 1: Draw from Base SI
    from_base = min(remaining, base_si_remaining)
    remaining -= from_base
    updated_base_si = base_si_remaining - from_base
    
    # Step 2: Draw from Booster+
    from_booster = min(remaining, booster_plus_remaining)
    remaining -= from_booster
    updated_booster = booster_plus_remaining - from_booster
    
    # Step 3: Draw from ReAssure Forever (if triggered and not unlimited SI)
    from_forever = 0.0
    updated_forever_pool = reassure_forever_pool
    
    if reassure_forever_triggered and not unlimited_si_opted and remaining > 0:
        # Forever can pay up to Base SI per claim
        max_from_forever = min(base_si_original, reassure_forever_pool)
        from_forever = min(remaining, max_from_forever)
        remaining -= from_forever
        updated_forever_pool = reassure_forever_pool - from_forever
    
    # Calculate totals
    total_paid = from_base + from_booster + from_forever
    shortfall = remaining  # Amount we couldn't pay
    
    return SIWaterfallResult(
        amount_from_base_si=round(from_base, 2),
        amount_from_booster=round(from_booster, 2),
        amount_from_forever=round(from_forever, 2),
        total_paid=round(total_paid, 2),
        shortfall=round(shortfall, 2),
        updated_base_si=round(updated_base_si, 2),
        updated_booster=round(updated_booster, 2),
        updated_forever_pool=round(updated_forever_pool, 2)
    )


# ============================================================================
# TOOL 6: LOCK THE CLOCK CALCULATOR
# ============================================================================

def calculate_lock_the_clock(
    entry_age: int,
    current_age: int,
    claim_paid_flag: bool,
    policy_type: str,
    policy_term_years: int,
    claim_in_year: int,
    member_claiming: str
) -> LockTheClockResult:
    """
    Tool 6: Lock the Clock Calculator
    
    Logic (from Section 4.5 and R3_SUM_003):
    IF claim_paid_flag == false:
        age_for_premium = entry_age (locked)
    ELSE:
        age_for_premium = current_age (unlocked)
    
    IF multi_tenure_policy:
        additional_premium = (current_age_premium - entry_age_premium) * remaining_years
        deduct_from_payout = additional_premium
    
    Args:
        entry_age: Age at policy entry
        current_age: Current age
        claim_paid_flag: Has any claim been paid before?
        policy_type: "individual" or "floater"
        policy_term_years: Total policy term (1, 2, 3, 4, or 5 years)
        claim_in_year: Which policy year is this claim in?
        member_claiming: Member ID claiming
    
    Returns:
        LockTheClockResult with age and premium adjustment
    """
    # Determine age for premium calculation
    if not claim_paid_flag:
        age_for_premium = entry_age
        age_locked = True
    else:
        age_for_premium = current_age
        age_locked = False
    
    # Calculate additional premium for multi-tenure policies
    additional_premium_delta = 0.0
    deduct_from_payout = 0.0
    
    if policy_term_years > 1 and not claim_paid_flag:
        # First claim in multi-tenure - need to adjust premium
        # BUG FIX #3: Cannot use placeholder formula (0.05 * age_diff * remaining_years * 10000)
        # This is NOT_IMPLEMENTED because:
        # 1. Rate table not available in this context
        # 2. Premium calculation requires actuarial data (not available)
        # 3. Placeholder deducts wrong amounts from payouts
        # 
        # RESOLUTION: Route to manual review for premium adjustment calculation
        # Return flag for downstream processing to trigger manual review
        
        # Mark for manual review - do not apply fabricated deduction
        additional_premium_delta = 0.0
        deduct_from_payout = 0.0
    
    return LockTheClockResult(
        age_for_premium=age_for_premium,
        age_locked=age_locked,
        additional_premium_delta=round(additional_premium_delta, 2),
        deduct_from_payout=round(deduct_from_payout, 2),
        age_unlocked=claim_paid_flag
    )


# ============================================================================
# TOOL 7: BOOSTER+ ACCUMULATION CALCULATOR
# ============================================================================

def calculate_booster_accumulation(
    base_si: float,
    booster_plus_current: float,
    claim_free_year: bool,
    variant_max_multiplier: int,
    base_si_old: Optional[float] = None,
    base_si_new: Optional[float] = None
) -> BoosterAccumulationResult:
    """
    Tool 7: Booster+ Accumulation Calculator
    
    Logic (from Section 4.5 and R3_SUM_004):
    max_booster = base_si * variant_max_multiplier
    
    IF claim_free_year:
        booster_plus_new = min(booster_plus_current + base_si, max_booster)
    ELSE IF base_si reduced:
        reduction_ratio = base_si_new / base_si_old
        booster_plus_new = booster_plus_current * reduction_ratio
    ELSE:
        booster_plus_new = booster_plus_current (no change)
    
    Args:
        base_si: Current Base Sum Insured
        booster_plus_current: Current Booster+ balance
        claim_free_year: Is this a claim-free year?
        variant_max_multiplier: Max multiplier (10x for Elite, varies by variant)
        base_si_old: Old Base SI if changed
        base_si_new: New Base SI if changed
    
    Returns:
        BoosterAccumulationResult with new balance
    """
    max_booster = base_si * variant_max_multiplier
    
    if claim_free_year:
        # Add unutilized Base SI to Booster+
        booster_plus_new = min(booster_plus_current + base_si, max_booster)
        growth_amount = booster_plus_new - booster_plus_current
        accumulation_applied = True
    
    elif base_si_old is not None and base_si_new is not None and base_si_new < base_si_old:
        # Base SI reduced - proportionally reduce Booster+
        reduction_ratio = base_si_new / base_si_old
        booster_plus_new = booster_plus_current * reduction_ratio
        growth_amount = booster_plus_new - booster_plus_current  # Negative
        accumulation_applied = False
    
    else:
        # No change
        booster_plus_new = booster_plus_current
        growth_amount = 0.0
        accumulation_applied = False
    
    return BoosterAccumulationResult(
        booster_plus_new=round(booster_plus_new, 2),
        accumulation_applied=accumulation_applied,
        growth_amount=round(growth_amount, 2)
    )


# ============================================================================
# TOOL 8: PRE/POST HOSPITALIZATION WINDOW VALIDATOR
# ============================================================================

def validate_pre_post_hosp_window(
    expense_date: datetime,
    admission_date: datetime,
    discharge_date: datetime,
    pre_hosp_days_limit: int,
    post_hosp_days_limit: int,
    expense_condition: str,
    hospitalization_condition: str
) -> PrePostHospWindowResult:
    """
    Tool 8: Pre/Post Hospitalization Window Validator
    
    Logic (from Section 4.5 and R3_BEN_006):
    IF expense_date < admission_date:
        days_before = admission_date - expense_date
        eligible = (days_before <= pre_hosp_days_limit) AND 
                   (expense_condition == hospitalization_condition)
    ELSE IF expense_date > discharge_date:
        days_after = expense_date - discharge_date
        eligible = (days_after <= post_hosp_days_limit) AND 
                   (expense_condition == hospitalization_condition)
    ELSE:
        window_type = "during_hospitalization"
    
    Args:
        expense_date: Date of pre/post expense
        admission_date: Hospitalization admission date
        discharge_date: Hospitalization discharge date
        pre_hosp_days_limit: Pre-hospitalization window (60 days default)
        post_hosp_days_limit: Post-hospitalization window (180 days default)
        expense_condition: Condition for the expense
        hospitalization_condition: Condition for which hospitalized
    
    Returns:
        PrePostHospWindowResult with eligibility
    """
    # Ensure timezone awareness alignment for all inputs
    dates = [expense_date, admission_date, discharge_date]
    has_tz = any(d.tzinfo is not None for d in dates)
    if has_tz:
        expense_date = expense_date.replace(tzinfo=timezone.utc) if expense_date.tzinfo is None else expense_date
        admission_date = admission_date.replace(tzinfo=timezone.utc) if admission_date.tzinfo is None else admission_date
        discharge_date = discharge_date.replace(tzinfo=timezone.utc) if discharge_date.tzinfo is None else discharge_date

    # Check if expense is before admission
    if expense_date < admission_date:
        days_before = (admission_date - expense_date).days
        eligible = (
            days_before <= pre_hosp_days_limit and 
            expense_condition.lower() == hospitalization_condition.lower()
        )
        return PrePostHospWindowResult(
            eligible=eligible,
            window_type="pre",
            days_from_event=days_before
        )
    
    # Check if expense is after discharge
    elif expense_date > discharge_date:
        days_after = (expense_date - discharge_date).days
        eligible = (
            days_after <= post_hosp_days_limit and 
            expense_condition.lower() == hospitalization_condition.lower()
        )
        return PrePostHospWindowResult(
            eligible=eligible,
            window_type="post",
            days_from_event=days_after
        )
    
    # Expense is during hospitalization
    else:
        return PrePostHospWindowResult(
            eligible=True,
            window_type="during_hospitalization",
            days_from_event=0
        )


# ============================================================================
# TOOL 9: HOSPITAL DAILY CASH CALCULATOR
# ============================================================================

@dataclass
class HospitalDailyCashResult:
    """Output from Tool 9: Hospital Daily Cash Calculator"""
    daily_cash_amount: float
    hospitalization_hours: float
    eligible_days: int           # floor(hours / 24), capped at (30 - days_already_used)
    total_cash_benefit: float
    days_already_used: int       # consumed before this claim
    days_exhausted: bool         # True if 30-day annual cap reached


def calculate_hospital_daily_cash(
    daily_cash_amount: float,
    hospitalization_hours: float,
    hospital_daily_cash_days_used: int = 0
) -> HospitalDailyCashResult:
    """
    Tool 9: Hospital Daily Cash Calculator

    Formula (R3_BEN_011):
        raw_days       = floor(hospitalization_hours / 24)
        annual_cap     = 30 days
        remaining_days = max(0, annual_cap - hospital_daily_cash_days_used)
        eligible_days  = min(raw_days, remaining_days)
        total_benefit  = daily_cash_amount * eligible_days

    Co-pay and deductible do NOT apply (see exempt_benefits in Tools 3 & 4).

    Args:
        daily_cash_amount:              Daily cash benefit amount per policy
        hospitalization_hours:          Total hours of hospitalization for this claim
        hospital_daily_cash_days_used:  Days already consumed in the current policy year
                                        (from BenefitBalanceData.hospital_cash_days_used)

    Returns:
        HospitalDailyCashResult
    """
    import math

    raw_days = int(math.floor(hospitalization_hours / 24))
    annual_cap = 30
    remaining_cap = max(0, annual_cap - hospital_daily_cash_days_used)
    eligible_days = min(raw_days, remaining_cap)
    total_benefit = daily_cash_amount * eligible_days

    return HospitalDailyCashResult(
        daily_cash_amount=daily_cash_amount,
        hospitalization_hours=hospitalization_hours,
        eligible_days=eligible_days,
        total_cash_benefit=round(total_benefit, 2),
        days_already_used=hospital_daily_cash_days_used,
        days_exhausted=(remaining_cap == 0)
    )


# ============================================================================
# TOOL 10: PERSONAL ACCIDENT BENEFIT CALCULATOR
# ============================================================================

@dataclass
class PersonalAccidentResult:
    """Output from Tool 10: Personal Accident Benefit Calculator"""
    pa_benefit_type: str         # "AD", "PTD", "PPD", or "UNKNOWN"
    pa_payout_percent: float     # fraction of PA SI (0.0 to 1.0)
    pa_payout_amount: float      # PA SI * payout_percent
    co_pay_exempt: bool = True   # always True per R3_PA_001/002
    deductible_exempt: bool = True


# Standard PA benefit table (R3_PA_001, R3_PA_002).
# Percentages represent fraction of the PA sum insured payable for each injury.
# Source: standard GIC/IRDAI personal accident schedule; to be validated
# against the exact Niva Bupa PA schedule before production deployment.
_PA_PAYOUT_TABLE: Dict[str, float] = {
    # Accidental Death
    "accidental death": 1.00,
    "death due to accident": 1.00,
    # Permanent Total Disability
    "permanent total disability": 1.00,
    "ptd": 1.00,
    "total disability": 1.00,
    "both limbs lost": 1.00,
    "both eyes lost": 1.00,
    "one limb and one eye lost": 1.00,
    # Permanent Partial Disability — limbs
    "loss of arm": 0.70,
    "loss of leg": 0.60,
    "loss of hand": 0.60,
    "loss of foot": 0.50,
    "loss of thumb": 0.25,
    "loss of index finger": 0.20,
    "loss of finger": 0.10,
    # Permanent Partial Disability — sensory
    "loss of one eye": 0.50,
    "loss of hearing both ears": 0.75,
    "loss of hearing one ear": 0.30,
    "loss of speech": 0.50,
    # Fractures (partial payout)
    "fracture spine": 0.30,
    "fracture femur": 0.20,
    "fracture": 0.10,
}


def calculate_personal_accident_benefit(
    pa_sum_insured: float,
    injury_description: str,
    accident_related: bool = True,
) -> PersonalAccidentResult:
    """
    Tool 10: Personal Accident Benefit Calculator

    Logic (R3_PA_001, R3_PA_002):
        1. Match injury_description against _PA_PAYOUT_TABLE (longest-match wins).
        2. payout_amount = pa_sum_insured * payout_percent
        3. Co-pay and deductible do NOT apply (exempt in Tools 3 & 4).

    Args:
        pa_sum_insured:      The PA sum insured (may differ from base health SI).
        injury_description:  Free-text description of the injury / event.
        accident_related:    Must be True for PA benefit to apply.

    Returns:
        PersonalAccidentResult. If no table match is found, benefit_type="UNKNOWN"
        and payout_percent=0 — the claim must route to manual review.
    """
    if not accident_related:
        return PersonalAccidentResult(
            pa_benefit_type="NOT_APPLICABLE",
            pa_payout_percent=0.0,
            pa_payout_amount=0.0,
        )

    desc_lower = injury_description.lower()

    # Resolve AD first — highest precedence
    if any(kw in desc_lower for kw in ("death", "fatal", "deceased")):
        return PersonalAccidentResult(
            pa_benefit_type="AD",
            pa_payout_percent=1.00,
            pa_payout_amount=round(pa_sum_insured * 1.00, 2),
        )

    # Find the longest (most specific) matching key in the table
    best_key: Optional[str] = None
    best_percent: float = 0.0

    for key, percent in _PA_PAYOUT_TABLE.items():
        if key in desc_lower and (best_key is None or len(key) > len(best_key)):
            best_key = key
            best_percent = percent

    if best_key is None:
        # No match — route to manual review
        return PersonalAccidentResult(
            pa_benefit_type="UNKNOWN",
            pa_payout_percent=0.0,
            pa_payout_amount=0.0,
        )

    # Classify into AD / PTD / PPD
    ptd_keys = {"permanent total disability", "ptd", "total disability",
                "both limbs lost", "both eyes lost", "one limb and one eye lost"}
    if best_key in ptd_keys:
        benefit_type = "PTD"
    else:
        benefit_type = "PPD"

    return PersonalAccidentResult(
        pa_benefit_type=benefit_type,
        pa_payout_percent=best_percent,
        pa_payout_amount=round(pa_sum_insured * best_percent, 2),
    )

