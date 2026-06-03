"""
Example Usage of Claims Adjudication Pipeline
Demonstrates Phase 1 implementation with deterministic calculators
"""

from datetime import datetime, timedelta, timezone
from schemas import *
from pipeline import ClaimsAdjudicationPipeline
import json


def create_sample_claim_context() -> ClaimContext:
    """Create a sample claim context for testing"""
    
    # Policy data
    policy = PolicyData(
        policy_id="POL-2025-001",
        product_code="R3",
        variant="Select",
        policy_start_date=datetime(2023, 1, 1),
        policy_end_date=datetime(2026, 1, 1),
        base_sum_insured=1000000.0,
        status="Active",
        premium_paid=True,
        co_payment_percent=0.10,  # 10% base co-pay
        room_category_entitled="Single Private Room"
    )
    
    # Member data
    member = MemberData(
        member_id="MEM-001",
        policy_id="POL-2025-001",
        name="John Doe",
        age=35,
        entry_age=33,
        relationship="Self",
        date_of_addition=datetime(2023, 1, 1),
        eligibility_active=True
    )
    
    # Claims history
    history = ClaimsHistoryData(
        policy_id="POL-2025-001",
        member_id="MEM-001",
        prior_claims_count=1,
        total_utilized_si=200000.0,
        claim_free_years=1
    )
    
    # Porting data
    porting = PortingMigrationData(
        policy_id="POL-2025-001",
        porting_applicable=False,
        prior_coverage_months=0
    )
    
    # Network data
    network = NetworkData(
        provider_id="PROV-001",
        provider_name="Apollo Hospital",
        provider_type="Network",
        tiered_network_member=True,
        heads_up_recommended=True
    )
    
    # Benefit balances
    benefit_balance = BenefitBalanceData(
        policy_id="POL-2025-001",
        base_si_remaining=800000.0,
        booster_plus_remaining=500000.0,
        reassure_forever_pool=1000000.0,
        deductible_consumed_ytd=0.0
    )
    
    # Lifetime state
    lifetime_state = LifetimeStateData(
        policy_id="POL-2025-001",
        reassure_forever_triggered=True,
        reassure_forever_triggered_date=datetime(2024, 6, 15),
        lock_the_clock_age_locked=False,
        lock_the_clock_entry_age=33,
        lock_the_clock_current_premium_age=35,
        booster_plus_accumulated=500000.0
    )
    
    # Line items
    line_items = [
        LineItemData(
            line_item_id="LI-001",
            description="Hospitalization for Appendectomy",
            claimed_amount=150000.0,
            expense_date=datetime.now(timezone.utc),
            benefit_bucket="Expenses during Hospitalization",
            admission_date=datetime.now(timezone.utc) - timedelta(days=3),
            discharge_date=datetime.now(timezone.utc) - timedelta(days=1),
            hospitalization_hours=48.0,
            actual_room_rent=5000.0,
            room_category_claimed="Single Private Room",
            condition_diagnosed="Acute Appendicitis",
            accident_related=False,
            emergency=True
        )
    ]
    
    # Create claim context
    return ClaimContext(
        claim_id="CLM-2025-001234",
        claim_received_at=datetime.now(timezone.utc),
        policy=policy,
        member=member,
        history=history,
        porting=porting,
        network=network,
        benefit_balance=benefit_balance,
        lifetime_state=lifetime_state,
        line_items=line_items
    )



def create_ambiguous_claim_context() -> ClaimContext:
    """Create a claim context with highly ambiguous clinical jargon to test confidence failure safety guardrail"""
    context = create_sample_claim_context()
    context.claim_id = "CLM-2025-AMBIGUOUS"
    
    # Modify the line item description and condition to trigger the mock LLM low confidence check
    context.line_items[0].description = "Patient admitted for lifestyle assessment, routine body optimization, and cosmetic alignment verification"
    context.line_items[0].condition_diagnosed = "Routine lifestyle optimization"
    
    return context


def print_decision_summary(decision: ClaimDecision):
    """Print a formatted summary of the adjudication decision"""
    
    print("\n" + "="*80)
    print(f"CLAIM ADJUDICATION DECISION: {decision.claim_id}")
    print("="*80)
    
    print(f"\nOverall Decision: {decision.claim_decision}")
    print(f"Confidence Score: {decision.confidence_score:.2%}")
    print(f"Processing Time: {decision.processing_duration_ms:.2f}ms")
    
    print(f"\n{'FINANCIAL SUMMARY':^80}")
    print("-"*80)
    print(f"Total Claimed:     INR {decision.total_claimed:>12,.2f}")
    print(f"Total Admissible:  INR {decision.total_admissible:>12,.2f}")
    print(f"Total Payable:     INR {decision.total_payable:>12,.2f}")
    print(f"Total Deductions:  INR {decision.total_deductions:>12,.2f}")
    
    print(f"\n{'DEDUCTION BREAKDOWN':^80}")
    print("-"*80)
    breakdown = decision.deduction_breakdown
    print(f"Room Pro-Rata:     INR {breakdown.room_pro_rata:>12,.2f}")
    print(f"Co-Payment:        INR {breakdown.co_payment:>12,.2f}")
    print(f"Deductible:        INR {breakdown.deductible:>12,.2f}")
    print(f"Penalties:         INR {breakdown.penalties:>12,.2f}")
    print(f"SI Cap:            INR {breakdown.si_cap:>12,.2f}")
    
    print(f"\n{'SI WATERFALL BREAKDOWN':^80}")
    print("-"*80)
    si_breakdown = decision.si_waterfall_breakdown
    print(f"From Base SI:      INR {si_breakdown.amount_from_base_si:>12,.2f}")
    print(f"From Booster+:     INR {si_breakdown.amount_from_booster:>12,.2f}")
    print(f"From Forever:      INR {si_breakdown.amount_from_forever:>12,.2f}")
    print(f"Total Paid:        INR {si_breakdown.total_paid:>12,.2f}")
    print(f"Shortfall:         INR {si_breakdown.shortfall:>12,.2f}")
    
    print(f"\n{'LINE ITEM DECISIONS':^80}")
    print("-"*80)
    for item in decision.line_items:
        print(f"\nLine Item: {item.line_item_id} - {item.description}")
        print(f"  Status: {item.decision}")
        print(f"  Claimed: INR {item.claimed_amount:,.2f} -> Payable: INR {item.payable_amount:,.2f}")
        
        if item.deductions:
            print(f"  Deductions Applied:")
            for deduction in item.deductions:
                print(f"    • {deduction.deduction_type}: INR {deduction.amount:,.2f}")
                print(f"      Reason: {deduction.reason}")
    
    print(f"\n{'DECISION TRACE (Last 5 steps)':^80}")
    print("-"*80)
    for trace in decision.decision_trace[-5:]:
        print(f"\nStep {trace.step}: {trace.rule_name} ({trace.gate})")
        print(f"  Rule ID: {trace.rule_id}")
        print(f"  Evaluation: {trace.evaluation}")
        print(f"  Reason: {trace.reason}")
        if trace.source_section:
            print(f"  Source: Section {trace.source_section}")
    
    print("\n" + "="*80)
    print()



def main():
    """Main execution function"""
    
    print("\n" + "="*80)
    print("ReAssure 3.0 Claims Auto-Adjudication Engine - Phase 2 Demo")
    print("Deterministic Calculation Tools + Graph-based Execution + Safety Routing Guardrail")
    print("="*80)
    
    # Initialize pipeline
    print("\n[1/4] Initializing adjudication pipeline...")
    pipeline = ClaimsAdjudicationPipeline()
    print("  [PASS] Pipeline initialized")
    
    # -------------------------------------------------------------
    # CASE 1: Standard Inpatient Hospitalization (Should pass with partial approval due to copay/deductibles)
    # -------------------------------------------------------------
    print("\n" + "-"*50)
    print("CASE 1: Standard Inpatient Hospitalization")
    print("-"*50)
    
    print("\nCreating sample claim context...")
    context = create_sample_claim_context()
    
    print(f"  [PASS] Claim ID: {context.claim_id}")
    print(f"  [PASS] Policy: {context.policy.policy_id} ({context.policy.variant})")
    print(f"  [PASS] Member: {context.member.name} (Age {context.member.age})")
    print(f"  [PASS] Base SI: INR {context.policy.base_sum_insured:,.0f}")
    print(f"  [PASS] Line Items: {len(context.line_items)}")
    
    print("\nExecuting graph-based adjudication...")
    decision = pipeline.adjudicate_claim(context)
    print(f"  [PASS] Adjudication complete in {decision.processing_duration_ms:.2f}ms")
    
    # Print detailed decision
    print_decision_summary(decision)
    
    # Export to JSON
    print("[Optional] Exporting decision to JSON...")
    decision_dict = decision.model_dump(mode='json')
    output_file = "claim_decision_output.json"
    with open(output_file, 'w') as f:
        json.dump(decision_dict, f, indent=2, default=str)
    print(f"  [PASS] Decision exported to: {output_file}")
    
    # -------------------------------------------------------------
    # CASE 2: Ambiguous Clinical Jargon (Should trigger low-confidence safety routing to PENDING_REVIEW)
    # -------------------------------------------------------------
    print("\n" + "-"*50)
    print("CASE 2: Ambiguous Clinical Jargon (Safety Guardrail Demo)")
    print("-"*50)
    
    print("\nCreating ambiguous claim context...")
    ambiguous_context = create_ambiguous_claim_context()
    
    print(f"  [PASS] Claim ID: {ambiguous_context.claim_id}")
    print(f"  [PASS] Jargon Description: {ambiguous_context.line_items[0].description}")
    
    # Initialize a new pipeline for CASE 2 with a higher confidence threshold (e.g. 0.98) to demonstrate the Safety Guardrail
    print("\nInitializing case 2 pipeline with 98% confidence threshold...")
    pipeline_case2 = ClaimsAdjudicationPipeline(confidence_threshold=0.98)
    
    print("\nExecuting graph-based adjudication...")
    ambiguous_decision = pipeline_case2.adjudicate_claim(ambiguous_context)
    print(f"  [PASS] Adjudication complete in {ambiguous_decision.processing_duration_ms:.2f}ms")
    
    # Print detailed decision
    print_decision_summary(ambiguous_decision)
    
    # Export Case 2 to JSON
    output_file_ambig = "claim_decision_ambiguous_output.json"
    with open(output_file_ambig, 'w') as f:
        json.dump(ambiguous_decision.model_dump(mode='json'), f, indent=2, default=str)
    print(f"  [PASS] Ambiguous decision exported to: {output_file_ambig}")
    
    print("\n" + "="*80)
    print("Phase 2 Demo Complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
