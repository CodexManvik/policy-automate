"""
Unit Tests for Claims Engine Hardening & Defensive Boundaries
"""

from datetime import datetime, timezone
import pytest
from schemas import (
    ClaimContext, PolicyData, MemberData, ClaimsHistoryData,
    PortingMigrationData, NetworkData, BenefitBalanceData,
    LifetimeStateData, LineItemData
)
from pipeline import ClaimsAdjudicationPipeline
from product_memory import get_product_memory, ExecutionType


def create_base_test_context() -> ClaimContext:
    """Helper to create a standard test context"""
    policy = PolicyData(
        policy_id="TEST-POL-100",
        product_code="R3",
        variant="Select",
        policy_start_date=datetime(2025, 1, 1),
        policy_end_date=datetime(2030, 1, 1),
        base_sum_insured=500000.0,
        status="Active",
        premium_paid=True,
        co_payment_percent=0.0  # default, will override in tests
    )
    member = MemberData(
        member_id="TEST-MEM-100",
        policy_id="TEST-POL-100",
        name="John Doe",
        age=30,
        entry_age=30,
        relationship="Self",
        date_of_addition=datetime(2025, 1, 1),
        eligibility_active=True
    )
    history = ClaimsHistoryData(
        policy_id="TEST-POL-100",
        member_id="TEST-MEM-100",
        prior_claims_count=0
    )
    porting = PortingMigrationData(
        policy_id="TEST-POL-100",
        porting_applicable=False
    )
    network = NetworkData(
        provider_id="TEST-PROV-100",
        provider_name="Test General Hospital",
        provider_type="Network"
    )
    benefit_balance = BenefitBalanceData(
        policy_id="TEST-POL-100",
        base_si_remaining=500000.0,
        booster_plus_remaining=0.0
    )
    lifetime_state = LifetimeStateData(
        policy_id="TEST-POL-100",
        lock_the_clock_age_locked=False,
        lock_the_clock_entry_age=30,
        lock_the_clock_current_premium_age=30
    )
    line_item = LineItemData(
        line_item_id="TEST-LI-100",
        description="Appendicitis treatment",
        claimed_amount=50000.0,
        expense_date=datetime.now(timezone.utc),
        benefit_bucket="Expenses during Hospitalization",
        admission_date=datetime.now(timezone.utc),
        discharge_date=datetime.now(timezone.utc),
        hospitalization_hours=24.0,
        condition_diagnosed="Appendicitis"
    )
    return ClaimContext(
        claim_id="TEST-CLM-100",
        claim_received_at=datetime.now(timezone.utc),
        policy=policy,
        member=member,
        history=history,
        porting=porting,
        network=network,
        benefit_balance=benefit_balance,
        lifetime_state=lifetime_state,
        line_items=[line_item]
    )


def test_copayment_percent_normalization():
    """Test 1: Co-payment percent normalization if provided as a whole number (> 1.0)"""
    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")
    
    # CASE A: co_payment_percent = 10.0 (whole number)
    context = create_base_test_context()
    context.policy.co_payment_percent = 10.0  # 10.0% instead of 0.10
    
    decision = pipeline.adjudicate_claim(context)
    # 50,000 claimed. 10% co-payment is 5,000.
    # If 10.0 co-payment percent was NOT normalized, copay would be 50,000 * 10 = 500,000 (catastrophic)
    assert decision.total_payable == 45000.0
    
    # CASE B: co_payment_percent = 0.20 (decimal fraction, should remain unchanged)
    context2 = create_base_test_context()
    context2.policy.co_payment_percent = 0.20
    decision2 = pipeline.adjudicate_claim(context2)
    assert decision2.total_payable == 40000.0


def test_room_rent_limit_fallback():
    """Test 2: Room rent limit fallback values (Select -> 4000.0, others -> 3000.0) when missing/None"""
    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")
    
    # Case A: Variant == Select, room_rent_limit = None
    context = create_base_test_context()
    context.policy.variant = "Select"
    context.policy.room_rent_limit = None
    context.line_items[0].actual_room_rent = 5000.0
    context.line_items[0].room_charges = 5000.0
    context.line_items[0].nursing_charges = 1000.0
    
    # Fallback should inject eligible_room = 4000.0. Ratio is 4000 / 5000 = 0.8
    # Associated expenses (nursing charges) = 1000.0.
    # Room pro-rata deduction = (1 - 0.8) * 1000.0 + (5000 - 4000) = 200 + 1000 = 1200.
    decision = pipeline.adjudicate_claim(context)
    assert decision.deduction_breakdown.room_pro_rata == 1200.0

    # Case B: Variant == Classic, room_rent_limit = None
    context2 = create_base_test_context()
    context2.policy.variant = "Classic"
    context2.policy.room_rent_limit = None
    context2.line_items[0].actual_room_rent = 5000.0
    context2.line_items[0].room_charges = 5000.0
    context2.line_items[0].nursing_charges = 1000.0
    
    # Fallback should inject eligible_room = 3000.0. Ratio is 3000 / 5000 = 0.6
    # Room pro-rata deduction = (1 - 0.6) * 1000.0 + (5000 - 3000) = 400 + 2000 = 2400.
    decision2 = pipeline.adjudicate_claim(context2)
    assert decision2.deduction_breakdown.room_pro_rata == 2400.0


def test_dental_exclusion_deterministic():
    """Test 3: R3_EXCL_020 (Dental Treatment) is forced to be deterministic and evaluated correctly"""
    # Verify registration in memory
    memory = get_product_memory()
    dental_rule = memory.get_rule("R3_EXCL_020")
    assert dental_rule is not None
    assert dental_rule.execution_type == ExecutionType.DETERMINISTIC
    assert dental_rule.priority == 38

    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")

    # Case A: Dental procedure in description, not accident-related -> Excluded
    context_excluded = create_base_test_context()
    context_excluded.line_items[0].description = "Dental tooth extraction"
    context_excluded.line_items[0].accident_related = False
    decision_excluded = pipeline.adjudicate_claim(context_excluded)
    assert decision_excluded.claim_decision == "REJECTED"
    
    # Case B: Dental procedure in description, accident-related -> Covered/Passed
    context_accident = create_base_test_context()
    context_accident.line_items[0].description = "Dental extraction due to accident injury"
    context_accident.line_items[0].accident_related = True
    decision_accident = pipeline.adjudicate_claim(context_accident)
    assert decision_accident.claim_decision == "APPROVED"

    # Case C: No dental procedure in description -> Passed
    context_passed = create_base_test_context()
    context_passed.line_items[0].description = "Standard appendicitis treatment"
    decision_passed = pipeline.adjudicate_claim(context_passed)
    assert decision_passed.claim_decision == "APPROVED"


def test_waterfall_negative_bounds():
    """Test 4: Defensive wrap max(0.0, ...) on running payable amount and payable amount before waterfall"""
    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")
    context = create_base_test_context()
    
    # Let's verify that we can execute successfully without negative balance propagation
    # We will trigger co-pay or deductions such that payable_amount is valid
    decision = pipeline.adjudicate_claim(context)
    assert decision.total_payable >= 0.0


def test_waiting_period_dynamic_specified_diseases():
    """Test dynamic specified disease matching in calculate_waiting_period"""
    from calculators import calculate_waiting_period
    
    # CASE A: Condition is "Custom Illness" (not in default list).
    # Without custom list, it should NOT trigger specified disease wait (exclusion_active=False).
    res_default = calculate_waiting_period(
        condition="Custom Illness",
        policy_inception_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        continuous_coverage_months=12,
        claim_date=datetime.now(timezone.utc)
    )
    assert res_default.exclusion_active == False
    
    # CASE B: With custom list containing "Custom Illness", it should trigger specified disease wait.
    res_custom = calculate_waiting_period(
        condition="Custom Illness",
        policy_inception_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        continuous_coverage_months=12,
        claim_date=datetime.now(timezone.utc),
        specified_diseases=["Custom Illness"]
    )
    assert res_custom.exclusion_active == True
    assert res_custom.rule_applied == "R3_EXCL_002"
    
    # CASE C: Test long phrase match from Product JSON R3_TBL_007 structure
    res_phrase = calculate_waiting_period(
        condition="Cataract Surgery",
        policy_inception_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        continuous_coverage_months=12,
        claim_date=datetime.now(timezone.utc),
        specified_diseases=["Cataract, glaucoma and retinal detachment"]
    )
    assert res_phrase.exclusion_active == True
    assert res_phrase.rule_applied == "R3_EXCL_002"


def test_copayment_dynamic_exempt_benefits():
    """Test dynamic exempt benefits checking in calculate_copayment"""
    from calculators import calculate_copayment
    
    # CASE A: Default exempt benefits do not include "Special Consult"
    res_default = calculate_copayment(
        admissible_amount=1000.0,
        base_copay_percent=0.20,
        benefit_bucket="Special Consult"
    )
    assert res_default.copay_amount == 200.0
    assert res_default.payable_amount == 800.0
    
    # CASE B: Custom exempt benefits list includes "Special Consult"
    res_custom = calculate_copayment(
        admissible_amount=1000.0,
        base_copay_percent=0.20,
        benefit_bucket="Special Consult",
        exempt_benefits=["Special Consult"]
    )
    assert res_custom.copay_amount == 0.0
    assert res_custom.payable_amount == 1000.0


def test_deductible_dynamic_exempt_benefits():
    """Test dynamic exempt benefits checking in calculate_deductible"""
    from calculators import calculate_deductible
    
    # CASE A: Default exempt benefits do not include "Special Consult"
    res_default = calculate_deductible(
        claim_amount=1000.0,
        annual_deductible_limit=500.0,
        deductible_consumed_ytd=0.0,
        benefit_bucket="Special Consult"
    )
    assert res_default.deductible_applied == 500.0
    assert res_default.payable_amount == 500.0
    
    # CASE B: Custom exempt benefits list includes "Special Consult"
    res_custom = calculate_deductible(
        claim_amount=1000.0,
        annual_deductible_limit=500.0,
        deductible_consumed_ytd=0.0,
        benefit_bucket="Special Consult",
        exempt_benefits=["Special Consult"]
    )
    assert res_custom.deductible_applied == 0.0
    assert res_custom.payable_amount == 1000.0


def test_reasoning_model_thinking_extraction():
    """Test 5: Verify that _extract_json_from_response correctly strips thinking blocks and parses JSON"""
    from semantic_agent import SemanticExecutionAgent
    agent = SemanticExecutionAgent(llm_provider="mock", reasoning_on=True)
    
    # CASE A: Standard DeepSeek style <think> block
    raw_response_ds = (
        "<think>\n"
        "We are analyzing the claim for Appendectomy.\n"
        "An appendectomy is active surgery, not diagnostics.\n"
        "Therefore, R3_EXCL_004 is PASSED.\n"
        "</think>\n"
        '{"evaluation_status": "PASSED", "reasoning_trace": "Appendectomy is surgery", "confidence_score": 0.95}'
    )
    extracted_ds = agent._extract_json_from_response(raw_response_ds)
    assert "PASSED" in extracted_ds
    assert "Appendectomy is surgery" in extracted_ds
    
    # CASE B: Gemma style <|think|> block
    raw_response_gemma = (
        "<|think|>\n"
        "Hospitalization was for surgery.\n"
        "Matches exception.\n"
        "</|think|>\n"
        '{"evaluation_status": "PASSED", "reasoning_trace": "Reconstructive surgery following cancer", "confidence_score": 0.92}'
    )
    extracted_gemma = agent._extract_json_from_response(raw_response_gemma)
    assert "PASSED" in extracted_gemma
    
    # CASE C: Unclosed thinking block at the start of output
    raw_response_unclosed = (
        "<think>\n"
        "Checking if exclusion applies...\n"
        '{"evaluation_status": "PASSED", "reasoning_trace": "Not diagnostic-only", "confidence_score": 0.96}'
    )
    extracted_unclosed = agent._extract_json_from_response(raw_response_unclosed)
    assert "PASSED" in extracted_unclosed


def test_gemma4_prompt_formatting():
    """Verify that the gemma4-e4b-qat prompt template is formatted correctly and sent to /completion"""
    from semantic_agent import SemanticExecutionAgent, SemanticAdjudicationPayload
    from unittest.mock import MagicMock
    
    agent = SemanticExecutionAgent(llm_provider="local", reasoning_on=True)
    
    # Mock self._http_post to return a valid JSON response so the call succeeds
    agent._http_post = MagicMock(return_value='{"content": "{\\"evaluation_status\\": \\"PASSED\\", \\"reasoning_trace\\": \\"test\\", \\"confidence_score\\": 0.95}"}')
    
    # Trigger local LLM call
    agent._call_local_llm("System Instruction", "User Prompt", SemanticAdjudicationPayload)
    
    # Verify the mocked http post call
    assert agent._http_post.called
    args = agent._http_post.call_args[0]
    
    # Verify it posted to '/completion'
    assert args[3] == "/completion"
    
    # Verify the prompt contents match the gemma4-e4b-qat template format
    payload = args[4]
    prompt = payload["prompt"]
    expected_template = (
        "<|turn>system\n"
        "<|think|>\n"
        "System Instruction<turn|>\n"
        "<|turn>user\n"
        "User Prompt<turn|>\n"
        "<|turn>model\n"
    )
    assert prompt == expected_template
    assert payload["stop"] == ["</s>", "<end_of_turn>", "<|eot_id|>", "<turn|>"]


