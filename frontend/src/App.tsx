import { useState, useEffect } from 'react';
import { 
  Shield, 
  User, 
  Receipt, 
  Play, 
  Moon, 
  Sun, 
  Plus, 
  Trash2, 
  Activity, 
  CheckCircle2, 
  XCircle, 
  AlertCircle, 
  FileText, 
  BarChart3, 
  Sparkles
} from 'lucide-react';

// ============================================================================
// TYPES & SCHEMAS DEFINITION
// ============================================================================

interface PolicyData {
  policy_id: string;
  product_code: string;
  variant: 'Classic' | 'Select' | 'Elite';
  policy_start_date: string;
  policy_end_date: string;
  base_sum_insured: number;
  status: 'Active' | 'Lapsed' | 'Cancelled';
  premium_paid: boolean;
  grace_period_active: boolean;
  
  // Optional config (API contract)
  co_payment_percent: number | null;
  annual_aggregate_deductible: number | null;
  room_category_entitled: string;
  
  // Riders/opts
  borderless_opted: boolean;
  borderless_specific_illness_opted: boolean;
  unlimited_si_opted: boolean;
  modern_treatments_plus_opted: boolean;
  air_ambulance_plus_opted: boolean;
  heads_up_opted: boolean;
  tiered_network_opted: boolean;
  
  // Optional config (API contract)
  room_rent_limit: number | null;
  hospital_daily_cash_amount: number | null;
  pa_sum_insured: number | null;
  personal_waiting_period_months: number;
  policy_type: 'individual' | 'floater';
  policy_term_years: number;
}

interface MemberData {
  member_id: string;
  policy_id: string;
  name: string;
  age: number;
  entry_age: number;
  relationship: 'Self' | 'Spouse' | 'Child' | 'Parent' | 'Parent-in-law';
  date_of_addition: string;
  ped_declarations: string[];
  eligibility_active: boolean;
}

interface ClaimsHistoryData {
  policy_id: string;
  member_id: string;
  prior_claims_count: number;
  total_utilized_si: number;
  last_claim_date: string | null;
  prior_exclusions_triggered: string[];
  claim_free_years: number;
}

interface PortingMigrationData {
  policy_id: string;
  porting_applicable: boolean;
  prior_coverage_months: number;
  waiting_period_credit_months: number;
  moratorium_eligible: boolean;
}

interface NetworkData {
  provider_id: string;
  provider_name: string;
  provider_type: 'Network' | 'Non-Network' | 'Excluded';
  tiered_network_member: boolean;
  heads_up_recommended: boolean;
}

interface BenefitBalanceData {
  policy_id: string;
  base_si_remaining: number;
  booster_plus_remaining: number;
  reassure_forever_pool: number;
  cash_bag_plus_wallet: number;
  hospital_cash_days_used: number;
  deductible_consumed_ytd: number;
}

interface LifetimeStateData {
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
}

interface LineItemData {
  line_item_id: string;
  description: string;
  claimed_amount: number;
  expense_date: string;
  benefit_bucket: 
    | "Expenses in reaching a Hospital"
    | "Expenses during Hospitalization"
    | "Expenses before and after hospitalization"
    | "Home Care / Domiciliary Treatment"
    | "Organ Donor"
    | "Hospital Daily Cash"
    | "Personal Accident"
    | "Other";
  
  // Optional configs (API contract)
  admission_date: string | null;
  discharge_date: string | null;
  hospitalization_hours: number | null;
  
  actual_room_rent: number | null;
  room_category_claimed: string | null;
  
  treatment_type: string;
  condition_diagnosed: string;
  accident_related: boolean;
  emergency: boolean;
  
  // Room rent bill breakdown (API contract)
  room_charges: number | null;
  nursing_charges: number | null;
  medical_practitioner_fees: number | null;
  ot_charges: number | null;
  
  // Domiciliary conditions
  doctor_advised: boolean;
  continuous_treatment: boolean;
  daily_monitoring_chart: boolean;
}

interface ClaimContext {
  claim_id: string;
  claim_received_at: string;
  policy: PolicyData;
  member: MemberData;
  history: ClaimsHistoryData;
  porting: PortingMigrationData;
  network: NetworkData;
  benefit_balance: BenefitBalanceData;
  lifetime_state: LifetimeStateData;
  endorsements: any[];
  line_items: LineItemData[];
  product_json_version: string;
  context_assembled_at: string;
}

// Response output structures
interface DeductionDetail {
  deduction_type: string;
  amount: number;
  rule_id: string;
  reason: string;
  calculation_details: Record<string, any>;
}

interface LineItemDecision {
  line_item_id: string;
  decision: 'APPROVED' | 'PARTIALLY_APPROVED' | 'REJECTED' | 'ASSISTED_REVIEW' | 'PENDING_REVIEW';
  claimed_amount: number;
  admissible_amount: number;
  payable_amount: number;
  deductions: DeductionDetail[];
  decision_trace: DecisionTrace[];
  confidence_score: number;
  manual_review_required: boolean;
  review_reason: string | null;
}

interface DecisionTrace {
  step: number;
  rule_id: string;
  rule_name: string;
  gate: string;
  evaluation: 'PASSED' | 'FAILED' | 'NOT_APPLICABLE' | 'EXCLUSION_ACTIVE' | 'DEDUCTION_APPLIED' | 'PENDING_REVIEW' | 'ASSISTED_REVIEW';
  reason: string;
  confidence: number;
}

interface DeductionBreakdown {
  room_pro_rata: number;
  co_payment: number;
  deductible: number;
  non_payable_items: number;
  si_cap: number;
  sublimits: number;
  penalties: number;
}

interface SIWaterfallBreakdown {
  amount_from_base_si: number;
  amount_from_booster: number;
  amount_from_forever: number;
  total_paid: number;
  shortfall: number;
  updated_base_si: number;
  updated_booster: number;
  updated_forever_pool: number;
}

interface ClaimDecision {
  claim_id: string;
  claim_decision: 'APPROVED' | 'PARTIALLY_APPROVED' | 'REJECTED' | 'ASSISTED_REVIEW' | 'PENDING_REVIEW';
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
  decision_timestamp: string;
  processing_duration_ms: number | null;
}

// ============================================================================
// SCENARIO PRESETS
// ============================================================================

const PRESETS: Record<string, { name: string; description: string; context: ClaimContext }> = {
  PRESET_APPENDICITIS: {
    name: 'Appendicitis Room Pro-Rata Match',
    description: 'Standard hospitalization claim where room rents match entitlement. Full pro-rata approval with standard 10% co-payment.',
    context: {
      claim_id: 'CLM-APP-001',
      claim_received_at: new Date().toISOString(),
      product_json_version: 'R3_v2.1_2025-01-15',
      context_assembled_at: new Date().toISOString(),
      endorsements: [],
      policy: {
        policy_id: 'POL-1001',
        product_code: 'R3',
        variant: 'Select',
        policy_start_date: '2024-06-01T00:00:00Z',
        policy_end_date: '2025-05-31T00:00:00Z',
        base_sum_insured: 500000.0,
        status: 'Active',
        premium_paid: true,
        grace_period_active: false,
        policy_type: 'individual',
        policy_term_years: 1,
        co_payment_percent: 10.0, // OPTIONAL contract field
        annual_aggregate_deductible: null,
        room_category_entitled: 'Single Private Room',
        room_rent_limit: null, // OPTIONAL contract field (None = Unlimited for Single Private Room)
        hospital_daily_cash_amount: null,
        pa_sum_insured: null,
        personal_waiting_period_months: 0,
        borderless_opted: false,
        borderless_specific_illness_opted: false,
        unlimited_si_opted: false,
        modern_treatments_plus_opted: false,
        air_ambulance_plus_opted: false,
        heads_up_opted: false,
        tiered_network_opted: false,
      },
      member: {
        member_id: 'MEM-1001',
        policy_id: 'POL-1001',
        name: 'Amit Patel',
        age: 35,
        entry_age: 32,
        relationship: 'Self',
        date_of_addition: '2024-06-01T00:00:00Z',
        ped_declarations: [],
        eligibility_active: true
      },
      history: {
        policy_id: 'POL-1001',
        member_id: 'MEM-1001',
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 2
      },
      porting: {
        policy_id: 'POL-1001',
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false
      },
      network: {
        provider_id: 'HOSP-201',
        provider_name: 'Apollo Hospital Ahmedabad',
        provider_type: 'Network',
        tiered_network_member: false,
        heads_up_recommended: false
      },
      benefit_balance: {
        policy_id: 'POL-1001',
        base_si_remaining: 500000.0,
        booster_plus_remaining: 100000.0,
        reassure_forever_pool: 0.0,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0,
        deductible_consumed_ytd: 0.0
      },
      lifetime_state: {
        policy_id: 'POL-1001',
        reassure_forever_triggered: false,
        reassure_forever_triggered_date: null,
        reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: true,
        lock_the_clock_entry_age: 32,
        lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: 32,
        booster_plus_accumulated: 100000.0,
        booster_plus_last_updated: '2025-06-01T00:00:00Z',
        convalescence_claimed: false,
        critical_illness_claimed: false,
        critical_illness_type: null
      },
      line_items: [
        {
          line_item_id: 'LI-APP-01',
          description: 'Appendectomy Surgical Room Charges',
          claimed_amount: 50000.0,
          expense_date: '2024-10-15T00:00:00Z',
          benefit_bucket: 'Expenses during Hospitalization',
          admission_date: '2024-10-13T00:00:00Z', // OPTIONAL contract field
          discharge_date: '2024-10-15T00:00:00Z', // OPTIONAL contract field
          hospitalization_hours: 48.0,            // OPTIONAL contract field
          actual_room_rent: 8000.0,               // OPTIONAL contract field (matches entitlement single private)
          room_category_claimed: 'Single Private Room', // OPTIONAL contract field
          treatment_type: 'Allopathic',
          condition_diagnosed: 'Acute Appendicitis',
          accident_related: false,
          emergency: false,
          room_charges: 16000.0,                 // OPTIONAL contract field
          nursing_charges: 8000.0,                  // OPTIONAL contract field
          medical_practitioner_fees: 20000.0,     // OPTIONAL contract field
          ot_charges: 6000.0,                     // OPTIONAL contract field
          doctor_advised: false,
          continuous_treatment: false,
          daily_monitoring_chart: false
        }
      ]
    }
  },
  PRESET_MODERN_LIMIT: {
    name: 'Robotic Surgery (Sub-limit Applied)',
    description: 'Robotic Surgery claim under Classic variant which imposes a 50% sub-limit. Watch how it caps payout, then toggle "Modern Treatments Plus" to remove it.',
    context: {
      claim_id: 'CLM-ROB-001',
      claim_received_at: new Date().toISOString(),
      product_json_version: 'R3_v2.1_2025-01-15',
      context_assembled_at: new Date().toISOString(),
      endorsements: [],
      policy: {
        policy_id: 'POL-1002',
        product_code: 'R3',
        variant: 'Classic',
        policy_start_date: '2024-06-01T00:00:00Z',
        policy_end_date: '2025-05-31T00:00:00Z',
        base_sum_insured: 100000.0,
        status: 'Active',
        premium_paid: true,
        grace_period_active: false,
        policy_type: 'individual',
        policy_term_years: 1,
        co_payment_percent: null,
        annual_aggregate_deductible: null,
        room_category_entitled: 'General Ward',
        room_rent_limit: null,
        hospital_daily_cash_amount: null,
        pa_sum_insured: null,
        personal_waiting_period_months: 0,
        borderless_opted: false,
        borderless_specific_illness_opted: false,
        unlimited_si_opted: false,
        modern_treatments_plus_opted: false, // Turn this ON to bypass sub-limit
        air_ambulance_plus_opted: false,
        heads_up_opted: false,
        tiered_network_opted: false,
      },
      member: {
        member_id: 'MEM-1002',
        policy_id: 'POL-1002',
        name: 'Rita Sen',
        age: 42,
        entry_age: 40,
        relationship: 'Self',
        date_of_addition: '2024-06-01T00:00:00Z',
        ped_declarations: [],
        eligibility_active: true
      },
      history: {
        policy_id: 'POL-1002',
        member_id: 'MEM-1002',
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 1
      },
      porting: {
        policy_id: 'POL-1002',
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false
      },
      network: {
        provider_id: 'HOSP-202',
        provider_name: 'Fortis Hospital Noida',
        provider_type: 'Network',
        tiered_network_member: false,
        heads_up_recommended: false
      },
      benefit_balance: {
        policy_id: 'POL-1002',
        base_si_remaining: 100000.0,
        booster_plus_remaining: 0.0,
        reassure_forever_pool: 0.0,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0,
        deductible_consumed_ytd: 0.0
      },
      lifetime_state: {
        policy_id: 'POL-1002',
        reassure_forever_triggered: false,
        reassure_forever_triggered_date: null,
        reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: true,
        lock_the_clock_entry_age: 40,
        lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: 40,
        booster_plus_accumulated: 0.0,
        booster_plus_last_updated: null,
        convalescence_claimed: false,
        critical_illness_claimed: false,
        critical_illness_type: null
      },
      line_items: [
        {
          line_item_id: 'LI-ROB-01',
          description: 'Robotic Surgery for prostate cancer',
          claimed_amount: 80000.0,
          expense_date: '2024-11-20T00:00:00Z',
          benefit_bucket: 'Expenses during Hospitalization',
          admission_date: '2024-11-18T00:00:00Z',
          discharge_date: '2024-11-20T00:00:00Z',
          hospitalization_hours: 48.0,
          actual_room_rent: 4000.0,
          room_category_claimed: 'Single Private Room',
          treatment_type: 'Allopathic',
          condition_diagnosed: 'Prostate Cancer',
          accident_related: false,
          emergency: false,
          room_charges: 8000.0,
          nursing_charges: 4000.0,
          medical_practitioner_fees: 50000.0,
          ot_charges: 18000.0,
          doctor_advised: false,
          continuous_treatment: false,
          daily_monitoring_chart: false
        }
      ]
    }
  },
  PRESET_DAILY_CASH: {
    name: 'Hospital Daily Cash Payout',
    description: 'Special fixed benefit payout of Daily Cash amount for 3 days of hospitalization (exempt from deductibles/co-payments).',
    context: {
      claim_id: 'CLM-HDC-001',
      claim_received_at: new Date().toISOString(),
      product_json_version: 'R3_v2.1_2025-01-15',
      context_assembled_at: new Date().toISOString(),
      endorsements: [],
      policy: {
        policy_id: 'POL-1003',
        product_code: 'R3',
        variant: 'Select',
        policy_start_date: '2024-06-01T00:00:00Z',
        policy_end_date: '2025-05-31T00:00:00Z',
        base_sum_insured: 400000.0,
        status: 'Active',
        premium_paid: true,
        grace_period_active: false,
        policy_type: 'individual',
        policy_term_years: 1,
        co_payment_percent: null,
        annual_aggregate_deductible: null,
        room_category_entitled: 'Single Private Room',
        room_rent_limit: null,
        hospital_daily_cash_amount: 2000.0, // OPTIONAL contract field (Rider cash benefit)
        pa_sum_insured: null,
        personal_waiting_period_months: 0,
        borderless_opted: false,
        borderless_specific_illness_opted: false,
        unlimited_si_opted: false,
        modern_treatments_plus_opted: false,
        air_ambulance_plus_opted: false,
        heads_up_opted: false,
        tiered_network_opted: false,
      },
      member: {
        member_id: 'MEM-1003',
        policy_id: 'POL-1003',
        name: 'Vikas Kumar',
        age: 28,
        entry_age: 28,
        relationship: 'Self',
        date_of_addition: '2024-06-01T00:00:00Z',
        ped_declarations: [],
        eligibility_active: true
      },
      history: {
        policy_id: 'POL-1003',
        member_id: 'MEM-1003',
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 0
      },
      porting: {
        policy_id: 'POL-1003',
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false
      },
      network: {
        provider_id: 'HOSP-203',
        provider_name: 'Max Healthcare Delhi',
        provider_type: 'Network',
        tiered_network_member: false,
        heads_up_recommended: false
      },
      benefit_balance: {
        policy_id: 'POL-1003',
        base_si_remaining: 400000.0,
        booster_plus_remaining: 0.0,
        reassure_forever_pool: 0.0,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0, // Track YTD days
        deductible_consumed_ytd: 0.0
      },
      lifetime_state: {
        policy_id: 'POL-1003',
        reassure_forever_triggered: false,
        reassure_forever_triggered_date: null,
        reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: true,
        lock_the_clock_entry_age: 28,
        lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: 28,
        booster_plus_accumulated: 0.0,
        booster_plus_last_updated: null,
        convalescence_claimed: false,
        critical_illness_claimed: false,
        critical_illness_type: null
      },
      line_items: [
        {
          line_item_id: 'LI-HDC-01',
          description: 'Daily cash benefit during recovery',
          claimed_amount: 10000.0,
          expense_date: '2024-09-10T00:00:00Z',
          benefit_bucket: 'Hospital Daily Cash',
          admission_date: '2024-09-07T00:00:00Z',
          discharge_date: '2024-09-10T00:00:00Z',
          hospitalization_hours: 72.0, // 3 full days
          actual_room_rent: null,
          room_category_claimed: null,
          treatment_type: 'Allopathic',
          condition_diagnosed: 'Viral Fever',
          accident_related: false,
          emergency: false,
          room_charges: null,
          nursing_charges: null,
          medical_practitioner_fees: null,
          ot_charges: null,
          doctor_advised: false,
          continuous_treatment: false,
          daily_monitoring_chart: false
        }
      ]
    }
  },
  PRESET_WAITING_PERIOD: {
    name: 'New Member Waiting Period Reject',
    description: 'A member added to an active policy mid-term files a claim for Fever within the initial 30 days of addition. It gets completely rejected.',
    context: {
      claim_id: 'CLM-WAIT-001',
      claim_received_at: new Date().toISOString(),
      product_json_version: 'R3_v2.1_2025-01-15',
      context_assembled_at: new Date().toISOString(),
      endorsements: [
        {
          endorsement_id: 'END-MEMBER-01',
          policy_id: 'POL-1004',
          endorsement_type: 'MemberAddition',
          effective_date: '2024-07-01T00:00:00Z',
          details: { member_id: 'MEM-1004B' }
        }
      ],
      policy: {
        policy_id: 'POL-1004',
        product_code: 'R3',
        variant: 'Select',
        policy_start_date: '2024-06-01T00:00:00Z',
        policy_end_date: '2025-05-31T00:00:00Z',
        base_sum_insured: 500000.0,
        status: 'Active',
        premium_paid: true,
        grace_period_active: false,
        policy_type: 'individual',
        policy_term_years: 1,
        co_payment_percent: null,
        annual_aggregate_deductible: null,
        room_category_entitled: 'Single Private Room',
        room_rent_limit: null,
        hospital_daily_cash_amount: null,
        pa_sum_insured: null,
        personal_waiting_period_months: 0,
        borderless_opted: false,
        borderless_specific_illness_opted: false,
        unlimited_si_opted: false,
        modern_treatments_plus_opted: false,
        air_ambulance_plus_opted: false,
        heads_up_opted: false,
        tiered_network_opted: false,
      },
      member: {
        member_id: 'MEM-1004B',
        policy_id: 'POL-1004',
        name: 'Sunita Patel',
        age: 30,
        entry_age: 30,
        relationship: 'Spouse',
        date_of_addition: '2024-07-01T00:00:00Z', // Added mid-term
        ped_declarations: [],
        eligibility_active: true
      },
      history: {
        policy_id: 'POL-1004',
        member_id: 'MEM-1004B',
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 0 // Fresh waiting period
      },
      porting: {
        policy_id: 'POL-1004',
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false
      },
      network: {
        provider_id: 'HOSP-204',
        provider_name: 'Sterling Hospital Vadodara',
        provider_type: 'Network',
        tiered_network_member: false,
        heads_up_recommended: false
      },
      benefit_balance: {
        policy_id: 'POL-1004',
        base_si_remaining: 500000.0,
        booster_plus_remaining: 0.0,
        reassure_forever_pool: 0.0,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0,
        deductible_consumed_ytd: 0.0
      },
      lifetime_state: {
        policy_id: 'POL-1004',
        reassure_forever_triggered: false,
        reassure_forever_triggered_date: null,
        reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: true,
        lock_the_clock_entry_age: 30,
        lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: 30,
        booster_plus_accumulated: 0.0,
        booster_plus_last_updated: null,
        convalescence_claimed: false,
        critical_illness_claimed: false,
        critical_illness_type: null
      },
      line_items: [
        {
          line_item_id: 'LI-WAIT-01',
          description: 'Treatment for Acute Viral Fever',
          claimed_amount: 15000.0,
          expense_date: '2024-07-15T00:00:00Z', // Only 14 days after addition!
          benefit_bucket: 'Expenses during Hospitalization',
          admission_date: '2024-07-12T00:00:00Z',
          discharge_date: '2024-07-15T00:00:00Z',
          hospitalization_hours: 72.0,
          actual_room_rent: 3000.0,
          room_category_claimed: 'General Ward',
          treatment_type: 'Allopathic',
          condition_diagnosed: 'Viral Fever',
          accident_related: false,
          emergency: false,
          room_charges: 6000.0,
          nursing_charges: 3000.0,
          medical_practitioner_fees: 4000.0,
          ot_charges: 2000.0,
          doctor_advised: false,
          continuous_treatment: false,
          daily_monitoring_chart: false
        }
      ]
    }
  },
  PRESET_MISSING_CONFIG: {
    name: 'Missing Room Rent Limit (Assisted Review)',
    description: 'We intentionally set "room_rent_limit" to None/Empty and claim room charges, causing the pro-rata step to yield NOT_APPLICABLE and route the claim to ASSISTED_REVIEW.',
    context: {
      claim_id: 'CLM-MISS-001',
      claim_received_at: new Date().toISOString(),
      product_json_version: 'R3_v2.1_2025-01-15',
      context_assembled_at: new Date().toISOString(),
      endorsements: [],
      policy: {
        policy_id: 'POL-1005',
        product_code: 'R3',
        variant: 'Classic', // Classic variants require either room rent limits or Single Private Room
        policy_start_date: '2024-06-01T00:00:00Z',
        policy_end_date: '2025-05-31T00:00:00Z',
        base_sum_insured: 200000.0,
        status: 'Active',
        premium_paid: true,
        grace_period_active: false,
        policy_type: 'individual',
        policy_term_years: 1,
        co_payment_percent: null,
        annual_aggregate_deductible: null,
        room_category_entitled: 'Shared Room',
        room_rent_limit: null, // CONTRACT FIELD SET TO NULL (None)
        hospital_daily_cash_amount: null,
        pa_sum_insured: null,
        personal_waiting_period_months: 0,
        borderless_opted: false,
        borderless_specific_illness_opted: false,
        unlimited_si_opted: false,
        modern_treatments_plus_opted: false,
        air_ambulance_plus_opted: false,
        heads_up_opted: false,
        tiered_network_opted: false,
      },
      member: {
        member_id: 'MEM-1005',
        policy_id: 'POL-1005',
        name: 'Alok Sharma',
        age: 50,
        entry_age: 48,
        relationship: 'Self',
        date_of_addition: '2024-06-01T00:00:00Z',
        ped_declarations: [],
        eligibility_active: true
      },
      history: {
        policy_id: 'POL-1005',
        member_id: 'MEM-1005',
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 3
      },
      porting: {
        policy_id: 'POL-1005',
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false
      },
      network: {
        provider_id: 'HOSP-205',
        provider_name: 'Care Hospital Hyderabad',
        provider_type: 'Network',
        tiered_network_member: false,
        heads_up_recommended: false
      },
      benefit_balance: {
        policy_id: 'POL-1005',
        base_si_remaining: 200000.0,
        booster_plus_remaining: 0.0,
        reassure_forever_pool: 0.0,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0,
        deductible_consumed_ytd: 0.0
      },
      lifetime_state: {
        policy_id: 'POL-1005',
        reassure_forever_triggered: false,
        reassure_forever_triggered_date: null,
        reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: true,
        lock_the_clock_entry_age: 48,
        lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: 48,
        booster_plus_accumulated: 0.0,
        booster_plus_last_updated: null,
        convalescence_claimed: false,
        critical_illness_claimed: false,
        critical_illness_type: null
      },
      line_items: [
        {
          line_item_id: 'LI-MISS-01',
          description: 'Surgical recovery room stay',
          claimed_amount: 30000.0,
          expense_date: '2024-09-05T00:00:00Z',
          benefit_bucket: 'Expenses during Hospitalization',
          admission_date: '2024-09-02T00:00:00Z',
          discharge_date: '2024-09-05T00:00:00Z',
          hospitalization_hours: 72.0,
          actual_room_rent: 6000.0, // Higher than shared room rates
          room_category_claimed: 'Single Private Room',
          treatment_type: 'Allopathic',
          condition_diagnosed: 'Gastroenteritis',
          accident_related: false,
          emergency: false,
          room_charges: 18000.0,
          nursing_charges: 4000.0,
          medical_practitioner_fees: 5000.0,
          ot_charges: 3000.0,
          doctor_advised: false,
          continuous_treatment: false,
          daily_monitoring_chart: false
        }
      ]
    }
  }
};

// Default context (uses PRESET_APPENDICITIS)
const DEFAULT_CONTEXT = PRESETS.PRESET_APPENDICITIS.context;

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [activeTab, setActiveTab] = useState<'policy' | 'member' | 'lineItems'>('policy');
  const [context, setContext] = useState<ClaimContext>(JSON.parse(JSON.stringify(DEFAULT_CONTEXT)));
  
  // API Call States
  const [loading, setLoading] = useState<boolean>(false);
  const [result, setResult] = useState<ClaimDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Set initial theme
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'light') {
      root.classList.add('light');
    } else {
      root.classList.remove('light');
    }
  }, [theme]);

  const toggleTheme = () => {
    setTheme(t => t === 'dark' ? 'light' : 'dark');
  };

  // Preset loader
  const loadPreset = (presetKey: string) => {
    const selected = PRESETS[presetKey];
    if (selected) {
      // Create deep clone
      const cloned = JSON.parse(JSON.stringify(selected.context));
      // Re-initialize claim dates
      cloned.claim_received_at = new Date().toISOString();
      cloned.context_assembled_at = new Date().toISOString();
      setContext(cloned);
      setResult(null);
      setError(null);
    }
  };

  // Value Handlers (ensuring numeric inputs map properly to float or null)
  const handlePolicyChange = (field: keyof PolicyData, value: any) => {
    setContext(prev => ({
      ...prev,
      policy: {
        ...prev.policy,
        [field]: value
      }
    }));
  };

  const handlePolicyNumberChange = (field: keyof PolicyData, valueStr: string) => {
    const val = valueStr === '' ? null : parseFloat(valueStr);
    handlePolicyChange(field, val);
  };

  const handleMemberChange = (field: keyof MemberData, value: any) => {
    setContext(prev => ({
      ...prev,
      member: {
        ...prev.member,
        [field]: value
      }
    }));
  };

  const handleMemberNumChange = (field: keyof MemberData, valueStr: string) => {
    const val = valueStr === '' ? 0 : parseInt(valueStr, 10);
    handleMemberChange(field, val);
  };

  const handleBalanceChange = (field: keyof BenefitBalanceData, valueStr: string) => {
    const val = valueStr === '' ? 0 : parseFloat(valueStr);
    setContext(prev => ({
      ...prev,
      benefit_balance: {
        ...prev.benefit_balance,
        [field]: val
      }
    }));
  };

  // Line Item Handlers
  const handleLineItemChange = (index: number, field: keyof LineItemData, value: any) => {
    setContext(prev => {
      const updated = [...prev.line_items];
      updated[index] = {
        ...updated[index],
        [field]: value
      };
      return {
        ...prev,
        line_items: updated
      };
    });
  };

  const handleLineItemNumChange = (index: number, field: keyof LineItemData, valueStr: string) => {
    const val = valueStr === '' ? null : parseFloat(valueStr);
    handleLineItemChange(index, field, val);
  };

  const addLineItem = () => {
    const newItem: LineItemData = {
      line_item_id: `LI-00${context.line_items.length + 1}`,
      description: 'Medical consumables and charges',
      claimed_amount: 10000.0,
      expense_date: new Date().toISOString().split('T')[0],
      benefit_bucket: 'Expenses during Hospitalization',
      admission_date: null,
      discharge_date: null,
      hospitalization_hours: null,
      actual_room_rent: null,
      room_category_claimed: null,
      treatment_type: 'Allopathic',
      condition_diagnosed: 'Acute Appendicitis',
      accident_related: false,
      emergency: false,
      room_charges: null,
      nursing_charges: null,
      medical_practitioner_fees: null,
      ot_charges: null,
      doctor_advised: false,
      continuous_treatment: false,
      daily_monitoring_chart: false
    };
    setContext(prev => ({
      ...prev,
      line_items: [...prev.line_items, newItem]
    }));
  };

  const removeLineItem = (index: number) => {
    setContext(prev => ({
      ...prev,
      line_items: prev.line_items.filter((_, i) => i !== index)
    }));
  };

  // Run Adjudication call
  const runAdjudication = async () => {
    setLoading(true);
    setResult(null);
    setError(null);

    // Deep copy and clean payload dates
    const payload = JSON.parse(JSON.stringify(context));
    
    // Coerce empty strings to null or appropriate types for dates and lists
    if (!payload.policy.policy_id) payload.policy.policy_id = 'POL-GENERIC';
    
    // Perform standard fetch post
    try {
      const response = await fetch('http://127.0.0.1:8000/api/v2/adjudicate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errDetail = await response.json();
        throw new Error(errDetail.detail || `Server returned error status ${response.status}`);
      }

      const data: ClaimDecision = await response.json();
      setResult(data);
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'Failed to establish connection to the auto-adjudication server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-layout">
      {/* Header */}
      <header className="glass-header">
        <div className="header-content">
          <div className="brand">
            <div className="brand-icon">NB</div>
            <div>
              <h1 className="brand-title">ReAssure 3.0</h1>
              <div className="brand-subtitle">Claims Auto-Adjudication Engine Sandbox</div>
            </div>
          </div>
          <div className="header-actions">
            {/* Theme switcher */}
            <button 
              type="button" 
              className="theme-toggle" 
              onClick={toggleTheme} 
              title="Toggle theme"
            >
              {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </div>
      </header>

      {/* Main Sandbox Grid */}
      <main className="main-container">
        
        {/* Editor (Left Column) */}
        <div className="editor-column">
          
          {/* Preset selector */}
          <div className="glass-card panel-section">
            <h2 className="panel-title" style={{ fontSize: '1rem', marginBottom: '0.75rem' }}>
              <Sparkles size={16} /> Pre-configured Test Presets
            </h2>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {Object.keys(PRESETS).map(key => (
                <button
                  key={key}
                  type="button"
                  className="demo-btn"
                  onClick={() => loadPreset(key)}
                  style={{
                    fontSize: '0.8rem',
                    border: '1px solid var(--border-color)',
                    background: context.claim_id.includes(key.split('_')[1]) ? 'var(--color-primary)' : 'var(--bg-tertiary)',
                    color: context.claim_id.includes(key.split('_')[1]) ? 'white' : 'var(--text-primary)'
                  }}
                >
                  {PRESETS[key].name}
                </button>
              ))}
            </div>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.75rem', lineHeight: '1.4' }}>
              ℹ️ Presets auto-populate the underlying context builder. You can modify any field below to simulate edge cases.
            </p>
          </div>

          {/* Form Editor Card */}
          <div className="glass-card" style={{ overflow: 'hidden' }}>
            {/* Tab navigation */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border-color)', background: 'rgba(0,0,0,0.1)' }}>
              <button
                type="button"
                onClick={() => setActiveTab('policy')}
                style={{
                  flex: 1, padding: '1rem', border: 'none', background: 'transparent',
                  color: activeTab === 'policy' ? 'var(--color-primary)' : 'var(--text-secondary)',
                  fontWeight: 600, borderBottom: activeTab === 'policy' ? '2px solid var(--color-primary)' : 'none',
                  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem'
                }}
              >
                <Shield size={16} /> Policy Profile
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('member')}
                style={{
                  flex: 1, padding: '1rem', border: 'none', background: 'transparent',
                  color: activeTab === 'member' ? 'var(--color-primary)' : 'var(--text-secondary)',
                  fontWeight: 600, borderBottom: activeTab === 'member' ? '2px solid var(--color-primary)' : 'none',
                  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem'
                }}
              >
                <User size={16} /> Member & Balances
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('lineItems')}
                style={{
                  flex: 1, padding: '1rem', border: 'none', background: 'transparent',
                  color: activeTab === 'lineItems' ? 'var(--color-primary)' : 'var(--text-secondary)',
                  fontWeight: 600, borderBottom: activeTab === 'lineItems' ? '2px solid var(--color-primary)' : 'none',
                  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem'
                }}
              >
                <Receipt size={16} /> Line Items ({context.line_items.length})
              </button>
            </div>

            {/* Tab Panel contents */}
            <div className="panel-section">
              
              {/* POLICY TAB */}
              {activeTab === 'policy' && (
                <div className="form-grid">
                  <div className="form-group">
                    <label className="form-label">Policy ID</label>
                    <input 
                      type="text" className="input-control" 
                      value={context.policy.policy_id} 
                      onChange={e => handlePolicyChange('policy_id', e.target.value)}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Policy Variant</label>
                    <select 
                      className="input-control"
                      value={context.policy.variant}
                      onChange={e => handlePolicyChange('variant', e.target.value as any)}
                    >
                      <option value="Classic">Classic</option>
                      <option value="Select">Select</option>
                      <option value="Elite">Elite</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Policy Type</label>
                    <select 
                      className="input-control"
                      value={context.policy.policy_type}
                      onChange={e => handlePolicyChange('policy_type', e.target.value as any)}
                    >
                      <option value="individual">Individual</option>
                      <option value="floater">Floater</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Policy Term (Years)</label>
                    <input 
                      type="number" className="input-control" 
                      min="1" max="5"
                      value={context.policy.policy_term_years} 
                      onChange={e => handlePolicyChange('policy_term_years', parseInt(e.target.value) || 1)}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Base Sum Insured (INR)</label>
                    <input 
                      type="number" className="input-control" 
                      value={context.policy.base_sum_insured} 
                      onChange={e => handlePolicyChange('base_sum_insured', parseFloat(e.target.value) || 0)}
                    />
                  </div>
                  
                  {/* Co-Payment Contract Field */}
                  <div className="form-group">
                    <label className="form-label">
                      Co-payment Percent (%)
                      <span className="form-label-desc"> (Optional contract field)</span>
                    </label>
                    <input 
                      type="number" className="input-control" placeholder="None (e.g. 10)"
                      value={context.policy.co_payment_percent ?? ''} 
                      onChange={e => handlePolicyNumberChange('co_payment_percent', e.target.value)}
                    />
                  </div>

                  {/* Deductible Contract Field */}
                  <div className="form-group">
                    <label className="form-label">
                      Annual Deductible (INR)
                      <span className="form-label-desc"> (Optional contract field)</span>
                    </label>
                    <input 
                      type="number" className="input-control" placeholder="None (e.g. 20000)"
                      value={context.policy.annual_aggregate_deductible ?? ''} 
                      onChange={e => handlePolicyNumberChange('annual_aggregate_deductible', e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Room Category Entitled</label>
                    <select 
                      className="input-control"
                      value={context.policy.room_category_entitled}
                      onChange={e => handlePolicyChange('room_category_entitled', e.target.value)}
                    >
                      <option value="General Ward">General Ward</option>
                      <option value="Shared Room">Shared Room</option>
                      <option value="Single Private Room">Single Private Room</option>
                      <option value="Suite">Suite</option>
                    </select>
                  </div>

                  {/* Room Rent Limit Contract Field */}
                  <div className="form-group">
                    <label className="form-label">
                      Room Rent Limit / Day (INR)
                      <span className="form-label-desc"> (Optional contract field)</span>
                    </label>
                    <input 
                      type="number" className="input-control" placeholder="None (Unlimited)"
                      value={context.policy.room_rent_limit ?? ''} 
                      onChange={e => handlePolicyNumberChange('room_rent_limit', e.target.value)}
                    />
                  </div>

                  {/* Hospital Daily Cash Benefit Field */}
                  <div className="form-group">
                    <label className="form-label">
                      Daily Cash Amount (INR)
                      <span className="form-label-desc"> (Optional cash benefit rider)</span>
                    </label>
                    <input 
                      type="number" className="input-control" placeholder="None"
                      value={context.policy.hospital_daily_cash_amount ?? ''} 
                      onChange={e => handlePolicyNumberChange('hospital_daily_cash_amount', e.target.value)}
                    />
                  </div>

                  {/* Personal Accident sum insured field */}
                  <div className="form-group">
                    <label className="form-label">
                      PA Sum Insured (INR)
                      <span className="form-label-desc"> (Optional personal accident rider)</span>
                    </label>
                    <input 
                      type="number" className="input-control" placeholder="None"
                      value={context.policy.pa_sum_insured ?? ''} 
                      onChange={e => handlePolicyNumberChange('pa_sum_insured', e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Personal Waiting Period (m)</label>
                    <input 
                      type="number" className="input-control"
                      value={context.policy.personal_waiting_period_months} 
                      onChange={e => handlePolicyChange('personal_waiting_period_months', parseInt(e.target.value, 10) || 0)}
                    />
                  </div>

                  {/* Riders toggles */}
                  <div className="form-group full-width" style={{ marginTop: '0.5rem' }}>
                    <label className="form-label" style={{ marginBottom: '0.5rem' }}>Riders & Benefits Opted</label>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.75rem' }}>
                      <div className="switch-group">
                        <span className="form-label">Borderless Rider</span>
                        <label className="switch-control">
                          <input 
                            type="checkbox" 
                            checked={context.policy.borderless_opted}
                            onChange={e => handlePolicyChange('borderless_opted', e.target.checked)}
                          />
                          <span className="slider"></span>
                        </label>
                      </div>
                      <div className="switch-group">
                        <span className="form-label">Unlimited SI</span>
                        <label className="switch-control">
                          <input 
                            type="checkbox" 
                            checked={context.policy.unlimited_si_opted}
                            onChange={e => handlePolicyChange('unlimited_si_opted', e.target.checked)}
                          />
                          <span className="slider"></span>
                        </label>
                      </div>
                      <div className="switch-group">
                        <span className="form-label">Modern Treatments Plus</span>
                        <label className="switch-control">
                          <input 
                            type="checkbox" 
                            checked={context.policy.modern_treatments_plus_opted}
                            onChange={e => handlePolicyChange('modern_treatments_plus_opted', e.target.checked)}
                          />
                          <span className="slider"></span>
                        </label>
                      </div>
                      <div className="switch-group">
                        <span className="form-label">HeadsUp (Pre-auth Penalty)</span>
                        <label className="switch-control">
                          <input 
                            type="checkbox" 
                            checked={context.policy.heads_up_opted}
                            onChange={e => handlePolicyChange('heads_up_opted', e.target.checked)}
                          />
                          <span className="slider"></span>
                        </label>
                      </div>
                      <div className="switch-group">
                        <span className="form-label">Tiered Network Opted</span>
                        <label className="switch-control">
                          <input 
                            type="checkbox" 
                            checked={context.policy.tiered_network_opted}
                            onChange={e => handlePolicyChange('tiered_network_opted', e.target.checked)}
                          />
                          <span className="slider"></span>
                        </label>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* MEMBER TAB */}
              {activeTab === 'member' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                  {/* Member Profile */}
                  <div>
                    <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Member Demographics</h3>
                    <div className="form-grid">
                      <div className="form-group">
                        <label className="form-label">Name</label>
                        <input 
                          type="text" className="input-control" 
                          value={context.member.name} 
                          onChange={e => handleMemberChange('name', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Relationship</label>
                        <select 
                          className="input-control"
                          value={context.member.relationship}
                          onChange={e => handleMemberChange('relationship', e.target.value as any)}
                        >
                          <option value="Self">Self</option>
                          <option value="Spouse">Spouse</option>
                          <option value="Child">Child</option>
                          <option value="Parent">Parent</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label className="form-label">Current Age</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.member.age} 
                          onChange={e => handleMemberNumChange('age', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Entry Age</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.member.entry_age} 
                          onChange={e => handleMemberNumChange('entry_age', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Date of Addition</label>
                        <input 
                          type="date" className="input-control" 
                          value={context.member.date_of_addition.split('T')[0]} 
                          onChange={e => handleMemberChange('date_of_addition', e.target.value ? new Date(e.target.value).toISOString() : '')}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Financial Balances */}
                  <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1.25rem' }}>
                    <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Policy Balances</h3>
                    <div className="form-grid">
                      <div className="form-group">
                        <label className="form-label">Remaining Base SI</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.benefit_balance.base_si_remaining} 
                          onChange={e => handleBalanceChange('base_si_remaining', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Booster+ Balance</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.benefit_balance.booster_plus_remaining} 
                          onChange={e => handleBalanceChange('booster_plus_remaining', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">ReAssure Forever Pool</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.benefit_balance.reassure_forever_pool} 
                          onChange={e => handleBalanceChange('reassure_forever_pool', e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Deductible Consumed YTD</label>
                        <input 
                          type="number" className="input-control" 
                          value={context.benefit_balance.deductible_consumed_ytd} 
                          onChange={e => handleBalanceChange('deductible_consumed_ytd', e.target.value)}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Network status */}
                  <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1.25rem' }}>
                    <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Provider Network Status</h3>
                    <div className="form-grid">
                      <div className="form-group">
                        <label className="form-label">Hospital Name</label>
                        <input 
                          type="text" className="input-control" 
                          value={context.network.provider_name} 
                          onChange={e => setContext(prev => ({ ...prev, network: { ...prev.network, provider_name: e.target.value } }))}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Network Tier / Type</label>
                        <select 
                          className="input-control"
                          value={context.network.provider_type}
                          onChange={e => setContext(prev => ({ ...prev, network: { ...prev.network, provider_type: e.target.value as any } }))}
                        >
                          <option value="Network">Network</option>
                          <option value="Non-Network">Non-Network</option>
                          <option value="Excluded">Blacklisted/Excluded</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* LINE ITEMS TAB */}
              {activeTab === 'lineItems' && (
                <div className="line-items-container">
                  {context.line_items.map((item, idx) => (
                    <div key={item.line_item_id} className="line-item-card">
                      <div className="line-item-header">
                        <span className="line-item-index">Line Item #{idx + 1} — {item.line_item_id}</span>
                        {context.line_items.length > 1 && (
                          <button 
                            type="button" className="line-item-remove"
                            onClick={() => removeLineItem(idx)}
                          >
                            <Trash2 size={14} /> Remove
                          </button>
                        )}
                      </div>

                      <div className="form-grid">
                        <div className="form-group full-width">
                          <label className="form-label">Item Description</label>
                          <input 
                            type="text" className="input-control" 
                            value={item.description} 
                            onChange={e => handleLineItemChange(idx, 'description', e.target.value)}
                          />
                        </div>

                        <div className="form-group">
                          <label className="form-label">Benefit Bucket</label>
                          <select 
                            className="input-control"
                            value={item.benefit_bucket}
                            onChange={e => handleLineItemChange(idx, 'benefit_bucket', e.target.value)}
                          >
                            <option value="Expenses during Hospitalization">Expenses during Hospitalization</option>
                            <option value="Expenses before and after hospitalization">Expenses before & after Hosp</option>
                            <option value="Expenses in reaching a Hospital">Ambulance / Reaching Hospital</option>
                            <option value="Hospital Daily Cash">Hospital Daily Cash (Rider)</option>
                            <option value="Personal Accident">Personal Accident (Rider)</option>
                            <option value="Home Care / Domiciliary Treatment">Home Care / Domiciliary</option>
                            <option value="Other">Other</option>
                          </select>
                        </div>

                        <div className="form-group">
                          <label className="form-label">Claimed Amount (INR)</label>
                          <input 
                            type="number" className="input-control" 
                            value={item.claimed_amount} 
                            onChange={e => handleLineItemChange(idx, 'claimed_amount', parseFloat(e.target.value) || 0)}
                          />
                        </div>

                        <div className="form-group">
                          <label className="form-label">Condition Diagnosed</label>
                          <input 
                            type="text" className="input-control" 
                            value={item.condition_diagnosed} 
                            onChange={e => handleLineItemChange(idx, 'condition_diagnosed', e.target.value)}
                          />
                        </div>

                        {/* Room charges pro-rata optional values (API contract) */}
                        {item.benefit_bucket === "Expenses during Hospitalization" && (
                          <>
                            <div className="form-group">
                              <label className="form-label">
                                Room Category Claimed
                                <span className="form-label-desc"> (Optional contract field)</span>
                              </label>
                              <select 
                                className="input-control"
                                value={item.room_category_claimed ?? ''}
                                onChange={e => handleLineItemChange(idx, 'room_category_claimed', e.target.value === '' ? null : e.target.value)}
                              >
                                <option value="">None (Not Claimed)</option>
                                <option value="General Ward">General Ward</option>
                                <option value="Shared Room">Shared Room</option>
                                <option value="Single Private Room">Single Private Room</option>
                                <option value="Suite">Suite</option>
                              </select>
                            </div>

                            <div className="form-group">
                              <label className="form-label">
                                Actual Room Rent/Day (INR)
                                <span className="form-label-desc"> (Optional contract field)</span>
                              </label>
                              <input 
                                type="number" className="input-control" placeholder="None"
                                value={item.actual_room_rent ?? ''} 
                                onChange={e => handleLineItemNumChange(idx, 'actual_room_rent', e.target.value)}
                              />
                            </div>

                            <div className="form-group">
                              <label className="form-label">
                                Hospitalization Hours
                                <span className="form-label-desc"> (Optional contract field)</span>
                              </label>
                              <input 
                                type="number" className="input-control" placeholder="None"
                                value={item.hospitalization_hours ?? ''} 
                                onChange={e => handleLineItemNumChange(idx, 'hospitalization_hours', e.target.value)}
                              />
                            </div>

                            {/* Billing breakdown grid */}
                            <div className="form-group full-width" style={{ marginTop: '0.5rem', padding: '0.75rem', background: 'rgba(0,0,0,0.15)', borderRadius: '8px' }}>
                              <span className="form-label" style={{ fontWeight: 600, display: 'block', marginBottom: '0.5rem' }}>
                                Hospitalization Bill Itemisation (API contract for Room Pro-Rata)
                              </span>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.5rem' }}>
                                <div>
                                  <label className="form-label" style={{ fontSize: '0.7rem' }}>Room Charges</label>
                                  <input 
                                    type="number" className="input-control" style={{ padding: '0.4rem' }} placeholder="None"
                                    value={item.room_charges ?? ''} 
                                    onChange={e => handleLineItemNumChange(idx, 'room_charges', e.target.value)}
                                  />
                                </div>
                                <div>
                                  <label className="form-label" style={{ fontSize: '0.7rem' }}>Nursing Charges</label>
                                  <input 
                                    type="number" className="input-control" style={{ padding: '0.4rem' }} placeholder="None"
                                    value={item.nursing_charges ?? ''} 
                                    onChange={e => handleLineItemNumChange(idx, 'nursing_charges', e.target.value)}
                                  />
                                </div>
                                <div>
                                  <label className="form-label" style={{ fontSize: '0.7rem' }}>Practitioner Fees</label>
                                  <input 
                                    type="number" className="input-control" style={{ padding: '0.4rem' }} placeholder="None"
                                    value={item.medical_practitioner_fees ?? ''} 
                                    onChange={e => handleLineItemNumChange(idx, 'medical_practitioner_fees', e.target.value)}
                                  />
                                </div>
                                <div>
                                  <label className="form-label" style={{ fontSize: '0.7rem' }}>OT Charges</label>
                                  <input 
                                    type="number" className="input-control" style={{ padding: '0.4rem' }} placeholder="None"
                                    value={item.ot_charges ?? ''} 
                                    onChange={e => handleLineItemNumChange(idx, 'ot_charges', e.target.value)}
                                  />
                                </div>
                              </div>
                            </div>
                          </>
                        )}
                        
                        <div className="form-group">
                          <div className="switch-group" style={{ height: '100%', alignSelf: 'stretch' }}>
                            <span className="form-label">Accident Related?</span>
                            <label className="switch-control">
                              <input 
                                type="checkbox" 
                                checked={item.accident_related}
                                onChange={e => handleLineItemChange(idx, 'accident_related', e.target.checked)}
                              />
                              <span className="slider"></span>
                            </label>
                          </div>
                        </div>

                      </div>
                    </div>
                  ))}

                  <button 
                    type="button" className="add-line-item-btn"
                    onClick={addLineItem}
                  >
                    <Plus size={16} /> Add Another Line Item
                  </button>
                </div>
              )}

            </div>
          </div>

          {/* Submit/Execution Button */}
          <button 
            type="button" 
            className="submit-btn" 
            onClick={runAdjudication}
            disabled={loading}
          >
            {loading ? 'Processing through 7-Gate Pipeline...' : (
              <>
                <Play size={18} fill="currentColor" /> Run Auto-Adjudication Engine
              </>
            )}
          </button>
        </div>

        {/* Results Panel (Right Column) */}
        <div className="results-column">
          
          {/* Default Placeholder */}
          {!loading && !result && !error && (
            <div className="glass-card placeholder-result">
              <div className="placeholder-icon">
                <Activity size={32} />
              </div>
              <h3 className="placeholder-text">Adjudication Pipeline Idle</h3>
              <p className="placeholder-sub">
                Modify the Claim Context on the left and trigger the engine to run live 7-gate adjudication rules.
              </p>
            </div>
          )}

          {/* Loading Pulse */}
          {loading && (
            <div className="glass-card loading-container">
              <div className="pulse-loader"></div>
              <h3 className="loading-text">Executing Gate Diagnostics</h3>
              <p className="loading-subtext">Evaluating pro-rata, copay, deductibles & SI waterfalls...</p>
            </div>
          )}

          {/* Error Banner */}
          {error && (
            <div className="glass-card panel-section" style={{ borderLeft: '4px solid var(--color-danger)' }}>
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                <AlertCircle className="text-danger" style={{ color: 'var(--color-danger)', flexShrink: 0 }} size={24} />
                <div>
                  <h3 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--color-danger)' }}>Adjudication Pipeline Failed</h3>
                  <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>{error}</p>
                </div>
              </div>
            </div>
          )}

          {/* Adjudication Success Output */}
          {result && (
            <>
              {/* Decision Header */}
              <div className="glass-card result-header-panel"
                  style={{ overflow: 'visible', marginTop: '1.5rem' }}>
                <div className={`result-badge ${
                  result.claim_decision === 'APPROVED' ? 'approved' : 
                  result.claim_decision === 'REJECTED' ? 'rejected' : 'review'
                }`}>
                  {result.claim_decision === 'APPROVED' && <CheckCircle2 size={24} />}
                  {result.claim_decision === 'REJECTED' && <XCircle size={24} />}
                  {result.claim_decision === 'ASSISTED_REVIEW' && <AlertCircle size={24} />}
                  {result.claim_decision.replace('_', ' ')}
                </div>

                {result.review_reasons && result.review_reasons.length > 0 && (
                  <p className="result-reason" style={{ fontWeight: 500, color: 'var(--color-warning)' }}>
                    Reason: {result.review_reasons.join(', ')}
                  </p>
                )}
                
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                  Execution Duration: {(result.processing_duration_ms ?? 0).toFixed(1)} ms | Pipeline Mode: {!result.manual_review_required ? 'Auto-Adjudicated' : 'Assisted'}
                </p>
              </div>

              {/* Financial Dashboard */}
              <div className="financials-summary">
                <div className="glass-card financial-card">
                  <div className="fin-label">Total Claimed</div>
                  <div className="fin-value claimed">₹{result.total_claimed.toLocaleString('en-IN')}</div>
                </div>
                <div className="glass-card financial-card" style={{ borderTop: '2px solid var(--color-accent)' }}>
                  <div className="fin-label">Payable Amount</div>
                  <div className="fin-value payable">₹{result.total_payable.toLocaleString('en-IN')}</div>
                </div>
                <div className="glass-card financial-card" style={{ borderTop: '2px solid var(--color-danger)' }}>
                  <div className="fin-label">Deducted</div>
                  <div className="fin-value deducted">₹{result.total_deductions.toLocaleString('en-IN')}</div>
                </div>
              </div>

              {/* SI Waterfall Visualizer */}
              <div className="glass-card panel-section">
                <h3 className="panel-title" style={{ fontSize: '0.95rem', marginBottom: '1rem' }}>
                  <BarChart3 size={16} /> Sum Insured Waterfall Breakdown
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                  
                  {/* Base SI step */}
                  <div className="waterfall-step">
                    <div className="waterfall-label-row">
                      <span className="waterfall-name">Base Sum Insured</span>
                      <span className="waterfall-value">
                        Paid: ₹{result.si_waterfall_breakdown.amount_from_base_si.toLocaleString('en-IN')}
                      </span>
                    </div>
                    <div className="waterfall-bar-container">
                      <div 
                        className="waterfall-bar-fill" 
                        style={{ 
                          width: `${context.policy.base_sum_insured > 0 ? (result.si_waterfall_breakdown.amount_from_base_si / context.policy.base_sum_insured) * 100 : 0}%` 
                        }}
                      ></div>
                    </div>
                  </div>

                  {/* Booster+ step */}
                  {result.si_waterfall_breakdown.amount_from_booster > 0 && (
                    <div className="waterfall-step">
                      <div className="waterfall-label-row">
                        <span className="waterfall-name">Booster+ Accumulation Pool</span>
                        <span className="waterfall-value">
                          Paid: ₹{result.si_waterfall_breakdown.amount_from_booster.toLocaleString('en-IN')}
                        </span>
                      </div>
                      <div className="waterfall-bar-container">
                        <div 
                          className="waterfall-bar-fill" 
                          style={{ 
                            width: `${context.benefit_balance.booster_plus_remaining > 0 ? (result.si_waterfall_breakdown.amount_from_booster / context.benefit_balance.booster_plus_remaining) * 100 : 100}%`,
                            background: 'linear-gradient(90deg, var(--color-purple), #ec4899)'
                          }}
                        ></div>
                      </div>
                    </div>
                  )}

                  {/* ReAssure Forever step */}
                  {result.si_waterfall_breakdown.amount_from_forever > 0 && (
                    <div className="waterfall-step">
                      <div className="waterfall-label-row">
                        <span className="waterfall-name">ReAssure Forever Pool (Unlimited Reset)</span>
                        <span className="waterfall-value">
                          Paid: ₹{result.si_waterfall_breakdown.amount_from_forever.toLocaleString('en-IN')}
                        </span>
                      </div>
                      <div className="waterfall-bar-container">
                        <div 
                          className="waterfall-bar-fill" 
                          style={{ 
                            width: '100%',
                            background: 'linear-gradient(90deg, #ec4899, var(--color-danger))'
                          }}
                        ></div>
                      </div>
                    </div>
                  )}

                  {/* Co-pay and Deductible rows */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.75rem', padding: '0.5rem', background: 'var(--bg-tertiary)', borderRadius: '6px' }}>
                    <span>Co-payment: ₹{result.deduction_breakdown.co_payment.toLocaleString('en-IN')}</span>
                    <span>Deductible: ₹{result.deduction_breakdown.deductible.toLocaleString('en-IN')}</span>
                  </div>

                </div>
              </div>

              {/* Deductions breakdown detail */}
              {result.total_deductions > 0 && (
                <div className="glass-card panel-section">
                  <h3 className="panel-title" style={{ fontSize: '0.95rem', marginBottom: '1rem', color: 'var(--color-danger)' }}>
                    <Receipt size={16} /> Itemized Deductions
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                    {result.line_items.flatMap(li => li.deductions).map((ded, dIdx) => (
                      <div key={dIdx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', fontSize: '0.85rem' }}>
                        <div>
                          <div style={{ fontWeight: 600 }}>{ded.deduction_type.replace(/_/g, ' ').toUpperCase()}</div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{ded.reason} (Rule: {ded.rule_id})</div>
                        </div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--color-danger)' }}>
                          -₹{ded.amount.toLocaleString('en-IN')}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Rule execution audit log / traces */}
              <div className="glass-card panel-section">
                <h3 className="panel-title" style={{ fontSize: '0.95rem', marginBottom: '1rem' }}>
                  <FileText size={16} /> 7-Gate Rules Execution Audit Log
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', maxHeight: '300px', overflowY: 'auto', paddingRight: '4px' }}>
                  {result.decision_trace.map((trace, tIdx) => (
                    <div key={tIdx} className="trace-item">
                      <div className="trace-summary">
                        <div className="trace-title">
                          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', background: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px' }}>
                            {trace.gate}
                          </span>
                          <span>{trace.rule_id}</span>
                        </div>
                        <span className={`trace-status ${
                          trace.evaluation === 'PASSED' ? 'passed' :
                          trace.evaluation === 'FAILED' ? 'failed' : 'na'
                        }`}>
                          {trace.evaluation.replace('_', ' ')}
                        </span>
                      </div>
                      <p className="trace-desc">{trace.reason}</p>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

        </div>
      </main>
    </div>
  );
}

export default App;
