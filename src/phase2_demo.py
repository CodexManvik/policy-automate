"""
Phase 2 Demo - Graph-Based Agentic Execution
Demonstrates AI Planner, Semantic Agent, and Dynamic Execution
"""

from datetime import datetime, timedelta, timezone
from schemas import *
from product_memory import get_product_memory, RuleGate
from planner import AIPlanner
from semantic_agent import SemanticExecutionAgent
import json


def demo_product_memory():
    """Demo 1: Product Memory Store"""
    print("\n" + "="*80)
    print("DEMO 1: Product Memory Store - Rule Repository")
    print("="*80)
    
    memory = get_product_memory()
    
    print(f"\n📚 Loaded Rules: {len(memory.rules)}")
    print(f"   Product: {memory.product_name} ({memory.product_id})")
    print(f"   Version: {memory.version}")
    
    # Show rules by gate
    print("\n📊 Rules by Gate:")
    for gate in RuleGate:
        rules = memory.get_rules_for_gate(gate)
        print(f"   {gate.value:30} : {len(rules)} rules")
    
    # Show sample rule
    print("\n🔍 Sample Rule: R3_EXCL_007 (Cosmetic Surgery)")
    rule = memory.get_rule("R3_EXCL_007")
    if rule:
        print(f"   Name: {rule.rule_name}")
        print(f"   Gate: {rule.gate.value}")
        print(f"   Type: {rule.execution_type.value}")
        print(f"   Priority: {rule.priority}")
        print(f"   Section: {rule.section_ref}")
        print(f"   Formula: {rule.formula}")
        if rule.semantic_prompt_template:
            print(f"   Has semantic prompt: Yes ({len(rule.semantic_prompt_template)} chars)")


def demo_ai_planner():
    """Demo 2: AI Planner with DAG Construction"""
    print("\n" + "="*80)
    print("DEMO 2: AI Planner - Dynamic Execution Plan Generation")
    print("="*80)
    
    # Create sample claim context
    context = ClaimContext(
        claim_id="CLM-DEMO-001",
        claim_received_at=datetime.now(timezone.utc),
        policy=PolicyData(
            policy_id="POL-DEMO-001",
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
            member_id="MEM-001",
            policy_id="POL-DEMO-001",
            name="Test Member",
            age=35,
            entry_age=33,
            relationship="Self",
            date_of_addition=datetime(2023, 1, 1),
            eligibility_active=True
        ),
        history=ClaimsHistoryData(
            policy_id="POL-DEMO-001",
            member_id="MEM-001",
            claim_free_years=2
        ),
        porting=PortingMigrationData(
            policy_id="POL-DEMO-001",
            porting_applicable=False
        ),
        network=NetworkData(
            provider_id="PROV-001",
            provider_name="Test Hospital",
            provider_type="Network"
        ),
        benefit_balance=BenefitBalanceData(
            policy_id="POL-DEMO-001",
            base_si_remaining=1000000.0,
            booster_plus_remaining=500000.0
        ),
        lifetime_state=LifetimeStateData(
            policy_id="POL-DEMO-001",
            lock_the_clock_age_locked=True,
            lock_the_clock_entry_age=33,
            lock_the_clock_current_premium_age=33
        ),
        line_items=[
            LineItemData(
                line_item_id="LI-001",
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
    
    # Create execution plan
    planner = AIPlanner()
    plan = planner.create_execution_plan(context, context.line_items[0])
    
    print(f"\n🎯 Execution Plan Generated:")
    print(f"   Plan ID: {plan.plan_id}")
    print(f"   Claim ID: {plan.claim_id}")
    print(f"   Variant: {plan.variant}")
    print(f"   Benefit Bucket: {plan.benefit_bucket}")
    
    print(f"\n📊 Plan Statistics:")
    print(f"   Total rules evaluated: {plan.total_rules_evaluated}")
    print(f"   Rules filtered out: {plan.rules_filtered_out}")
    print(f"   Applicable rules: {len(plan.execution_steps)}")
    print(f"   Dependency depth: {plan.dependency_depth}")
    
    print(f"\n📝 Execution Steps (Topologically Sorted):")
    for step in plan.execution_steps[:10]:  # Show first 10
        print(f"   {step.step_number}. [{step.gate.value:25}] {step.rule_name}")
        print(f"      Type: {step.execution_type} | Priority: {step.priority}")
        if step.tool_required:
            print(f"      Tool: {step.tool_required}")
        if step.depends_on:
            print(f"      Depends on: {', '.join(step.depends_on)}")
    
    if len(plan.execution_steps) > 10:
        print(f"   ... and {len(plan.execution_steps) - 10} more steps")


def demo_semantic_agent():
    """Demo 3: Semantic Execution Agent"""
    print("\n" + "="*80)
    print("DEMO 3: Semantic Execution Agent - AI-Powered Reasoning")
    print("="*80)
    
    agent = SemanticExecutionAgent()
    
    # Test Case 1: Cosmetic Surgery (should exclude)
    print("\n🧪 Test Case 1: Cosmetic Surgery Exclusion")
    prompt1 = """Analyze if this treatment is cosmetic/plastic surgery:
Treatment: Rhinoplasty for aesthetic enhancement
Diagnosis: Cosmetic procedure
Doctor Notes: Patient requests nose reshaping for appearance

EXCLUDE if cosmetic.
ALLOW if: reconstruction after Accident/Burns/Cancer OR medically necessary.

Return: {"is_cosmetic": bool, "reason": str, "confidence": float}"""
    
    result1 = agent.execute_semantic_rule(
        rule_id="R3_EXCL_007",
        prompt=prompt1,
        rule_type="exclusion"
    )
    
    print(f"   Decision: {'REJECTED' if not result1.passed else 'APPROVED'}")
    print(f"   Confidence: {result1.confidence:.2%}")
    print(f"   Reason: {result1.reason}")
    print(f"   Manual Review: {result1.requires_manual_review}")
    
    # Test Case 2: Reconstructive Surgery (should allow)
    print("\n🧪 Test Case 2: Reconstructive Surgery After Accident")
    prompt2 = """Analyze if this treatment is cosmetic/plastic surgery:
Treatment: Reconstructive surgery for facial burns
Diagnosis: Third-degree burns from accident
Doctor Notes: Medically necessary reconstruction

EXCLUDE if cosmetic.
ALLOW if: reconstruction after Accident/Burns/Cancer OR medically necessary.

Return: {"is_cosmetic": bool, "reason": str, "confidence": float}"""
    
    result2 = agent.execute_semantic_rule(
        rule_id="R3_EXCL_007",
        prompt=prompt2,
        rule_type="exclusion"
    )
    
    print(f"   Decision: {'REJECTED' if not result2.passed else 'APPROVED'}")
    print(f"   Confidence: {result2.confidence:.2%}")
    print(f"   Reason: {result2.reason}")
    print(f"   Manual Review: {result2.requires_manual_review}")
    
    # Test Case 3: Maternity (should exclude)
    print("\n🧪 Test Case 3: Maternity Exclusion")
    prompt3 = """Check if claim involves maternity:
Diagnosis: Normal delivery
Procedures: Vaginal delivery
ICD Codes: O80 (Normal delivery)

EXCLUDE: Childbirth (normal/complicated/caesarean), Miscarriage.
ALLOW: Ectopic pregnancy.

Return: {"is_maternity": bool, "is_ectopic": bool, "reason": str, "confidence": float}"""
    
    result3 = agent.execute_semantic_rule(
        rule_id="R3_EXCL_016",
        prompt=prompt3,
        rule_type="exclusion"
    )
    
    print(f"   Decision: {'REJECTED' if not result3.passed else 'APPROVED'}")
    print(f"   Confidence: {result3.confidence:.2%}")
    print(f"   Reason: {result3.reason}")
    print(f"   Manual Review: {result3.requires_manual_review}")
    
    # Test Case 4: Investigation Only (should exclude)
    print("\n🧪 Test Case 4: Investigation-Only Admission")
    prompt4 = """Assess if admission was primarily for diagnostics only:
Admission Reason: MRI and CT scan
Procedures Performed: MRI brain, CT abdomen
Treatment Given: None
Discharge Summary: All tests normal, patient discharged

EXCLUDE if: admission solely for tests with no treatment.
ALLOW if: tests were part of active treatment protocol.

Return: {"investigation_only": bool, "reason": str, "confidence": float}"""
    
    result4 = agent.execute_semantic_rule(
        rule_id="R3_EXCL_004",
        prompt=prompt4,
        rule_type="exclusion"
    )
    
    print(f"   Decision: {'REJECTED' if not result4.passed else 'APPROVED'}")
    print(f"   Confidence: {result4.confidence:.2%}")
    print(f"   Reason: {result4.reason}")
    print(f"   Manual Review: {result4.requires_manual_review}")
    
    # Summary
    print(f"\n📊 Semantic Agent Statistics:")
    print(f"   Total calls: {agent.call_count}")
    print(f"   Average confidence: {agent.get_average_confidence():.2%}")


def demo_dag_visualization():
    """Demo 4: DAG Visualization"""
    print("\n" + "="*80)
    print("DEMO 4: Rule Dependency DAG")
    print("="*80)
    
    memory = get_product_memory()
    
    print("\n🔗 Rule Dependencies (Gate 6 - Financial Computation):")
    
    fin_rules = memory.get_rules_for_gate(RuleGate.FINANCIAL_COMPUTATION)
    fin_rules_sorted = sorted(fin_rules, key=lambda r: r.priority)
    
    for rule in fin_rules_sorted:
        print(f"\n   {rule.rule_id}: {rule.rule_name}")
        print(f"   Priority: {rule.priority}")
        if rule.depends_on:
            print(f"   Depends on: {', '.join(rule.depends_on)}")
        else:
            print(f"   Depends on: (none - can execute first)")
    
    print("\n💡 Execution Order (determined by topological sort):")
    print("   1. R3_BEN_004 (Room Pro-Rata) - no dependencies")
    print("   2. R3_GEN_002 (Prolonged Hosp Penalty) - no dependencies")
    print("   3. R3_BEN_016 (HeadsUp) - no dependencies")
    print("   4. R3_BEN_017 (Tiered Network) - no dependencies")
    print("   5. R3_FIN_001 (Deductible) - after Room Pro-Rata, Prolonged Penalty")
    print("   6. R3_FIN_002 (Co-Payment) - after all penalties")
    print("   7. R3_SUM_001 (SI Waterfall) - after Deductible and Co-Payment")


def main():
    """Run all Phase 2 demos"""
    print("\n" + "="*80)
    print("ReAssure 3.0 Claims Auto-Adjudication Engine - Phase 2 Demo")
    print("Graph-Based Agentic Execution with AI Planner & Semantic Agent")
    print("="*80)
    
    demo_product_memory()
    demo_ai_planner()
    demo_semantic_agent()
    demo_dag_visualization()
    
    print("\n" + "="*80)
    print("Phase 2 Demo Complete!")
    print("="*80)
    print("\n💡 Key Takeaways:")
    print("   ✅ Product Memory Store: Structured rule repository with metadata")
    print("   ✅ AI Planner: Dynamic DAG construction and topological sorting")
    print("   ✅ Semantic Agent: AI-powered reasoning with structured outputs")
    print("   ✅ Confidence Tracking: Auto-routing to manual review when uncertain")
    print("\n🚀 Next: Run full pipeline with `python example_usage.py`")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
