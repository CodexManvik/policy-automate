/**
 * useClaimContext — Manages ClaimContext state and all its deep-nested mutators.
 *
 * Separates all data mutation logic from UI rendering.
 * Consumes the PRESETS map and exposes typed updater functions.
 */

import { useState, useEffect, useCallback } from 'react';
import type {
  ClaimContext,
  PolicyData,
  MemberData,
  LiveHealthyData,
  CashBagPlusData,
  LineItemData,
  EndorsementData,
  EndorsementType,
  BenefitBalanceData,
} from '../types/claims';

// ---------------------------------------------------------------------------
// Demo scenario presets — kept here, not in UI component
// ---------------------------------------------------------------------------

export const PRESETS: Record<string, ClaimContext> = {
  case1: {
    claim_id: 'CLM-APP-001',
    claim_received_at: '2026-06-10T12:00:00Z',
    policy: {
      policy_id: 'POL-1001', product_code: 'R3', variant: 'Classic',
      policy_start_date: '2025-01-01T00:00:00Z', policy_end_date: '2026-01-01T00:00:00Z',
      base_sum_insured: 500000.0, status: 'Active', premium_paid: true, grace_period_active: false,
      policy_type: 'individual', policy_term_years: 1, co_payment_percent: 10.0,
      annual_aggregate_deductible: null, room_category_entitled: 'Single Private Room',
      room_rent_limit: null, hospital_daily_cash_amount: null, pa_sum_insured: 100000.0,
      personal_waiting_period_months: 0, borderless_opted: false, borderless_specific_illness_opted: false,
      unlimited_si_opted: false, modern_treatments_plus_opted: false, air_ambulance_plus_opted: false,
      heads_up_opted: false, tiered_network_opted: true,
    },
    member: {
      member_id: 'MEM-9921', policy_id: 'POL-1001', name: 'Jane Doe', age: 35, entry_age: 35,
      relationship: 'Self', date_of_addition: '2025-01-01T00:00:00Z', ped_declarations: ['Diabetes'], eligibility_active: true,
    },
    history: {
      policy_id: 'POL-1001', member_id: 'MEM-9921', prior_claims_count: 0,
      total_utilized_si: 0.0, last_claim_date: null, prior_exclusions_triggered: [], claim_free_years: 1,
    },
    porting: {
      policy_id: 'POL-1001', porting_applicable: false, prior_coverage_months: 12,
      waiting_period_credit_months: 0, moratorium_eligible: false,
    },
    network: {
      provider_id: 'PROV-551', provider_name: 'City Care Hospital', provider_type: 'Network',
      tiered_network_member: false, heads_up_recommended: false,
    },
    benefit_balance: {
      policy_id: 'POL-1001', base_si_remaining: 500000.0, booster_plus_remaining: 0.0,
      reassure_forever_pool: 500000.0, cash_bag_plus_wallet: 0.0, hospital_cash_days_used: 0, deductible_consumed_ytd: 0.0,
    },
    lifetime_state: {
      policy_id: 'POL-1001', reassure_forever_triggered: false, reassure_forever_triggered_date: null,
      reassure_forever_triggered_claim_id: null, lock_the_clock_age_locked: true, lock_the_clock_entry_age: 35,
      lock_the_clock_unlocked_date: null, lock_the_clock_current_premium_age: 35, booster_plus_accumulated: 0.0,
      booster_plus_last_updated: null, convalescence_claimed: false, critical_illness_claimed: false,
      critical_illness_type: null, live_healthy: { current_points: 1000, points_snapshot_date: null },
      cash_bag_plus: { balance: 0.0, last_credited: null },
    },
    endorsements: [],
    line_items: [{
      line_item_id: 'LI-001', description: 'Inpatient Room & Nursing (Suite Upgrade)', claimed_amount: 34000.0,
      expense_date: '2026-06-08T10:00:00Z', benefit_bucket: 'Expenses during Hospitalization',
      admission_date: '2026-06-06T10:00:00Z', discharge_date: '2026-06-08T10:00:00Z', hospitalization_hours: 48.0,
      actual_room_rent: 12000.0, room_category_claimed: 'Suite', treatment_type: 'Allopathic',
      condition_diagnosed: 'Acute Appendicitis', accident_related: false, emergency: false,
      room_charges: 24000.0, nursing_charges: 10000.0, medical_practitioner_fees: 8000.0, ot_charges: 12000.0,
      doctor_advised: false, continuous_treatment: false, daily_monitoring_chart: false,
    }],
    product_json_version: 'R3_v2.1_2025-01-15', renewal_event_simulation: false,
  },
  case2: {
    claim_id: 'CLM-LTC-002',
    claim_received_at: '2026-06-10T12:00:00Z',
    policy: {
      policy_id: 'POL-2002', product_code: 'R3', variant: 'Select',
      policy_start_date: '2025-01-01T00:00:00Z', policy_end_date: '2028-01-01T00:00:00Z',
      base_sum_insured: 500000.0, status: 'Active', premium_paid: true, grace_period_active: false,
      policy_type: 'individual', policy_term_years: 3, co_payment_percent: null,
      annual_aggregate_deductible: null, room_category_entitled: 'Single Private Room',
      room_rent_limit: null, hospital_daily_cash_amount: null, pa_sum_insured: null,
      personal_waiting_period_months: 0, borderless_opted: false, borderless_specific_illness_opted: false,
      unlimited_si_opted: true, modern_treatments_plus_opted: false, air_ambulance_plus_opted: false,
      heads_up_opted: false, tiered_network_opted: false,
    },
    member: {
      member_id: 'MEM-4432', policy_id: 'POL-2002', name: 'Arjun Mehra', age: 45, entry_age: 25,
      relationship: 'Self', date_of_addition: '2025-01-01T00:00:00Z', ped_declarations: [], eligibility_active: true,
    },
    history: {
      policy_id: 'POL-2002', member_id: 'MEM-4432', prior_claims_count: 1,
      total_utilized_si: 120000.0, last_claim_date: '2025-04-10T00:00:00Z', prior_exclusions_triggered: [], claim_free_years: 0,
    },
    porting: {
      policy_id: 'POL-2002', porting_applicable: false, prior_coverage_months: 24,
      waiting_period_credit_months: 0, moratorium_eligible: false,
    },
    network: {
      provider_id: 'PROV-112', provider_name: 'Apollo Spectra Hospital', provider_type: 'Network',
      tiered_network_member: false, heads_up_recommended: false,
    },
    benefit_balance: {
      policy_id: 'POL-2002', base_si_remaining: 380000.0, booster_plus_remaining: 0.0,
      reassure_forever_pool: 500000.0, cash_bag_plus_wallet: 0.0, hospital_cash_days_used: 0, deductible_consumed_ytd: 0.0,
    },
    lifetime_state: {
      policy_id: 'POL-2002', reassure_forever_triggered: false, reassure_forever_triggered_date: null,
      reassure_forever_triggered_claim_id: null, lock_the_clock_age_locked: false, lock_the_clock_entry_age: 25,
      lock_the_clock_unlocked_date: '2026-01-01T00:00:00Z', lock_the_clock_current_premium_age: 45, booster_plus_accumulated: 0.0,
      booster_plus_last_updated: null, convalescence_claimed: false, critical_illness_claimed: false,
      critical_illness_type: null, live_healthy: { current_points: 500, points_snapshot_date: null },
      cash_bag_plus: { balance: 0.0, last_credited: null },
    },
    endorsements: [],
    line_items: [{
      line_item_id: 'LI-002', description: 'Inpatient Surgery - Hernia Repair', claimed_amount: 80000.0,
      expense_date: '2026-06-08T10:00:00Z', benefit_bucket: 'Expenses during Hospitalization',
      admission_date: '2026-06-06T10:00:00Z', discharge_date: '2026-06-08T10:00:00Z', hospitalization_hours: 48.0,
      actual_room_rent: 5000.0, room_category_claimed: 'Single Private Room', treatment_type: 'Allopathic',
      condition_diagnosed: 'Hernia Repair', accident_related: false, emergency: false,
      room_charges: 10000.0, nursing_charges: 5000.0, medical_practitioner_fees: 35000.0, ot_charges: 30000.0,
      doctor_advised: false, continuous_treatment: false, daily_monitoring_chart: false,
    }],
    product_json_version: 'R3_v2.1_2025-01-15', renewal_event_simulation: false,
  },
  case3: {
    claim_id: 'CLM-CB-003',
    claim_received_at: '2026-06-10T12:00:00Z',
    policy: {
      policy_id: 'POL-3003', product_code: 'R3', variant: 'Elite',
      policy_start_date: '2025-01-01T00:00:00Z', policy_end_date: '2026-01-01T00:00:00Z',
      base_sum_insured: 1000000.0, status: 'Active', premium_paid: true, grace_period_active: false,
      policy_type: 'individual', policy_term_years: 1, co_payment_percent: null,
      annual_aggregate_deductible: null, room_category_entitled: 'Single Private Room',
      room_rent_limit: null, hospital_daily_cash_amount: 1500.0, pa_sum_insured: null,
      personal_waiting_period_months: 0, borderless_opted: false, borderless_specific_illness_opted: false,
      unlimited_si_opted: false, modern_treatments_plus_opted: false, air_ambulance_plus_opted: false,
      heads_up_opted: false, tiered_network_opted: false,
    },
    member: {
      member_id: 'MEM-7710', policy_id: 'POL-3003', name: 'Priya Sharma', age: 29, entry_age: 29,
      relationship: 'Self', date_of_addition: '2025-01-01T00:00:00Z', ped_declarations: [], eligibility_active: true,
    },
    history: {
      policy_id: 'POL-3003', member_id: 'MEM-7710', prior_claims_count: 0,
      total_utilized_si: 0.0, last_claim_date: null, prior_exclusions_triggered: [], claim_free_years: 1,
    },
    porting: {
      policy_id: 'POL-3003', porting_applicable: false, prior_coverage_months: 12,
      waiting_period_credit_months: 0, moratorium_eligible: false,
    },
    network: {
      provider_id: 'PROV-889', provider_name: 'Fortis Healthcare', provider_type: 'Network',
      tiered_network_member: false, heads_up_recommended: false,
    },
    benefit_balance: {
      policy_id: 'POL-3003', base_si_remaining: 1000000.0, booster_plus_remaining: 0.0,
      reassure_forever_pool: 1000000.0, cash_bag_plus_wallet: 1000.0, hospital_cash_days_used: 0, deductible_consumed_ytd: 0.0,
    },
    lifetime_state: {
      policy_id: 'POL-3003', reassure_forever_triggered: false, reassure_forever_triggered_date: null,
      reassure_forever_triggered_claim_id: null, lock_the_clock_age_locked: true, lock_the_clock_entry_age: 29,
      lock_the_clock_unlocked_date: null, lock_the_clock_current_premium_age: 29, booster_plus_accumulated: 0.0,
      booster_plus_last_updated: null, convalescence_claimed: false, critical_illness_claimed: false,
      critical_illness_type: null, live_healthy: { current_points: 2800, points_snapshot_date: null },
      cash_bag_plus: { balance: 15000.0, last_credited: null },
    },
    endorsements: [],
    line_items: [{
      line_item_id: 'LI-003', description: 'Post-hospitalization physiotherapy', claimed_amount: 8000.0,
      expense_date: '2026-06-08T10:00:00Z', benefit_bucket: 'Expenses before and after hospitalization',
      admission_date: null, discharge_date: null, hospitalization_hours: null,
      actual_room_rent: null, room_category_claimed: null, treatment_type: 'Allopathic',
      condition_diagnosed: 'Post-surgical physiotherapy', accident_related: false, emergency: false,
      room_charges: null, nursing_charges: null, medical_practitioner_fees: 8000.0, ot_charges: null,
      doctor_advised: true, continuous_treatment: true, daily_monitoring_chart: false,
    }],
    product_json_version: 'R3_v2.1_2025-01-15', renewal_event_simulation: true,
  },
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseClaimContextReturn {
  context: ClaimContext;
  activePreset: string;
  endType: string;
  endVal: string;
  setEndType: (t: string) => void;
  setEndVal: (v: string) => void;
  handlePresetChange: (name: string) => void;
  updatePolicy: (key: keyof PolicyData, value: unknown) => void;
  updateMember: (key: keyof MemberData, value: unknown) => void;
  updateLiveHealthy: (key: keyof LiveHealthyData, value: unknown) => void;
  updateCashBagPlus: (key: keyof CashBagPlusData, value: unknown) => void;
  updateLineItem: (index: number, key: keyof LineItemData, value: unknown) => void;
  updateBenefitBalance: (key: keyof BenefitBalanceData, value: unknown) => void;
  toggleRenewalSimulation: () => void;
  addLineItem: () => void;
  removeLineItem: (index: number) => void;
  addEndorsement: () => boolean;
  removeEndorsement: (id: string) => void;
}

export function useClaimContext(): UseClaimContextReturn {
  const [activePreset, setActivePreset] = useState<string>('case1');
  const [context, setContext] = useState<ClaimContext>(() =>
    JSON.parse(JSON.stringify(PRESETS.case1)) as ClaimContext,
  );
  const [endType, setEndType] = useState<string>('SIEnhancement');
  const [endVal, setEndVal] = useState<string>('{\n  "base_sum_insured": 500000.0\n}');

  // Auto-populate endorsement JSON template when type changes
  useEffect(() => {
    switch (endType) {
      case 'MemberAddition':
        setEndVal(JSON.stringify({ name: 'Bob Jones', age: 28, relationship: 'Spouse' }, null, 2));
        break;
      case 'SIEnhancement':
        setEndVal(JSON.stringify({ base_sum_insured: 750000.0 }, null, 2));
        break;
      case 'IndividualToFloater':
        setEndVal(JSON.stringify({ members: [{ member_id: 'MEM-9921', booster_plus: 40000 }] }, null, 2));
        break;
      case 'FloaterSplit':
        setEndVal(JSON.stringify({ new_policies: [{ member_id: 'MEM-9921', new_sum_insured: 300000 }] }, null, 2));
        break;
      default:
        setEndVal('{}');
    }
  }, [endType]);

  const handlePresetChange = useCallback((name: string) => {
    setActivePreset(name);
    setContext(JSON.parse(JSON.stringify(PRESETS[name])) as ClaimContext);
  }, []);

  const updatePolicy = useCallback((key: keyof PolicyData, value: unknown) => {
    setContext(prev => ({ ...prev, policy: { ...prev.policy, [key]: value } }));
  }, []);

  const updateMember = useCallback((key: keyof MemberData, value: unknown) => {
    setContext(prev => ({ ...prev, member: { ...prev.member, [key]: value } }));
  }, []);

  const updateLiveHealthy = useCallback((key: keyof LiveHealthyData, value: unknown) => {
    setContext(prev => ({
      ...prev,
      lifetime_state: {
        ...prev.lifetime_state,
        live_healthy: { ...prev.lifetime_state.live_healthy, [key]: value },
      },
    }));
  }, []);

  const updateCashBagPlus = useCallback((key: keyof CashBagPlusData, value: unknown) => {
    setContext(prev => ({
      ...prev,
      lifetime_state: {
        ...prev.lifetime_state,
        cash_bag_plus: { ...prev.lifetime_state.cash_bag_plus, [key]: value },
      },
    }));
  }, []);

  const updateLineItem = useCallback((index: number, key: keyof LineItemData, value: unknown) => {
    setContext(prev => {
      const updated = [...prev.line_items];
      updated[index] = { ...updated[index], [key]: value };
      return { ...prev, line_items: updated };
    });
  }, []);

  const updateBenefitBalance = useCallback((key: keyof BenefitBalanceData, value: unknown) => {
    setContext(prev => ({ ...prev, benefit_balance: { ...prev.benefit_balance, [key]: value } }));
  }, []);

  const toggleRenewalSimulation = useCallback(() => {
    setContext(prev => ({ ...prev, renewal_event_simulation: !prev.renewal_event_simulation }));
  }, []);

  const addLineItem = useCallback(() => {
    setContext(prev => ({
      ...prev,
      line_items: [
        ...prev.line_items,
        {
          line_item_id: `LI-${Date.now()}`,
          description: 'New Line Item',
          claimed_amount: 0,
          expense_date: new Date().toISOString(),
          benefit_bucket: 'Expenses during Hospitalization',
          admission_date: null, discharge_date: null, hospitalization_hours: null,
          actual_room_rent: null, room_category_claimed: null,
          treatment_type: 'Allopathic', condition_diagnosed: '',
          accident_related: false, emergency: false,
          room_charges: null, nursing_charges: null,
          medical_practitioner_fees: null, ot_charges: null,
          doctor_advised: false, continuous_treatment: false, daily_monitoring_chart: false,
        },
      ],
    }));
  }, []);

  const removeLineItem = useCallback((index: number) => {
    setContext(prev => ({
      ...prev,
      line_items: prev.line_items.filter((_, i) => i !== index),
    }));
  }, []);

  /** Returns true on success, false on JSON parse error */
  const addEndorsement = useCallback((): boolean => {
    let parsedDetails: Record<string, unknown> = {};
    try {
      parsedDetails = JSON.parse(endVal) as Record<string, unknown>;
    } catch {
      return false;
    }
    const newEndorsement: EndorsementData = {
      endorsement_id: `END-${Date.now()}`,
      policy_id: context.policy.policy_id,
      endorsement_type: endType as EndorsementType,
      effective_date: new Date().toISOString(),
      details: parsedDetails,
    };
    setContext(prev => ({ ...prev, endorsements: [...prev.endorsements, newEndorsement] }));
    return true;
  }, [endVal, endType, context.policy.policy_id]);

  const removeEndorsement = useCallback((id: string) => {
    setContext(prev => ({
      ...prev,
      endorsements: prev.endorsements.filter(e => e.endorsement_id !== id),
    }));
  }, []);

  return {
    context, activePreset, endType, endVal,
    setEndType, setEndVal,
    handlePresetChange, updatePolicy, updateMember,
    updateLiveHealthy, updateCashBagPlus,
    updateBenefitBalance, toggleRenewalSimulation,
    updateLineItem, addLineItem, removeLineItem,
    addEndorsement, removeEndorsement,
  };
}
