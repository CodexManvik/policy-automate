
"""
Phase 2 Integration Tests
Validates end-to-end functionality of graph-based execution
"""

import sys
# Ensure consistent Unicode output on Windows terminals (avoid CP1252 crashes)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


from datetime import datetime, timedelta, timezone
from schemas import *
from product_memory import get_product_memory, RuleGate
from planner import AIPlanner
from semantic_agent import SemanticExecutionAgent


def test_product_memory_loading():
    """Test 1: Product Memory Store initialization"""
    print("\n" + "="*70)
    print("TEST 1: Product Memory Loading")
    print("="*70)
    
    memory = get_product_memory()
    
    assert len(memory.rules) > 0, "No rules loaded"
    assert memory.product_id == "R3", "Wrong product ID"
    assert memory.product_name == "ReAssure 3.0", "Wrong product name"
    
    # Check gates are populated
    for gate in RuleGate:
        rules = memory.get_rules_for_gate(gate)
        print(f"  {gate.value}: {len(rules)} rules")
    
    print("[PASS] PASSED: Product Memory loaded successfully")
    return True


def test_rule_filtering():
    """Test 2: Rule filtering logic"""
    print("\n" + "="*70)
    print("TEST 2: Rule Filtering")
    print("="*70)
    
    memory = get_product_memory()
    
    # Filter by gate
    excl_rules = memory.filter_rules(gate=RuleGate.EXCLUSION_VALIDATION)
    print(f"  Exclusion rules: {len(excl_rules)}")
    assert len(excl_rules) > 0, "No exclusion rules found"
    
    # Filter by variant
    select_rules = memory.filter_rules(variant="Select")
    print(f"  Select variant rules: {len(select_rules)}")
    assert len(select_rules) > 0, "No Select rules found"
    
    # Specific rule lookup
    rule = memory.get_rule("R3_EXCL_007")
    assert rule is not None, "Rule R3_EXCL_007 not found"
    assert rule.rule_name.lower().startswith("cosmetic")
    print(f"  Found rule: {rule.rule_name}")
    
    print("[PASS] PASSED: Rule filtering works correctly")
    return True


def test_dag_construction():
    """Test 3: DAG construction and topological sort"""
    print("\n" + "="*70)
    print("TEST 3: DAG Construction")
    print("="*70)
    
    context = create_test_context()
    planner = AIPlanner()
    
    plan = planner.create_execution_plan(context, context.line_items[0])
    
    assert len(plan.execution_steps) > 0, "No execution steps generated"
    assert plan.dependency_depth >= 0, "Invalid dependency depth"
    
    print(f"  Total steps: {len(plan.execution_steps)}")
    print(f"  Dependency depth: {plan.dependency_depth}")
    
    # Verify topological ordering
    executed_rules = set()
    for step in plan.execution_steps:
        # All dependencies should have been executed before this step
        for dep in step.depends_on:
            assert dep in executed_rules, f"Dependency {dep} not executed before {step.rule_id}"
        executed_rules.add(step.rule_id)
    
    print("[PASS] PASSED: DAG construction and topological sort correct")
    return True


def test_semantic_agent_exclusions():
    """Test 4: Semantic agent exclusion assessments"""
    print("\n" + "="*70)
    print("TEST 4: Semantic Agent - Exclusions")
    print("="*70)
    
    # Use mock provider for testing
    agent = SemanticExecutionAgent(llm_provider="mock")
    
    # Test cosmetic exclusion
    prompt_cosmetic = "Treatment: Rhinoplasty for cosmetic purposes"
    result1 = agent.execute_semantic_rule(
        "R3_EXCL_007", prompt_cosmetic, "exclusion"
    )
    assert not result1.passed, "Cosmetic surgery should be excluded"
    assert result1.confidence > 0.8, "Low confidence on clear exclusion"
    print(f"  Cosmetic: {'EXCLUDED' if not result1.passed else 'APPROVED'} (confidence: {result1.confidence:.2f})")
    
    # Test maternity exclusion
    prompt_maternity = "Diagnosis: Normal delivery"
    result2 = agent.execute_semantic_rule(
        "R3_EXCL_016", prompt_maternity, "exclusion"
    )
    print(f"  DEBUG: prompt='{prompt_maternity}', passed={result2.passed}, confidence={result2.confidence}, reason={result2.reason}")
    assert not result2.passed, "Maternity should be excluded"
    print(f"  Maternity: {'EXCLUDED' if not result2.passed else 'APPROVED'} (confidence: {result2.confidence:.2f})")
    
    # Test investigation-only exclusion
    prompt_investigation = "Admission Reason: MRI scan only, no treatment"
    result3 = agent.execute_semantic_rule(
        "R3_EXCL_004", prompt_investigation, "exclusion"
    )
    assert not result3.passed, "Investigation-only should be excluded"
    print(f"  Investigation: {'EXCLUDED' if not result3.passed else 'APPROVED'} (confidence: {result3.confidence:.2f})")
    
    print("[PASS] PASSED: Semantic agent correctly assesses exclusions")
    return True


def test_semantic_agent_coverage():
    """Test 5: Semantic agent coverage assessments"""
    print("\n" + "="*70)
    print("TEST 5: Semantic Agent - Coverage")
    print("="*70)
    
    # Use mock provider for testing
    agent = SemanticExecutionAgent(llm_provider="mock")
    
    # Test valid coverage
    prompt = "Treatment: Appendectomy for acute appendicitis, medically necessary"
    result = agent.execute_semantic_rule(
        "R3_BEN_003", prompt, "coverage"
    )
    
    assert result.passed or result.confidence < 0.90, "Valid treatment should be covered or require review"
    print(f"  Coverage: {'APPROVED' if result.passed else 'REVIEW'} (confidence: {result.confidence:.2f})")
    print(f"  Manual review: {result.requires_manual_review}")
    
    print("[PASS] PASSED: Semantic agent coverage assessment works")
    return True


def test_confidence_based_routing():
    """Test 6: Confidence-based routing to manual review"""
    print("\n" + "="*70)
    print("TEST 6: Confidence-Based Routing")
    print("="*70)
    
    # Use mock provider for testing
    agent = SemanticExecutionAgent(llm_provider="mock", confidence_threshold=0.90)
    
    # High confidence case
    result_high = agent.execute_semantic_rule(
        "R3_EXCL_007",
        "Treatment: Clear cosmetic procedure",
        "exclusion"
    )
    
    # Low confidence should route to review
    if result_high.confidence < 0.90:
        assert result_high.requires_manual_review, "Low confidence should require review"
        print(f"  Low confidence ({result_high.confidence:.2f}) → Manual Review [PASS]")
    else:
        print(f"  High confidence ({result_high.confidence:.2f}) → Auto-Decision [PASS]")
    
    print("[PASS] PASSED: Confidence-based routing works")
    return True


def test_execution_plan_generation():
    """Test 7: Complete execution plan generation"""
    print("\n" + "="*70)
    print("TEST 7: Execution Plan Generation")
    print("="*70)
    
    context = create_test_context()
    planner = AIPlanner()
    
    plan = planner.create_execution_plan(context, context.line_items[0])
    
    # Validate plan structure
    assert plan.claim_id == context.claim_id
    assert plan.variant == context.policy.variant
    assert len(plan.execution_steps) > 0
    
    # Check step types
    has_deterministic = False
    has_semantic = False
    
    for step in plan.execution_steps:
        if step.execution_type == "deterministic":
            has_deterministic = True
        elif step.execution_type == "semantic":
            has_semantic = True
    
    print(f"  Plan ID: {plan.plan_id}")
    print(f"  Total steps: {len(plan.execution_steps)}")
    print(f"  Has deterministic: {has_deterministic}")
    print(f"  Has semantic: {has_semantic}")
    print(f"  Dependency depth: {plan.dependency_depth}")
    
    assert has_deterministic, "Should have deterministic rules"
    
    print("[PASS] PASSED: Execution plan generated correctly")
    return True


def create_test_context() -> ClaimContext:
    """Create a test claim context"""
    return ClaimContext(
        claim_id="TEST-CLM-001",
        claim_received_at=datetime.now(timezone.utc),
        policy=PolicyData(
            policy_id="TEST-POL-001",
            product_code="R3",
            variant="Select",
            policy_start_date=datetime(2023, 1, 1),
            policy_end_date=datetime(2026, 1, 1),
            base_sum_insured=1000000.0,
            status="Active",
            premium_paid=True,
            co_payment_percent=0.10
        ),
        member=MemberData(
            member_id="TEST-MEM-001",
            policy_id="TEST-POL-001",
            name="Test Member",
            age=35,
            entry_age=33,
            relationship="Self",
            date_of_addition=datetime(2023, 1, 1),
            eligibility_active=True
        ),
        history=ClaimsHistoryData(
            policy_id="TEST-POL-001",
            member_id="TEST-MEM-001",
            claim_free_years=2
        ),
        porting=PortingMigrationData(
            policy_id="TEST-POL-001",
            porting_applicable=False
        ),
        network=NetworkData(
            provider_id="TEST-PROV-001",
            provider_name="Test Hospital",
            provider_type="Network"
        ),
        benefit_balance=BenefitBalanceData(
            policy_id="TEST-POL-001",
            base_si_remaining=1000000.0,
            booster_plus_remaining=500000.0
        ),
        lifetime_state=LifetimeStateData(
            policy_id="TEST-POL-001",
            lock_the_clock_age_locked=True,
            lock_the_clock_entry_age=33,
            lock_the_clock_current_premium_age=33
        ),
        line_items=[
            LineItemData(
                line_item_id="TEST-LI-001",
                description="Hospitalization for Appendectomy",
                claimed_amount=150000.0,
                expense_date=datetime.now(timezone.utc),
                benefit_bucket="Expenses during Hospitalization",
                admission_date=datetime.now(timezone.utc) - timedelta(days=2),
                discharge_date=datetime.now(timezone.utc) - timedelta(days=1),
                hospitalization_hours=30.0,
                condition_diagnosed="Acute Appendicitis",
                accident_related=False
            )
        ]
    )


def test_async_adjudication():
    """Test 8: Asynchronous claim adjudication"""
    print("\n" + "="*70)
    print("TEST 8: Asynchronous Adjudication")
    print("="*70)
    
    import asyncio
    from pipeline import ClaimsAdjudicationPipeline
    
    context = create_test_context()
    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")
    
    decision = asyncio.run(pipeline.adjudicate_claim_async(context))
    
    assert decision.claim_decision in ["APPROVED", "PARTIALLY_APPROVED", "REJECTED", "PENDING_REVIEW"]
    assert decision.total_claimed == 150000.0
    assert len(decision.line_items) == 1
    assert decision.confidence_score > 0.0
    
    print(f"  Overall Decision: {decision.claim_decision}")
    print(f"  Total Payable: INR {decision.total_payable:,.2f}")
    print("[PASS] PASSED: Asynchronous adjudication matches schema and succeeds")
    return True


def run_all_tests():
    """Run all Phase 2 integration tests"""
    print("\n" + "="*70)
    print("PHASE 2 INTEGRATION TESTS")
    print("="*70)
    
    tests = [
        test_product_memory_loading,
        test_rule_filtering,
        test_dag_construction,
        test_semantic_agent_exclusions,
        test_semantic_agent_coverage,
        test_confidence_based_routing,
        test_execution_plan_generation,
        test_async_adjudication
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] ERROR: {e}")
            failed += 1
    
    print("\n" + "="*70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*70)
    
    if failed == 0:
        print("\n ALL TESTS PASSED - Phase 2 Ready for Production!")
    else:
        print(f"\n[WARN] {failed} test(s) failed - review and fix")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
