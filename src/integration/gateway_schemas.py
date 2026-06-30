"""
Pydantic V2 schemas for external API response payloads.
Represents external systems like Core PAS, Provider Network, and Enterprise Ledgers.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class PolicyApiResponse(BaseModel):
    """Payload representing policy verification response from Core PAS"""
    policy_id: str
    status: str
    product_code: str
    policy_variant: str
    policy_start_date: datetime
    policy_end_date: datetime
    base_sum_insured: float
    optional_riders: List[str] = Field(default_factory=list)
    co_pay_option: Optional[float] = None
    deductible_option: Optional[float] = None
    room_category_entitled: str
    # Financial correctness fields — previously hardcoded in context_builder.py.
    # premium_paid: False for lapsed/cancelled policies; prevents incorrect adjudication.
    premium_paid: bool = True
    # policy_type: "individual" or "floater" — floater uses a shared SI pool.
    policy_type: str = "individual"
    # Daily cash benefit amount per covered day; None means benefit is not opted.
    hospital_daily_cash_amount: Optional[float] = None
    # Personal Accident SI; None means PA cover is not included in this plan.
    pa_sum_insured: Optional[float] = None


class MemberApiResponse(BaseModel):
    """Payload representing member details lookup"""
    member_id: str
    policy_id: str
    name: str = "John Doe"
    age: int
    relationship: str
    date_of_addition: datetime
    ped_declarations: List[str] = Field(default_factory=list)
    eligibility_active: bool


class ClaimsHistoryApiResponse(BaseModel):
    """Payload representing cumulative history of claims for member/policy"""
    policy_id: str
    member_id: str
    prior_claims_count: int
    total_prior_amount_paid: float
    cumulative_exclusions_triggered: List[str] = Field(default_factory=list)
    # YTD aggregate deductible already consumed by prior claims in the current policy year.
    # The pipeline uses this to correctly compute remaining deductible on the current claim
    # rather than re-applying the full deductible amount.  Defaults to 0.0 for backward
    # compatibility with gateway implementations that have not yet added this field.
    deductible_consumed_ytd: float = 0.0
    # Last claim date — used by Booster+ and ReAssure Forever state machines.
    last_claim_date: Optional[datetime] = None
    # Claim-free years — used by Booster+ accumulation calculator.
    claim_free_years: int = 0


class PortingApiResponse(BaseModel):
    """Payload representing porting portability credits"""
    policy_id: str
    is_ported_policy: bool
    waiting_period_credit_months: int
    moratorium_eligible_months: int
    continuous_coverage_months: int


class NetworkApiResponse(BaseModel):
    """Payload representing healthcare provider lookup details"""
    provider_id: str
    hospital_name: str
    network_tier: str  # "Network", "Tiered", or "Excluded"
    heads_up_recommended: bool


class BenefitBalanceApiResponse(BaseModel):
    """Payload representing active sum insured and benefit balances"""
    policy_id: str
    member_id: str
    base_si_remaining: float
    booster_plus_remaining: float
    reassure_forever_pool: float
    cash_bag_plus_wallet_balance: float
    hospital_cash_days_used: int
    personal_accident_limit_remaining: float


class LiveHealthyApiResponse(BaseModel):
    current_points: int = 0
    points_snapshot_date: Optional[datetime] = None


class CashBagPlusApiResponse(BaseModel):
    balance: float = 0.0
    last_credited: Optional[datetime] = None


class LifetimeStateApiResponse(BaseModel):
    """Payload representing long-term state parameters for the policy/member"""
    policy_id: str
    lock_the_clock_age_locked: bool
    current_premium_age: int
    reassure_forever_triggered: bool
    convalescence_claimed: bool
    critical_illness_claimed: bool
    live_healthy: Optional[LiveHealthyApiResponse] = Field(default_factory=LiveHealthyApiResponse)
    cash_bag_plus: Optional[CashBagPlusApiResponse] = Field(default_factory=CashBagPlusApiResponse)



class EndorsementItem(BaseModel):
    """Chronological update item representing a single policy endorsement"""
    endorsement_id: str
    policy_id: str
    type: str  # "MemberAddition", "SIEnhancement", "SIReduction", "RiderAddition", "PlanUpgrade", etc.
    effective_date: datetime
    mutated_fields: Dict[str, Any] = Field(default_factory=dict)


class EndorsementApiResponse(BaseModel):
    """Payload representing list of mid-term policy endorsements"""
    endorsements: List[EndorsementItem] = Field(default_factory=list)


# ── New caseapi Integration Models ──────────────────────────────────────────

class GetAuthTokenRequest(BaseModel):
    UserID: str
    Client_id: str
    Identifier_Code: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"


class GetPolicyDetailsRequest(BaseModel):
    PolicyNumber: str


class GetPolicyDetailsByMobileRequest(BaseModel):
    MobileNumber: str
    PolicyNumber: str
    DOB: str


class GetClaimHistoryRequest(BaseModel):
    PolicyNo_COI: str
    Membership_No_ID: str = ""
    AllowedInactiveRecord: str = "Y"


class GetPolicyDataRequest(BaseModel):
    PolicyNo_COI: str

