"""
Pydantic v2 Data Models for Claims Auto-Adjudication System
Implements ClaimContext (input) and ClaimDecision (output) schemas
Aligned with Technical Solution Document Section 4.1 and 4.7
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from decimal import Decimal


# ============================================================================
# INPUT MODELS - Claim Context Assembly (Section 4.1)
# ============================================================================

class PolicyData(BaseModel):
    """Policy-level data from Policy API"""
    policy_id: str
    product_code: str = "R3"  # ReAssure 3.0
    variant: Literal["Classic", "Select", "Elite"]
    policy_start_date: datetime
    policy_end_date: datetime
    base_sum_insured: float
    status: Literal["Active", "Lapsed", "Cancelled"]
    premium_paid: bool
    grace_period_active: bool = False
    policy_type: Literal["individual", "floater"] = "individual"
    policy_term_years: int = 1
    
    # Optional benefits configuration
    co_payment_percent: Optional[float] = None
    annual_aggregate_deductible: Optional[float] = None
    room_category_entitled: str = "General Ward"  # Classic default
    
    # Riders and optional benefits opted
    borderless_opted: bool = False
    borderless_specific_illness_opted: bool = False
    unlimited_si_opted: bool = False
    modern_treatments_plus_opted: bool = False
    air_ambulance_plus_opted: bool = False
    heads_up_opted: bool = False
    tiered_network_opted: bool = False

    # Financial configuration — populated by Policy API or UI config screen.
    # When None at runtime, the pipeline emits NOT_APPLICABLE and routes to ASSISTED_REVIEW
    # rather than fabricating a deduction from a hardcoded fallback.
    room_rent_limit: Optional[float] = None
    # INR ceiling on daily room rent. None = not yet configured.
    # Examples: 3000.0 (Single Private Room, Select); None (unlimited / not applicable).

    hospital_daily_cash_amount: Optional[float] = None
    # Daily cash benefit in INR per policy schedule.
    # None = benefit not opted or not configured by Policy API.

    pa_sum_insured: Optional[float] = None
    # Personal Accident sum insured (may differ from base health SI).
    # None = PA benefit not opted or not configured.

    personal_waiting_period_months: int = 0
    # Insurer-imposed personal waiting period (R3_EXCL_017), 0–48 months.
    # 0 = not imposed. Set by underwriting at policy issuance.



class MemberData(BaseModel):
    """Member-level data from Member API"""
    member_id: str
    policy_id: str
    name: str
    age: int
    entry_age: int
    relationship: Literal["Self", "Spouse", "Child", "Parent", "Parent-in-law"]
    date_of_addition: datetime
    ped_declarations: List[str] = Field(default_factory=list)
    eligibility_active: bool = True


class ClaimsHistoryData(BaseModel):
    """Claims history from Claims History API"""
    policy_id: str
    member_id: str
    prior_claims_count: int = 0
    total_utilized_si: float = 0.0
    last_claim_date: Optional[datetime] = None
    prior_exclusions_triggered: List[str] = Field(default_factory=list)
    claim_free_years: int = 0


class PortingMigrationData(BaseModel):
    """Porting/Migration credits from Porting API"""
    policy_id: str
    porting_applicable: bool = False
    prior_coverage_months: int = 0
    waiting_period_credit_months: int = 0
    moratorium_eligible: bool = False


class NetworkData(BaseModel):
    """Provider network status from Network API"""
    provider_id: str
    provider_name: str
    provider_type: Literal["Network", "Non-Network", "Excluded"]
    tiered_network_member: bool = False
    heads_up_recommended: bool = False


class BenefitBalanceData(BaseModel):
    """Remaining benefit balances from Benefit Balance API"""
    policy_id: str
    base_si_remaining: float
    booster_plus_remaining: float
    reassure_forever_pool: float = 0.0
    cash_bag_plus_wallet: float = 0.0
    hospital_cash_days_used: int = 0
    deductible_consumed_ytd: float = 0.0


class LifetimeStateData(BaseModel):
    """Lifetime state flags from Lifetime State API"""
    policy_id: str
    
    # ReAssure Forever state
    reassure_forever_triggered: bool = False
    reassure_forever_triggered_date: Optional[datetime] = None
    reassure_forever_triggered_claim_id: Optional[str] = None
    
    # Lock the Clock state
    lock_the_clock_age_locked: bool = True
    lock_the_clock_entry_age: int
    lock_the_clock_unlocked_date: Optional[datetime] = None
    lock_the_clock_current_premium_age: int
    
    # Booster+ state
    booster_plus_accumulated: float = 0.0
    booster_plus_last_updated: Optional[datetime] = None
    
    # One-time benefit flags
    convalescence_claimed: bool = False
    critical_illness_claimed: bool = False
    critical_illness_type: Optional[str] = None


class EndorsementData(BaseModel):
    """Mid-term policy endorsements from Endorsement API"""
    endorsement_id: str
    policy_id: str
    endorsement_type: Literal[
        "MemberAddition", "MemberDeletion", "SIEnhancement", 
        "SIReduction", "RiderAddition", "PlanUpgrade", 
        "IndividualToFloater", "FloaterSplit"
    ]
    effective_date: datetime
    details: Dict[str, Any] = Field(default_factory=dict)


class LineItemData(BaseModel):
    """Individual claim line item"""
    line_item_id: str
    description: str
    claimed_amount: float
    expense_date: datetime
    
    # Categorization
    benefit_bucket: Literal[
        "Expenses in reaching a Hospital",
        "Expenses during Hospitalization",
        "Expenses before and after hospitalization",
        "Home Care / Domiciliary Treatment",
        "Organ Donor",
        "Hospital Daily Cash",
        "Personal Accident",
        "Other"
    ]
    
    # Hospitalization context (if applicable)
    admission_date: Optional[datetime] = None
    discharge_date: Optional[datetime] = None
    hospitalization_hours: Optional[float] = None
    
    # Room rent details (if applicable)
    actual_room_rent: Optional[float] = None
    room_category_claimed: Optional[str] = None
    
    # Treatment details
    treatment_type: str = "Allopathic"
    condition_diagnosed: str
    accident_related: bool = False
    emergency: bool = False

    # Room rent expense breakdown — sourced from hospital bill itemisation.
    # Required for accurate pro-rata calculation (Tool 2).
    # If None, pro-rata cannot be computed and the claim routes to ASSISTED_REVIEW.
    room_charges: Optional[float] = None
    nursing_charges: Optional[float] = None
    medical_practitioner_fees: Optional[float] = None
    ot_charges: Optional[float] = None

    # Home Care / Domiciliary Treatment preconditions (R3_BEN_007).
    # All three must be True for the Home Care benefit to be payable.
    doctor_advised: bool = False
    continuous_treatment: bool = False
    daily_monitoring_chart: bool = False



class ClaimContext(BaseModel):
    """
    Complete claim context assembled by Context Builder (Section 4.1)
    Single source of truth for adjudication decision
    """
    claim_id: str
    claim_received_at: datetime
    
    # Assembled data from external APIs
    policy: PolicyData
    member: MemberData
    history: ClaimsHistoryData
    porting: PortingMigrationData
    network: NetworkData
    benefit_balance: BenefitBalanceData
    lifetime_state: LifetimeStateData
    endorsements: List[EndorsementData] = Field(default_factory=list)
    
    # Line items to adjudicate
    line_items: List[LineItemData]
    
    # Metadata
    product_json_version: str = "R3_v2.1_2025-01-15"
    context_assembled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# OUTPUT MODELS - Claim Decision (Section 4.7)
# ============================================================================

class DeductionDetail(BaseModel):
    """Individual deduction applied to a line item"""
    deduction_type: Literal[
        "room_pro_rata", "co_payment", "deductible", 
        "non_payable_items", "si_cap", "sublimit", 
        "prolonged_hosp_penalty", "heads_up_penalty", 
        "tiered_network_penalty", "room_copay_penalty"
    ]
    amount: float
    rule_id: str
    reason: str
    calculation_details: Dict[str, Any] = Field(default_factory=dict)


class DecisionTrace(BaseModel):
    """Audit trail entry for each rule evaluation"""
    step: int
    rule_id: str
    rule_name: str
    gate: Literal[
        "policy_validation", "member_validation", 
        "coverage_validation", "waiting_period_validation",
        "exclusion_validation", "financial_computation", "state_update"
    ]
    inputs: Dict[str, Any]
    evaluation: Literal[
        "PASSED", "FAILED", "NOT_APPLICABLE",
        "EXCLUSION_ACTIVE", "DEDUCTION_APPLIED",
        "PENDING_REVIEW", "ASSISTED_REVIEW"    # Issue 23: these are emitted by pipeline routing
    ]
    reason: str
    confidence: float = 1.0
    source_section: Optional[str] = None
    source_page: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LineItemDecision(BaseModel):
    """Decision for a single line item"""
    line_item_id: str
    description: str
    claimed_amount: float
    admissible_amount: float
    payable_amount: float
    
    decision: Literal["APPROVED", "PARTIALLY_APPROVED", "REJECTED", "ASSISTED_REVIEW", "PENDING_REVIEW"]
    
    deductions: List[DeductionDetail] = Field(default_factory=list)
    decision_trace: List[DecisionTrace] = Field(default_factory=list)
    
    confidence_score: float = 1.0
    manual_review_required: bool = False
    review_reason: Optional[str] = None


class SIWaterfallBreakdown(BaseModel):
    """Breakdown of SI consumption across pools"""
    amount_from_base_si: float = 0.0
    amount_from_booster: float = 0.0
    amount_from_forever: float = 0.0
    total_paid: float = 0.0
    shortfall: float = 0.0
    
    # Updated balances after claim
    updated_base_si: float
    updated_booster: float
    updated_forever_pool: float


class DeductionBreakdown(BaseModel):
    """Aggregated deduction breakdown at claim level"""
    room_pro_rata: float = 0.0
    co_payment: float = 0.0
    deductible: float = 0.0
    non_payable_items: float = 0.0
    sublimits: float = 0.0
    penalties: float = 0.0
    si_cap: float = 0.0


class ClaimDecision(BaseModel):
    """
    Final claim adjudication decision (Section 4.7)
    Produced by Decision Composer after all gates execute
    """
    claim_id: str
    claim_decision: Literal["APPROVED", "PARTIALLY_APPROVED", "REJECTED", "ASSISTED_REVIEW", "PENDING_REVIEW"]
    
    # Financial summary
    total_claimed: float
    total_admissible: float
    total_payable: float
    total_deductions: float
    
    # Breakdown
    deduction_breakdown: DeductionBreakdown
    si_waterfall_breakdown: SIWaterfallBreakdown
    
    # Line item decisions
    line_items: List[LineItemDecision]
    
    # Full audit trail
    decision_trace: List[DecisionTrace] = Field(default_factory=list)
    
    # Confidence and routing
    confidence_score: float = 1.0
    manual_review_required: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    
    # PAS submission payload
    pas_submission_payload: Dict[str, Any] = Field(default_factory=dict)
    
    # Metadata
    decision_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processing_duration_ms: Optional[float] = None


# ============================================================================
# INTERMEDIATE MODELS - Per-Claim Running State (Section 4.6)
# ============================================================================

class PerClaimState(BaseModel):
    """
    Running state maintained during single claim processing
    Tracks intermediate values and cumulative deductions
    """
    claim_id: str
    
    # Running financial state
    running_admissible_amount: float = 0.0
    running_payable_amount: float = 0.0
    running_deductions: float = 0.0
    
    # Gate completion flags
    policy_validation_passed: bool = False
    member_validation_passed: bool = False
    coverage_validation_passed: bool = False
    waiting_period_passed: bool = False
    exclusion_passed: bool = False
    financial_computation_complete: bool = False
    
    # Intermediate values for calculators
    room_pro_rata_ratio: float = 1.0
    copay_percent_total: float = 0.0
    deductible_applied_this_claim: float = 0.0
    
    # Flags for penalties and special conditions
    prolonged_hosp_penalty_triggered: bool = False
    heads_up_penalty_triggered: bool = False
    tiered_network_penalty_triggered: bool = False
    room_copay_triggered: bool = False
    
    # Confidence tracking
    overall_confidence: float = 1.0
    low_confidence_flags: List[str] = Field(default_factory=list)
    
    # Waterfall allocations
    amount_from_base_si: float = 0.0
    amount_from_booster: float = 0.0
    amount_from_forever: float = 0.0
    
    # State update transitions
    lock_the_clock_age_unlocked: bool = False
