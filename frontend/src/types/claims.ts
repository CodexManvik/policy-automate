/**
 * TypeScript type definitions for the ReAssure 3.0 Claims Adjudication Engine.
 * Mirrored 1:1 from src/schemas.py Pydantic models.
 */

// ---------------------------------------------------------------------------
// Claim Context — Input types
// ---------------------------------------------------------------------------

export type PolicyVariant = 'Classic' | 'Select' | 'Elite';
export type PolicyStatus = 'Active' | 'Lapsed' | 'Cancelled';
export type PolicyType = 'individual' | 'floater';
export type MemberRelationship = 'Self' | 'Spouse' | 'Child' | 'Parent' | 'Parent-in-law';
export type ProviderType = 'Network' | 'Non-Network' | 'Excluded';
export type EndorsementType =
  | 'MemberAddition'
  | 'MemberDeletion'
  | 'SIEnhancement'
  | 'SIReduction'
  | 'RiderAddition'
  | 'PlanUpgrade'
  | 'IndividualToFloater'
  | 'FloaterSplit';

export type BenefitBucket =
  | 'Expenses in reaching a Hospital'
  | 'Expenses during Hospitalization'
  | 'Expenses before and after hospitalization'
  | 'Home Care / Domiciliary Treatment'
  | 'Organ Donor'
  | 'Hospital Daily Cash'
  | 'Personal Accident'
  | 'Other';

export interface PolicyData {
  policy_id: string;
  product_code: string;
  variant: PolicyVariant;
  policy_start_date: string;
  policy_end_date: string;
  base_sum_insured: number;
  status: PolicyStatus;
  premium_paid: boolean;
  grace_period_active: boolean;
  policy_type: PolicyType;
  policy_term_years: number;
  co_payment_percent: number | null;
  annual_aggregate_deductible: number | null;
  room_category_entitled: string;
  room_rent_limit: number | null;
  hospital_daily_cash_amount: number | null;
  pa_sum_insured: number | null;
  personal_waiting_period_months: number;
  borderless_opted: boolean;
  borderless_specific_illness_opted: boolean;
  unlimited_si_opted: boolean;
  modern_treatments_plus_opted: boolean;
  air_ambulance_plus_opted: boolean;
  heads_up_opted: boolean;
  tiered_network_opted: boolean;
}

export interface MemberData {
  member_id: string;
  policy_id: string;
  name: string;
  age: number;
  entry_age: number;
  relationship: MemberRelationship;
  date_of_addition: string;
  ped_declarations: string[];
  eligibility_active: boolean;
}

export interface ClaimsHistoryData {
  policy_id: string;
  member_id: string;
  prior_claims_count: number;
  total_utilized_si: number;
  last_claim_date: string | null;
  prior_exclusions_triggered: string[];
  claim_free_years: number;
}

export interface PortingMigrationData {
  policy_id: string;
  porting_applicable: boolean;
  prior_coverage_months: number;
  waiting_period_credit_months: number;
  moratorium_eligible: boolean;
}

export interface NetworkData {
  provider_id: string;
  provider_name: string;
  provider_type: ProviderType;
  tiered_network_member: boolean;
  heads_up_recommended: boolean;
}

export interface BenefitBalanceData {
  policy_id: string;
  base_si_remaining: number;
  booster_plus_remaining: number;
  reassure_forever_pool: number;
  cash_bag_plus_wallet: number;
  hospital_cash_days_used: number;
  deductible_consumed_ytd: number;
}

export interface LiveHealthyData {
  current_points: number;
  points_snapshot_date: string | null;
}

export interface CashBagPlusData {
  balance: number;
  last_credited: string | null;
}

export interface LifetimeStateData {
  policy_id: string;
  reassure_forever_triggered: boolean;
  reassure_forever_triggered_date: string | null;
  reassure_forever_triggered_claim_id: string | null;
  lock_the_clock_age_locked: boolean;
  lock_the_clock_entry_age: number;
  lock_the_clock_unlocked_date: string | null;
  lock_the_clock_current_premium_age: number;
  booster_plus_accumulated: number;
  booster_plus_last_updated: string | null;
  convalescence_claimed: boolean;
  critical_illness_claimed: boolean;
  critical_illness_type: string | null;
  live_healthy: LiveHealthyData;
  cash_bag_plus: CashBagPlusData;
}

export interface EndorsementData {
  endorsement_id: string;
  policy_id: string;
  endorsement_type: EndorsementType;
  effective_date: string;
  details: Record<string, unknown>;
}

export interface LineItemData {
  line_item_id: string;
  description: string;
  claimed_amount: number;
  expense_date: string;
  benefit_bucket: BenefitBucket;
  admission_date: string | null;
  discharge_date: string | null;
  hospitalization_hours: number | null;
  actual_room_rent: number | null;
  room_category_claimed: string | null;
  treatment_type: string;
  condition_diagnosed: string;
  accident_related: boolean;
  emergency: boolean;
  room_charges: number | null;
  nursing_charges: number | null;
  medical_practitioner_fees: number | null;
  ot_charges: number | null;
  doctor_advised: boolean;
  continuous_treatment: boolean;
  daily_monitoring_chart: boolean;
}

export interface ClaimContext {
  claim_id: string;
  claim_received_at: string;
  policy: PolicyData;
  member: MemberData;
  history: ClaimsHistoryData;
  porting: PortingMigrationData;
  network: NetworkData;
  benefit_balance: BenefitBalanceData;
  lifetime_state: LifetimeStateData;
  endorsements: EndorsementData[];
  line_items: LineItemData[];
  product_json_version: string;
  renewal_event_simulation: boolean | null;
}

// ---------------------------------------------------------------------------
// Claim Decision — Response types
// ---------------------------------------------------------------------------

export interface DeductionDetail {
  deduction_type: string;
  amount: number;
  rule_id: string;
  reason: string;
  calculation_details?: Record<string, unknown>;
}

export interface DecisionTrace {
  step: number;
  rule_id: string;
  rule_name: string;
  gate: string;
  inputs: Record<string, unknown>;
  evaluation: string;
  reason: string;
  confidence: number;
  source_section?: string;
}

export interface LineItemDecision {
  line_item_id: string;
  description: string;
  claimed_amount: number;
  admissible_amount: number;
  payable_amount: number;
  decision: ClaimDecisionStatus;
  deductions: DeductionDetail[];
  decision_trace: DecisionTrace[];
  confidence_score: number;
  manual_review_required: boolean;
  review_reason?: string;
}

export interface DeductionBreakdown {
  room_pro_rata: number;
  co_payment: number;
  deductible: number;
  non_payable_items: number;
  sublimits: number;
  penalties: number;
  si_cap: number;
  lock_the_clock_premium_delta: number;
}

export interface SIWaterfallBreakdown {
  amount_from_base_si: number;
  amount_from_booster: number;
  amount_from_forever: number;
  total_paid: number;
  shortfall: number;
  updated_base_si: number;
  updated_booster: number;
  updated_forever_pool: number;
}

export type ClaimDecisionStatus =
  | 'APPROVED'
  | 'PARTIALLY_APPROVED'
  | 'REJECTED'
  | 'ASSISTED_REVIEW'
  | 'MEDICAL_REVIEW'
  | 'PENDING_REVIEW';

export interface ClaimDecision {
  claim_id: string;
  claim_decision: ClaimDecisionStatus;
  total_claimed: number;
  total_admissible: number;
  total_payable: number;
  total_deductions: number;
  deduction_breakdown: DeductionBreakdown;
  si_waterfall_breakdown: SIWaterfallBreakdown;
  line_items: LineItemDecision[];
  decision_trace: DecisionTrace[];
  confidence_score: number;
  manual_review_required: boolean;
  review_reasons: string[];
}

// ---------------------------------------------------------------------------
// API error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  public readonly status: number;
  constructor(
    status: number,
    message: string,
  ) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}
