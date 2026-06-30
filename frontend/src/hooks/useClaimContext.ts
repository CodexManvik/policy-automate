/**
 * useClaimContext — Manages ClaimContext state and all its deep-nested mutators.
 *
 * Separates all data mutation logic from UI rendering.
 * Consumes the PRESETS map (imported from src/data/presets.ts) and exposes
 * typed updater functions.
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
import { PRESETS } from '../data/presets';
import {
  fetchPolicyFromDb,
  fetchMemberFromDb,
  fetchClaimsHistoryFromDb,
  fetchBalancesFromDb,
  fetchLifetimeStateFromDb,
  fetchEndorsementsFromDb,
  fetchPortingFromDb,
} from '../services/adjudicationApi';

// Re-export so existing consumers that import PRESETS from this module keep working.
export { PRESETS };

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseClaimContextReturn {
  context: ClaimContext;
  activePreset: string;
  endType: string;
  endVal: string;
  syncing: boolean;
  syncError: string | null;
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
  syncFromDb: (memberId: string) => Promise<void>;
}

export function useClaimContext(): UseClaimContextReturn {
  const [activePreset, setActivePreset] = useState<string>('case1');
  const [context, setContext] = useState<ClaimContext>(() =>
    JSON.parse(JSON.stringify(PRESETS.case1)) as ClaimContext,
  );
  const [endType, setEndType] = useState<string>('SIEnhancement');
  const [endVal, setEndVal] = useState<string>('{\n  "base_sum_insured": 500000.0\n}');
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncError, setSyncError] = useState<string | null>(null);

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

  const syncFromDb = useCallback(async (memberId: string) => {
    if (!memberId.trim()) {
      setSyncError('Member ID is required to sync from DB.');
      return;
    }
    setSyncing(true);
    setSyncError(null);
    try {
      // Step 1: Look up member details first to resolve policy_id
      const memberRes = await fetchMemberFromDb(memberId);
      const policyId = memberRes.policy_id;

      // Step 2: Fetch remaining policy and profile ledger data in parallel
      const [
        policyRes,
        historyRes,
        balancesRes,
        lifetimeRes,
        endorsementsRes,
        portingRes,
      ] = await Promise.all([
        fetchPolicyFromDb(policyId),
        fetchClaimsHistoryFromDb(policyId, memberId).catch(() => null),
        fetchBalancesFromDb(policyId, memberId).catch(() => null),
        fetchLifetimeStateFromDb(policyId).catch(() => null),
        fetchEndorsementsFromDb(policyId).catch(() => null),
        fetchPortingFromDb(policyId).catch(() => null),
      ]);

      const normalizedPolicy: PolicyData = {
        policy_id: policyRes.policy_id,
        product_code: policyRes.product_code,
        variant: policyRes.policy_variant || policyRes.variant || 'Classic',
        policy_start_date: policyRes.policy_start_date,
        policy_end_date: policyRes.policy_end_date,
        base_sum_insured: Number(policyRes.base_sum_insured),
        status: policyRes.status || 'Active',
        premium_paid: Boolean(policyRes.premium_paid),
        grace_period_active: Boolean(policyRes.grace_period_active),
        policy_type: policyRes.policy_type || 'individual',
        policy_term_years: Number(policyRes.policy_term_years || 1),
        co_payment_percent: policyRes.co_pay_option !== undefined && policyRes.co_pay_option !== null ? Number(policyRes.co_pay_option) * 100 : null,
        annual_aggregate_deductible: policyRes.deductible_option !== undefined && policyRes.deductible_option !== null ? Number(policyRes.deductible_option) : null,
        room_category_entitled: policyRes.room_category_entitled || 'Single Private Room',
        room_rent_limit: policyRes.room_charges || null,
        hospital_daily_cash_amount: policyRes.hospital_daily_cash_amount !== undefined && policyRes.hospital_daily_cash_amount !== null ? Number(policyRes.hospital_daily_cash_amount) : null,
        pa_sum_insured: policyRes.pa_sum_insured !== undefined && policyRes.pa_sum_insured !== null ? Number(policyRes.pa_sum_insured) : null,
        personal_waiting_period_months: Number(policyRes.personal_waiting_period_months || 0),
        borderless_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('borderless'),
        borderless_specific_illness_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('borderless_specific_illness'),
        unlimited_si_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('unlimited_si'),
        modern_treatments_plus_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('modern_treatments_plus'),
        air_ambulance_plus_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('air_ambulance_plus'),
        heads_up_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('heads_up'),
        tiered_network_opted: Array.isArray(policyRes.optional_riders) && policyRes.optional_riders.includes('tiered_network'),
      };

      const normalizedMember: MemberData = {
        member_id: memberRes.member_id,
        policy_id: memberRes.policy_id,
        name: memberRes.name || 'Unknown',
        age: Number(memberRes.age),
        entry_age: Number(memberRes.entry_age || memberRes.age),
        relationship: memberRes.relationship || 'Self',
        date_of_addition: memberRes.date_of_addition,
        ped_declarations: Array.isArray(memberRes.ped_declarations) ? memberRes.ped_declarations : [],
        eligibility_active: memberRes.eligibility_active !== undefined ? Boolean(memberRes.eligibility_active) : true,
      };

      const normalizedHistory = historyRes ? {
        policy_id: historyRes.policy_id,
        member_id: historyRes.member_id,
        prior_claims_count: Number(historyRes.prior_claims_count || 0),
        total_utilized_si: Number(historyRes.total_prior_amount_paid || historyRes.total_utilized_si || 0.0),
        last_claim_date: historyRes.last_claim_date || null,
        prior_exclusions_triggered: Array.isArray(historyRes.cumulative_exclusions_triggered)
          ? historyRes.cumulative_exclusions_triggered
          : Array.isArray(historyRes.prior_exclusions_triggered) ? historyRes.prior_exclusions_triggered : [],
        claim_free_years: Number(historyRes.claim_free_years || 0),
      } : {
        policy_id: policyId,
        member_id: memberId,
        prior_claims_count: 0,
        total_utilized_si: 0.0,
        last_claim_date: null,
        prior_exclusions_triggered: [],
        claim_free_years: 0,
      };

      const normalizedPorting = portingRes ? {
        policy_id: portingRes.policy_id,
        porting_applicable: Boolean(portingRes.is_ported_policy),
        prior_coverage_months: Number(portingRes.continuous_coverage_months || 0),
        waiting_period_credit_months: Number(portingRes.waiting_period_credit_months || 0),
        moratorium_eligible: Boolean(portingRes.moratorium_eligible_months),
      } : {
        policy_id: policyId,
        porting_applicable: false,
        prior_coverage_months: 0,
        waiting_period_credit_months: 0,
        moratorium_eligible: false,
      };

      const normalizedBalances = balancesRes ? {
        policy_id: balancesRes.policy_id,
        base_si_remaining: Number(balancesRes.base_si_remaining),
        booster_plus_remaining: Number(balancesRes.booster_plus_remaining),
        reassure_forever_pool: Number(balancesRes.reassure_forever_pool),
        cash_bag_plus_wallet: Number(balancesRes.cash_bag_plus_wallet_balance || 0.0),
        hospital_cash_days_used: Number(balancesRes.hospital_cash_days_used || 0),
        deductible_consumed_ytd: Number(balancesRes.deductible_consumed_ytd || 0.0),
      } : {
        policy_id: policyId,
        base_si_remaining: normalizedPolicy.base_sum_insured,
        booster_plus_remaining: 0.0,
        reassure_forever_pool: normalizedPolicy.base_sum_insured,
        cash_bag_plus_wallet: 0.0,
        hospital_cash_days_used: 0,
        deductible_consumed_ytd: 0.0,
      };

      const normalizedLifetime = lifetimeRes ? {
        policy_id: lifetimeRes.policy_id,
        reassure_forever_triggered: Boolean(lifetimeRes.reassure_forever_triggered),
        reassure_forever_triggered_date: lifetimeRes.reassure_forever_triggered_date || null,
        reassure_forever_triggered_claim_id: lifetimeRes.reassure_forever_triggered_claim_id || null,
        lock_the_clock_age_locked: Boolean(lifetimeRes.lock_the_clock_age_locked),
        lock_the_clock_entry_age: Number(lifetimeRes.lock_the_clock_entry_age || normalizedMember.entry_age),
        lock_the_clock_unlocked_date: lifetimeRes.lock_the_clock_unlocked_date || null,
        lock_the_clock_current_premium_age: Number(lifetimeRes.lock_the_clock_current_premium_age || normalizedMember.age),
        booster_plus_accumulated: Number(lifetimeRes.booster_plus_accumulated || 0.0),
        booster_plus_last_updated: lifetimeRes.booster_plus_last_updated || null,
        convalescence_claimed: Boolean(lifetimeRes.convalescence_claimed),
        critical_illness_claimed: Boolean(lifetimeRes.critical_illness_claimed),
        critical_illness_type: lifetimeRes.critical_illness_type || null,
        live_healthy: {
          current_points: Number(lifetimeRes.live_healthy_points !== undefined ? lifetimeRes.live_healthy_points : (lifetimeRes.live_healthy && lifetimeRes.live_healthy.current_points) || 0),
          points_snapshot_date: null,
        },
        cash_bag_plus: {
          balance: Number(lifetimeRes.cash_bag_balance !== undefined ? lifetimeRes.cash_bag_balance : (lifetimeRes.cash_bag_plus && lifetimeRes.cash_bag_plus.balance) || 0.0),
          last_credited: null,
        },
      } : {
        policy_id: policyId,
        reassure_forever_triggered: false, reassure_forever_triggered_date: null, reassure_forever_triggered_claim_id: null,
        lock_the_clock_age_locked: false, lock_the_clock_entry_age: normalizedMember.entry_age, lock_the_clock_unlocked_date: null,
        lock_the_clock_current_premium_age: normalizedMember.age, booster_plus_accumulated: 0.0, booster_plus_last_updated: null,
        convalescence_claimed: false, critical_illness_claimed: false, critical_illness_type: null,
        live_healthy: { current_points: 0, points_snapshot_date: null },
        cash_bag_plus: { balance: 0.0, last_credited: null },
      };

      const normalizedEndorsements = endorsementsRes && Array.isArray(endorsementsRes.endorsements)
        ? endorsementsRes.endorsements.map((e: any) => ({
            endorsement_id: e.endorsement_id || `END-${Date.now()}-${Math.random()}`,
            policy_id: e.policy_id,
            endorsement_type: e.endorsement_type || e.type,
            effective_date: e.effective_date,
            details: e.details || e.mutated_fields || {},
          }))
        : [];

      setContext(prev => ({
        ...prev,
        policy: normalizedPolicy,
        member: normalizedMember,
        history: normalizedHistory,
        porting: normalizedPorting,
        benefit_balance: normalizedBalances,
        lifetime_state: normalizedLifetime,
        endorsements: normalizedEndorsements,
      }));
    } catch (err: any) {
      setSyncError(err.message || 'Unknown database fetch error.');
    } finally {
      setSyncing(false);
    }
  }, []);

  const handlePresetChange = useCallback((name: string) => {
    setActivePreset(name);
    const newPreset = JSON.parse(JSON.stringify(PRESETS[name])) as ClaimContext;
    setContext(newPreset);
    if (newPreset.member && newPreset.member.member_id) {
      void syncFromDb(newPreset.member.member_id);
    }
  }, [syncFromDb]);

  return {
    context, activePreset, endType, endVal, syncing, syncError,
    setEndType, setEndVal,
    handlePresetChange, updatePolicy, updateMember,
    updateLiveHealthy, updateCashBagPlus,
    updateBenefitBalance, toggleRenewalSimulation,
    updateLineItem, addLineItem, removeLineItem,
    addEndorsement, removeEndorsement, syncFromDb,
  };
}
