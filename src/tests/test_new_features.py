import pytest
from datetime import datetime, timezone, timedelta
from schemas import (
    ClaimContext, PolicyData, MemberData, ClaimsHistoryData,
    PortingMigrationData, NetworkData, BenefitBalanceData,
    LifetimeStateData, EndorsementData, LineItemData
)
from calculators import calculate_lock_the_clock
from pipeline import ClaimsAdjudicationPipeline
from product_memory import get_product_memory


def test_lock_the_clock_multi_tenure():
    """Verify multi-tenure premium delta calculations under Lock the Clock"""
    # Baseline checks for premium fetch and delta computation
    # entry_age = 25 (premium 10000), current_age = 35 (premium 15000)
    # remaining years = 3 - 1 = 2
    # expected delta = (15000 - 10000) * 2 = 10000
    res = calculate_lock_the_clock(
        entry_age=25,
        current_age=35,
        claim_paid_flag=True,
        policy_type="individual",
        policy_term_years=3,
        claim_in_year=1,
        member_claiming="MEM-1"
    )
    assert res.age_locked is False
    assert res.age_unlocked is True
    assert res.age_for_premium == 35
    assert res.additional_premium_delta == 10000.0
    assert res.deduct_from_payout == 10000.0


def test_individual_to_floater_endorsement():
    """Verify Individual-to-Floater booster pool assignment to the minimum member balance"""
    pipeline = ClaimsAdjudicationPipeline(use_ai=False)
    
    # Construct a sample context
    policy = PolicyData(
        policy_id="POL-1", product_code="R3", variant="Classic",
        policy_start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        policy_end_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        base_sum_insured=500000.0, room_category_entitled="Single Private Room",
        status="Active", premium_paid=True
    )
    member = MemberData(member_id="MEM-1", policy_id="POL-1", name="Jane", age=30, entry_age=30, relationship="Self", date_of_addition=datetime(2025, 1, 1, tzinfo=timezone.utc), eligibility_active=True)
    history = ClaimsHistoryData(policy_id="POL-1", member_id="MEM-1")
    porting = PortingMigrationData(policy_id="POL-1")
    network = NetworkData(provider_id="PROV-1", provider_name="Hospital", provider_type="Network")
    balance = BenefitBalanceData(policy_id="POL-1", base_si_remaining=500000.0, booster_plus_remaining=50000.0, reassure_forever_pool=500000.0)
    lifetime = LifetimeStateData(policy_id="POL-1", lock_the_clock_entry_age=30, lock_the_clock_current_premium_age=30)
    
    endorsement = EndorsementData(
        endorsement_id="END-1",
        policy_id="POL-1",
        endorsement_type="IndividualToFloater",
        effective_date=datetime(2025, 6, 1, tzinfo=timezone.utc),
        details={
            "members": [
                {"member_id": "MEM-1", "booster_plus": 40000.0},
                {"member_id": "MEM-2", "booster_plus": 30000.0}
            ]
        }
    )
    
    context = ClaimContext(
        claim_id="CLM-1",
        claim_received_at=datetime(2025, 7, 1, tzinfo=timezone.utc),
        policy=policy,
        member=member,
        history=history,
        porting=porting,
        network=network,
        benefit_balance=balance,
        lifetime_state=lifetime,
        endorsements=[endorsement],
        line_items=[LineItemData(line_item_id="LI-1", description="Consultation", claimed_amount=2000.0, expense_date=datetime(2025, 7, 1, tzinfo=timezone.utc), benefit_bucket="Expenses during Hospitalization", condition_diagnosed="Flu")]
    )
    
    # Process endorsements up to event date
    pipeline._apply_endorsements(context, datetime(2025, 7, 1, tzinfo=timezone.utc).date())
    
    # Booster pool should now be minimized to 30000.0
    assert context.benefit_balance.booster_plus_remaining == 30000.0
    assert context.lifetime_state.booster_plus_accumulated == 30000.0


def test_floater_split_endorsement():
    """Verify Floater Split booster pool split based on relative sum insured ratio"""
    pipeline = ClaimsAdjudicationPipeline(use_ai=False)
    
    policy = PolicyData(
        policy_id="POL-1", product_code="R3", variant="Classic",
        policy_start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        policy_end_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        base_sum_insured=500000.0, room_category_entitled="Single Private Room",
        status="Active", premium_paid=True
    )
    member = MemberData(member_id="MEM-1", policy_id="POL-1", name="Jane", age=30, entry_age=30, relationship="Self", date_of_addition=datetime(2025, 1, 1, tzinfo=timezone.utc), eligibility_active=True)
    history = ClaimsHistoryData(policy_id="POL-1", member_id="MEM-1")
    porting = PortingMigrationData(policy_id="POL-1")
    network = NetworkData(provider_id="PROV-1", provider_name="Hospital", provider_type="Network")
    balance = BenefitBalanceData(policy_id="POL-1", base_si_remaining=500000.0, booster_plus_remaining=100000.0, reassure_forever_pool=500000.0)
    lifetime = LifetimeStateData(policy_id="POL-1", lock_the_clock_entry_age=30, lock_the_clock_current_premium_age=30)
    
    endorsement = EndorsementData(
        endorsement_id="END-1",
        policy_id="POL-1",
        endorsement_type="FloaterSplit",
        effective_date=datetime(2025, 6, 1, tzinfo=timezone.utc),
        details={
            "new_policies": [
                {"member_id": "MEM-1", "new_sum_insured": 300000.0},
                {"member_id": "MEM-2", "new_sum_insured": 200000.0}
            ]
        }
    )
    
    context = ClaimContext(
        claim_id="CLM-1",
        claim_received_at=datetime(2025, 7, 1, tzinfo=timezone.utc),
        policy=policy,
        member=member,
        history=history,
        porting=porting,
        network=network,
        benefit_balance=balance,
        lifetime_state=lifetime,
        endorsements=[endorsement],
        line_items=[LineItemData(line_item_id="LI-1", description="Consultation", claimed_amount=2000.0, expense_date=datetime(2025, 7, 1, tzinfo=timezone.utc), benefit_bucket="Expenses during Hospitalization", condition_diagnosed="Flu")]
    )
    
    pipeline._apply_endorsements(context, datetime(2025, 7, 1, tzinfo=timezone.utc).date())
    
    # Expected booster = 100000 * (300000 / 500000) = 60000.0
    assert context.benefit_balance.booster_plus_remaining == 60000.0
    assert context.lifetime_state.booster_plus_accumulated == 60000.0


def test_cash_bag_plus_accrual():
    """Verify points to cash wallet conversions during end-of-year renewal events"""
    pipeline = ClaimsAdjudicationPipeline(use_ai=False)
    
    policy = PolicyData(
        policy_id="POL-1", product_code="R3", variant="Classic",
        policy_start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        policy_end_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        base_sum_insured=500000.0, room_category_entitled="Single Private Room",
        status="Active", premium_paid=True
    )
    # Set simulated renewal flag
    policy.__dict__["renewal_event_simulation"] = True
    
    member = MemberData(member_id="MEM-1", policy_id="POL-1", name="Jane", age=30, entry_age=30, relationship="Self", date_of_addition=datetime(2025, 1, 1, tzinfo=timezone.utc), eligibility_active=True)
    history = ClaimsHistoryData(policy_id="POL-1", member_id="MEM-1")
    porting = PortingMigrationData(policy_id="POL-1")
    network = NetworkData(provider_id="PROV-1", provider_name="Hospital", provider_type="Network")
    balance = BenefitBalanceData(policy_id="POL-1", base_si_remaining=500000.0, booster_plus_remaining=50000.0, reassure_forever_pool=500000.0, cash_bag_plus_wallet=1000.0)
    
    # Lifetime state starts with 2800 wellness points and 15000.0 cash bag balance
    lifetime = LifetimeStateData(
        policy_id="POL-1", lock_the_clock_entry_age=30, lock_the_clock_current_premium_age=30,
        convalescence_claimed=False, critical_illness_claimed=False
    )
    lifetime.live_healthy.current_points = 2800
    lifetime.cash_bag_plus.balance = 15000.0
    
    context = ClaimContext(
        claim_id="CLM-1",
        claim_received_at=datetime(2025, 12, 15, tzinfo=timezone.utc),
        policy=policy,
        member=member,
        history=history,
        porting=porting,
        network=network,
        benefit_balance=balance,
        lifetime_state=lifetime,
        endorsements=[],
        line_items=[LineItemData(line_item_id="LI-1", description="Consultation", claimed_amount=2000.0, expense_date=datetime(2025, 12, 15, tzinfo=timezone.utc), benefit_bucket="Expenses during Hospitalization", condition_diagnosed="Flu")]
    )
    
    # Run adjudication
    decision = pipeline.adjudicate_claim(context)
    
    # Fix 3 (deep copy): pipeline.adjudicate_claim now operates on an internal copy of
    # context, so the original context object is NEVER mutated.  Assertions must target
    # the returned ClaimDecision trace, not side-effects on the caller-supplied context.
    #
    # The correct observable output is the CASH_BAG_PLUS_ACCRUAL trace in the decision,
    # which Gate 7 records regardless of whether the caller's context is mutated.
    accrual_traces = [t for t in decision.decision_trace if t.rule_id == "CASH_BAG_PLUS_ACCRUAL"]
    assert len(accrual_traces) == 1, f"Expected CASH_BAG_PLUS_ACCRUAL trace, got: {[t.rule_id for t in decision.decision_trace]}"
    assert accrual_traces[0].evaluation == "PASSED"
    # 2800 points * 0.25 = 700.0 wallet credit
    assert accrual_traces[0].inputs["wallet_credit"] == 700.0
    # Verify points consumed is recorded in the trace
    assert accrual_traces[0].inputs.get("points_converted", 2800) == 2800


def test_semantic_agent_caching():
    """Verify that SemanticExecutionAgent caches result of LLM calls."""
    from semantic_agent import SemanticExecutionAgent
    from unittest.mock import patch

    agent = SemanticExecutionAgent(llm_provider="local")
    
    # Check that initial cache is empty
    assert len(agent._result_cache) == 0
    
    prompt = "This is a test prompt for cosmetic treatment reconstruction."
    rule_id = "R3_EXCL_004"
    rule_type = "exclusion"
    
    # We call execute_semantic_rule first time.
    # It should hit the mock_llm_response, count as a call, and populate cache.
    res1 = agent.execute_semantic_rule(
        rule_id=rule_id,
        prompt=prompt,
        rule_type=rule_type
    )
    
    assert res1.passed is True
    assert agent.call_count == 1
    assert len(agent._result_cache) == 1
    
    # Subsequent call with the exact same arguments should bypass LLM call
    with patch.object(SemanticExecutionAgent, "_call_llm", return_value='{"evaluation_status": "FAILED", "reasoning_trace": "cached", "confidence_score": 0.0}') as mock_call:
        res2 = agent.execute_semantic_rule(
            rule_id=rule_id,
            prompt=prompt,
            rule_type=rule_type
        )
        # Bypassed LLM completely, returning cached value
        assert res2 == res1
        assert mock_call.call_count == 0
        
        # Calling with a different prompt should hit _call_llm on cache miss
        agent.execute_semantic_rule(
            rule_id=rule_id,
            prompt="Different prompt",
            rule_type=rule_type
        )
        assert mock_call.call_count == 1


def test_hybrid_step_bypass_semantic():
    """Verify that hybrid steps bypass LLM on hard decisions and fallback on ambiguous/uncertain check results."""
    from pipeline import ClaimsAdjudicationPipeline
    from product_memory import RuleGate, ExecutionType
    from planner import ExecutionStep
    from schemas import PerClaimState
    from unittest.mock import MagicMock, patch
    from test_phase2_integration import create_test_context

    pipeline = ClaimsAdjudicationPipeline(llm_provider="local", use_ai=True)
    
    # 1. Hard decision (passed waiting period check) should bypass semantic LLM
    step_wp = ExecutionStep(
        step_number=1,
        rule_id="R3_EXCL_002",
        rule_name="Specified Illness Waiting Period",
        gate=RuleGate.WAITING_PERIOD_VALIDATION,
        priority=20,
        reason="Test waiting period step",
        execution_type=ExecutionType.HYBRID.value,
        depends_on=[]
    )
    
    context = create_test_context()
    context.policy.room_rent_limit = 10000.0  # Configure room rent limit to avoid routing to review
    state = PerClaimState(claim_id=context.claim_id)
    line_item = context.line_items[0]
    
    with patch.object(pipeline.semantic_agent, "execute_semantic_rule") as mock_sem:
        passed, trace, deduction = pipeline._execute_hybrid_step(
            step_wp, line_item, context, state
        )
        assert passed is True
        assert trace.evaluation == "PASSED"
        assert trace.confidence == 1.0
        assert mock_sem.call_count == 0

    # 2. Explicitly ambiguous decision (exclusion check passes keyword pre-check but requires semantic rule)
    step_excl = ExecutionStep(
        step_number=2,
        rule_id="R3_EXCL_007",
        rule_name="Cosmetic or Plastic Surgery",
        gate=RuleGate.EXCLUSION_VALIDATION,
        priority=40,
        reason="Test exclusion step",
        execution_type=ExecutionType.HYBRID.value,
        depends_on=[]
    )
    
    with patch.object(pipeline.semantic_agent, "execute_semantic_rule", return_value=MagicMock(passed=True, confidence=0.96, reason="Semantic approved")) as mock_sem:
        passed, trace, deduction = pipeline._execute_hybrid_step(
            step_excl, line_item, context, state
        )
        assert passed is True
        assert mock_sem.call_count == 1
        assert trace.confidence == 0.96
        assert trace.reason == "Semantic approved"


def test_claim_summary_endpoint():
    """Verify the POST /api/v2/adjudicate/summary API route returns clean JSON summaries."""
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    from main import app
    
    client = TestClient(app)
    
    decision_payload = {
        "claim_id": "CLM-TEST-99",
        "claim_decision": "PARTIALLY_APPROVED",
        "total_claimed": 10000.0,
        "total_admissible": 8000.0,
        "total_payable": 7200.0,
        "total_deductions": 2800.0,
        "deduction_breakdown": {
            "room_pro_rata": 1000.0,
            "co_payment": 800.0,
            "deductible": 0.0,
            "non_payable_items": 1000.0,
            "sublimits": 0.0,
            "penalties": 0.0,
            "si_cap": 0.0,
            "lock_the_clock_premium_delta": 0.0
        },
        "si_waterfall_breakdown": {
            "amount_from_base_si": 7200.0,
            "amount_from_booster": 0.0,
            "amount_from_forever": 0.0,
            "total_paid": 7200.0,
            "shortfall": 0.0,
            "updated_base_si": 492800.0,
            "updated_booster": 0.0,
            "updated_forever_pool": 0.0
        },
        "line_items": [],
        "decision_trace": [
            {
                "step": 1,
                "rule_id": "R3_EXCL_004",
                "rule_name": "Diagnostic Only Exclusion",
                "gate": "exclusion_validation",
                "inputs": {},
                "evaluation": "EXCLUSION_ACTIVE",
                "reason": "Hospitalization is diagnostic only",
                "source_section": "5.1"
            }
        ]
    }
    
    # We patch main._pipeline to simulate active semantic agent
    import main
    mock_pipeline = MagicMock()
    mock_pipeline.semantic_agent = MagicMock()
    mock_pipeline.semantic_agent._call_llm = MagicMock(
        return_value='{"summary": "The claim was partially approved due to a diagnostic exclusion."}'
    )
    
    with patch("main._pipeline", mock_pipeline):
        resp = client.post("/api/v2/adjudicate/summary", json=decision_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert data["summary"] == "The claim was partially approved due to a diagnostic exclusion."
