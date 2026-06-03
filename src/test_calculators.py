"""
Unit Tests for Calculation Tools
Validates deterministic calculator logic against known scenarios
"""

from datetime import datetime, timedelta, timezone
from calculators import (
    calculate_waiting_period,
    calculate_room_pro_rata,
    calculate_copayment,
    calculate_deductible,
    calculate_si_waterfall,
    calculate_lock_the_clock,
    calculate_booster_accumulation,
    validate_pre_post_hosp_window
)


def test_room_pro_rata_no_breach():
    """Test room pro-rata when no breach occurs"""
    result = calculate_room_pro_rata(
        eligible_room_rent=3000.0,
        actual_room_rent=2500.0,
        room_charges=2500.0,
        nursing_charges=5000.0,
        medical_practitioner_fees=10000.0,
        ot_charges=7500.0
    )
    
    assert result.pro_rata_ratio == 1.0
    assert result.deduction == 0.0
    assert result.payable_amount == 25000.0
    print("[OK] Test passed: Room pro-rata (no breach)")


def test_room_pro_rata_with_breach():
    """Test room pro-rata when room category is breached"""
    result = calculate_room_pro_rata(
        eligible_room_rent=3000.0,
        actual_room_rent=6000.0,  # Double the eligible
        room_charges=6000.0,
        nursing_charges=5000.0,
        medical_practitioner_fees=10000.0,
        ot_charges=7500.0
    )
    
    assert result.pro_rata_ratio == 0.5  # 3000/6000
    assert result.payable_amount == 14250.0  # 50% of 28500
    assert result.deduction == 14250.0
    print("[OK] Test passed: Room pro-rata (with breach)")


def test_copayment_base_only():
    """Test co-payment with base percentage only"""
    result = calculate_copayment(
        admissible_amount=100000.0,
        base_copay_percent=0.20,  # 20%
        benefit_bucket="Expenses during Hospitalization",
        heads_up_penalty=False,
        tiered_network_penalty=False,
        prolonged_hosp_penalty=False
    )
    
    assert result.copay_amount == 20000.0
    assert result.payable_amount == 80000.0
    assert result.total_copay_percent == 20.0
    print("[OK] Test passed: Co-payment (base only)")


def test_copayment_stacking():
    """Test co-payment stacking multiple penalties"""
    result = calculate_copayment(
        admissible_amount=100000.0,
        base_copay_percent=0.10,  # 10%
        benefit_bucket="Expenses during Hospitalization",
        heads_up_penalty=True,      # +20%
        tiered_network_penalty=True, # +20%
        prolonged_hosp_penalty=True  # +10%
    )
    
    # Total: 10% + 20% + 20% + 10% = 60%
    assert result.copay_amount == 60000.0
    assert result.payable_amount == 40000.0
    assert result.total_copay_percent == 60.0
    print("[OK] Test passed: Co-payment (stacking penalties)")


def test_deductible_partial_consumption():
    """Test deductible with partial consumption"""
    result = calculate_deductible(
        claim_amount=50000.0,
        annual_deductible_limit=100000.0,
        deductible_consumed_ytd=30000.0,
        benefit_bucket="Expenses during Hospitalization"
    )
    
    # Remaining deductible: 100k - 30k = 70k
    # Claim amount: 50k (less than remaining)
    # So deduct full 50k
    assert result.deductible_applied == 50000.0
    assert result.payable_amount == 0.0
    assert result.deductible_remaining == 20000.0
    print("[OK] Test passed: Deductible (partial consumption)")


def test_deductible_full_exhausted():
    """Test deductible when already exhausted"""
    result = calculate_deductible(
        claim_amount=50000.0,
        annual_deductible_limit=100000.0,
        deductible_consumed_ytd=100000.0,  # Already exhausted
        benefit_bucket="Expenses during Hospitalization"
    )
    
    assert result.deductible_applied == 0.0
    assert result.payable_amount == 50000.0
    assert result.deductible_remaining == 0.0
    print("[OK] Test passed: Deductible (exhausted)")


def test_si_waterfall_base_only():
    """Test SI waterfall using base SI only"""
    result = calculate_si_waterfall(
        payable_amount=200000.0,
        base_si_remaining=1000000.0,
        booster_plus_remaining=500000.0,
        reassure_forever_pool=1000000.0,
        reassure_forever_triggered=True,
        unlimited_si_opted=False,
        base_si_original=1000000.0
    )
    
    assert result.amount_from_base_si == 200000.0
    assert result.amount_from_booster == 0.0
    assert result.amount_from_forever == 0.0
    assert result.total_paid == 200000.0
    assert result.shortfall == 0.0
    assert result.updated_base_si == 800000.0
    print("[OK] Test passed: SI Waterfall (base only)")


def test_si_waterfall_with_booster():
    """Test SI waterfall using base + booster"""
    result = calculate_si_waterfall(
        payable_amount=1200000.0,
        base_si_remaining=1000000.0,
        booster_plus_remaining=500000.0,
        reassure_forever_pool=1000000.0,
        reassure_forever_triggered=True,
        unlimited_si_opted=False,
        base_si_original=1000000.0
    )
    
    assert result.amount_from_base_si == 1000000.0
    assert result.amount_from_booster == 200000.0
    assert result.amount_from_forever == 0.0
    assert result.total_paid == 1200000.0
    assert result.shortfall == 0.0
    print("[OK] Test passed: SI Waterfall (base + booster)")


def test_si_waterfall_with_forever():
    """Test SI waterfall using all three pools"""
    result = calculate_si_waterfall(
        payable_amount=1800000.0,
        base_si_remaining=1000000.0,
        booster_plus_remaining=500000.0,
        reassure_forever_pool=1000000.0,
        reassure_forever_triggered=True,
        unlimited_si_opted=False,
        base_si_original=1000000.0
    )
    
    # Base: 1M, Booster: 500k, Forever: 300k (claim needs 1.8M total)
    assert result.amount_from_base_si == 1000000.0
    assert result.amount_from_booster == 500000.0
    assert result.amount_from_forever == 300000.0
    assert result.total_paid == 1800000.0
    assert result.shortfall == 0.0
    print("[OK] Test passed: SI Waterfall (all pools)")


def test_waiting_period_accident_exemption():
    """Test waiting period exemption for accidents"""
    result = calculate_waiting_period(
        condition="Fracture",
        policy_inception_date=datetime.now(timezone.utc) - timedelta(days=10),
        continuous_coverage_months=0,
        accident_flag=True
    )
    
    assert result.exclusion_active == False
    assert result.remaining_days == 0
    print("[OK] Test passed: Waiting period (accident exemption)")


def test_waiting_period_initial_wait():
    """Test initial 30-day waiting period"""
    result = calculate_waiting_period(
        condition="Fever",
        policy_inception_date=datetime.now(timezone.utc) - timedelta(days=20),
        continuous_coverage_months=0,
        accident_flag=False
    )
    
    assert result.exclusion_active == True
    assert result.remaining_days == 10
    assert result.rule_applied == "R3_EXCL_003"
    print("[OK] Test passed: Waiting period (initial 30 days)")


def test_waiting_period_declared_ped():
    """Test declared PED waiting period validation"""
    # Declared PED condition
    result = calculate_waiting_period(
        condition="Diabetes Mellitus",
        policy_inception_date=datetime.now(timezone.utc) - timedelta(days=100),
        continuous_coverage_months=12,
        accident_flag=False,
        ped_declarations=["Diabetes"]
    )
    assert result.exclusion_active == True
    assert result.rule_applied == "R3_EXCL_001"
    
    # Non-declared condition (should be cleared since it's not a specified disease and initial wait cleared)
    result_non_ped = calculate_waiting_period(
        condition="Appendicitis",
        policy_inception_date=datetime.now(timezone.utc) - timedelta(days=100),
        continuous_coverage_months=12,
        accident_flag=False,
        ped_declarations=["Diabetes"]
    )
    assert result_non_ped.exclusion_active == False
    print("[OK] Test passed: Waiting period (declared PED and non-PED)")


def test_pre_post_window_pre_eligible():
    """Test pre-hospitalization window eligibility"""
    admission = datetime(2025, 6, 15, 10, 0)
    expense = datetime(2025, 6, 1, 10, 0)  # 14 days before
    discharge = datetime(2025, 6, 20, 10, 0)
    
    result = validate_pre_post_hosp_window(
        expense_date=expense,
        admission_date=admission,
        discharge_date=discharge,
        pre_hosp_days_limit=60,
        post_hosp_days_limit=180,
        expense_condition="Appendicitis",
        hospitalization_condition="Appendicitis"
    )
    
    assert result.eligible == True
    assert result.window_type == "pre"
    assert result.days_from_event == 14
    print("[OK] Test passed: Pre/Post window (pre-eligible)")


def test_pre_post_window_post_eligible():
    """Test post-hospitalization window eligibility"""
    admission = datetime(2025, 6, 15, 10, 0)
    discharge = datetime(2025, 6, 20, 10, 0)
    expense = datetime(2025, 7, 15, 10, 0)  # 25 days after
    
    result = validate_pre_post_hosp_window(
        expense_date=expense,
        admission_date=admission,
        discharge_date=discharge,
        pre_hosp_days_limit=60,
        post_hosp_days_limit=180,
        expense_condition="Appendicitis",
        hospitalization_condition="Appendicitis"
    )
    
    assert result.eligible == True
    assert result.window_type == "post"
    assert result.days_from_event == 25
    print("[OK] Test passed: Pre/Post window (post-eligible)")


def run_all_tests():
    """Run all calculator tests"""
    print("\n" + "="*70)
    print("Running Calculator Unit Tests")
    print("="*70 + "\n")
    
    tests = [
        test_room_pro_rata_no_breach,
        test_room_pro_rata_with_breach,
        test_copayment_base_only,
        test_copayment_stacking,
        test_deductible_partial_consumption,
        test_deductible_full_exhausted,
        test_si_waterfall_base_only,
        test_si_waterfall_with_booster,
        test_si_waterfall_with_forever,
        test_waiting_period_accident_exemption,
        test_waiting_period_initial_wait,
        test_waiting_period_declared_ped,
        test_pre_post_window_pre_eligible,
        test_pre_post_window_post_eligible
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] Test failed: {test.__name__}")
            print(f"  Error: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] Test error: {test.__name__}")
            print(f"  Error: {e}")
            failed += 1
    
    print("\n" + "="*70)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("="*70 + "\n")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
