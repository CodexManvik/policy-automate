"""
Property-Based Mathematical Invariant Tests for Financial Calculators
"""

import pytest
from hypothesis import given, strategies as st
from calculators import (
    calculate_room_pro_rata,
    calculate_copayment,
    calculate_deductible,
    calculate_si_waterfall
)

# Standard monetary value strategy to avoid floating-point overflow during random testing
money_strategy = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
charge_strategy = st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False)


@given(
    eligible_room_rent=money_strategy,
    actual_room_rent=money_strategy,
    room_charges=charge_strategy,
    nursing_charges=charge_strategy,
    medical_practitioner_fees=charge_strategy,
    ot_charges=charge_strategy
)
def test_room_pro_rata_invariants(
    eligible_room_rent, actual_room_rent,
    room_charges, nursing_charges, medical_practitioner_fees, ot_charges
):
    result = calculate_room_pro_rata(
        eligible_room_rent=eligible_room_rent,
        actual_room_rent=actual_room_rent,
        room_charges=room_charges,
        nursing_charges=nursing_charges,
        medical_practitioner_fees=medical_practitioner_fees,
        ot_charges=ot_charges
    )
    
    # Associated expenses calculations
    room_charges_f = round(max(0.0, room_charges), 4)
    nursing_charges_f = round(max(0.0, nursing_charges), 4)
    practitioner_fees_f = round(max(0.0, medical_practitioner_fees), 4)
    ot_charges_f = round(max(0.0, ot_charges), 4)
    total_expenses = round(room_charges_f + nursing_charges_f + practitioner_fees_f + ot_charges_f, 4)
    
    # 1. Ratio never exceeds 1.0 or drops below 0.0
    assert 0.0 <= result.pro_rata_ratio <= 1.0
    
    # 2. Pro-rata deduction is strictly == 0 if actual_room_rent <= entitled_limit
    act = round(max(0.0, actual_room_rent), 4)
    elig = round(max(0.0, eligible_room_rent), 4)
    
    if act <= elig:
        assert result.deduction == 0.0
    else:
        # Strictly > 0 whenever actual_room_rent > eligible_room_rent, provided expenses > 0
        if total_expenses > 0.0:
            assert result.deduction > 0.0
            
    # 3. Conservation of money holds down to 4 decimal places
    assert round(result.payable_amount + result.deduction, 4) == total_expenses


@given(
    admissible_amount=money_strategy,
    base_copay_percent=st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    benefit_bucket=st.sampled_from(["Hospitalization", "Annual Health Check-up", "Live Healthy"]),
    heads_up_penalty=st.booleans(),
    tiered_network_penalty=st.booleans(),
    prolonged_hosp_penalty=st.booleans(),
    room_category_copay_percent=st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False)
)
def test_copayment_invariants(
    admissible_amount, base_copay_percent, benefit_bucket,
    heads_up_penalty, tiered_network_penalty, prolonged_hosp_penalty,
    room_category_copay_percent
):
    result = calculate_copayment(
        admissible_amount=admissible_amount,
        base_copay_percent=base_copay_percent,
        benefit_bucket=benefit_bucket,
        heads_up_penalty=heads_up_penalty,
        tiered_network_penalty=tiered_network_penalty,
        prolonged_hosp_penalty=prolonged_hosp_penalty,
        room_category_copay_percent=room_category_copay_percent
    )
    
    adm = round(max(0.0, admissible_amount), 4)
    
    # 1. Copay deduction never exceeds admissible amount
    assert result.copay_amount <= adm
    
    # 2. Conservation of money: payable + copay == admissible down to 4 decimal places
    assert round(result.payable_amount + result.copay_amount, 4) == adm


@given(
    payable_amount=money_strategy,
    base_si_remaining=money_strategy,
    booster_plus_remaining=money_strategy,
    reassure_forever_pool=money_strategy,
    reassure_forever_triggered=st.booleans(),
    unlimited_si_opted=st.booleans(),
    base_si_original=money_strategy
)
def test_si_waterfall_invariants(
    payable_amount, base_si_remaining, booster_plus_remaining,
    reassure_forever_pool, reassure_forever_triggered, unlimited_si_opted,
    base_si_original
):
    result = calculate_si_waterfall(
        payable_amount=payable_amount,
        base_si_remaining=base_si_remaining,
        booster_plus_remaining=booster_plus_remaining,
        reassure_forever_pool=reassure_forever_pool,
        reassure_forever_triggered=reassure_forever_triggered,
        unlimited_si_opted=unlimited_si_opted,
        base_si_original=base_si_original
    )
    
    p_amt = round(max(0.0, payable_amount), 4)
    
    # 1. Conservation of money holds down to 4 decimal places
    assert round(result.total_paid + result.shortfall, 4) == p_amt
    
    # 2. No pool balance dips below 0.0
    assert result.updated_base_si >= 0.0
    assert result.updated_booster >= 0.0
    assert result.updated_forever_pool >= 0.0


@given(
    claim_amount=money_strategy,
    annual_deductible_limit=money_strategy,
    deductible_consumed_ytd=money_strategy,
    benefit_bucket=st.sampled_from(["Hospitalization", "Annual Health Check-up", "Live Healthy"])
)
def test_deductible_invariants(
    claim_amount, annual_deductible_limit, deductible_consumed_ytd, benefit_bucket
):
    result = calculate_deductible(
        claim_amount=claim_amount,
        annual_deductible_limit=annual_deductible_limit,
        deductible_consumed_ytd=deductible_consumed_ytd,
        benefit_bucket=benefit_bucket
    )
    
    c_amt = round(max(0.0, claim_amount), 4)
    ann_limit = round(max(0.0, annual_deductible_limit), 4)
    consumed = round(max(0.0, deductible_consumed_ytd), 4)
    remaining_limit = round(max(0.0, ann_limit - consumed), 4)
    
    exempt_buckets = ["Annual Health Check-up", "Live Healthy"]
    if any(ex in benefit_bucket for ex in exempt_buckets):
        assert result.deductible_applied == 0.0
        assert result.payable_amount == c_amt
    else:
        # 1. Deductible applied never exceeds remaining deductible cap
        assert result.deductible_applied <= remaining_limit
        # 2. Deductible applied never exceeds claim amount
        assert result.deductible_applied <= c_amt
        # 3. Conservation of money holds down to 4 decimal places
        assert round(result.payable_amount + result.deductible_applied, 4) == c_amt
