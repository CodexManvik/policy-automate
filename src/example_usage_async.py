"""
Asynchronous Example Usage of Claims Adjudication Pipeline
Demonstrates and compares synchronous vs asynchronous adjudication latency.
"""

import asyncio
import time
import json
from datetime import datetime, timedelta, timezone
from schemas import *
from pipeline import ClaimsAdjudicationPipeline


def create_sample_claim_context() -> ClaimContext:
    """Create a sample claim context with multiple line items to demonstrate batch execution benefits"""
    policy = PolicyData(
        policy_id="POL-2025-001",
        product_code="R3",
        variant="Select",
        policy_start_date=datetime(2023, 1, 1),
        policy_end_date=datetime(2026, 1, 1),
        base_sum_insured=1000000.0,
        status="Active",
        premium_paid=True,
        co_payment_percent=0.10,
        room_category_entitled="Single Private Room"
    )
    
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
    
    history = ClaimsHistoryData(
        policy_id="POL-2025-001",
        member_id="MEM-001",
        prior_claims_count=1,
        total_utilized_si=200000.0,
        claim_free_years=1
    )
    
    porting = PortingMigrationData(
        policy_id="POL-2025-001",
        porting_applicable=False,
        prior_coverage_months=0
    )
    
    network = NetworkData(
        provider_id="PROV-001",
        provider_name="Apollo Hospital",
        provider_type="Network",
        tiered_network_member=True,
        heads_up_recommended=True
    )
    
    benefit_balance = BenefitBalanceData(
        policy_id="POL-2025-001",
        base_si_remaining=800000.0,
        booster_plus_remaining=500000.0,
        reassure_forever_pool=1000000.0,
        deductible_consumed_ytd=0.0
    )
    
    lifetime_state = LifetimeStateData(
        policy_id="POL-2025-001",
        reassure_forever_triggered=True,
        reassure_forever_triggered_date=datetime(2024, 6, 15),
        lock_the_clock_age_locked=False,
        lock_the_clock_entry_age=33,
        lock_the_clock_current_premium_age=35,
        booster_plus_accumulated=500000.0
    )
    
    # Using multiple line items to show batch execution benefits
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
        ),
        LineItemData(
            line_item_id="LI-002",
            description="Medicines and pharmacy expenses",
            claimed_amount=25000.0,
            expense_date=datetime.now(timezone.utc) - timedelta(days=1),
            benefit_bucket="Expenses during Hospitalization",
            admission_date=datetime.now(timezone.utc) - timedelta(days=3),
            discharge_date=datetime.now(timezone.utc) - timedelta(days=1),
            hospitalization_hours=48.0,
            condition_diagnosed="Acute Appendicitis",
            accident_related=False
        )
    ]
    
    return ClaimContext(
        claim_id="CLM-2025-ASYNC-DEMO",
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


def print_decision_summary(decision: ClaimDecision, label: str):
    """Print formatted summary of the adjudication decision"""
    print("\n" + "="*80)
    print(f"[{label}] CLAIM ADJUDICATION DECISION: {decision.claim_id}")
    print("="*80)
    
    print(f"Overall Decision: {decision.claim_decision}")
    print(f"Confidence Score: {decision.confidence_score:.2%}")
    print(f"Processing Time:  {decision.processing_duration_ms:.2f}ms")
    print(f"Total Claimed:    INR {decision.total_claimed:,.2f}")
    print(f"Total Payable:    INR {decision.total_payable:,.2f}")
    print(f"Total Deductions: INR {decision.total_deductions:,.2f}")
    
    print("\nLine Item Results:")
    for item in decision.line_items:
        print(f"  - {item.line_item_id}: {item.description}")
        print(f"    Status: {item.decision} | Claimed: INR {item.claimed_amount:,.2f} -> Payable: INR {item.payable_amount:,.2f}")
    print("="*80 + "\n")


async def main_async():
    print("="*80)
    print("ReAssure 3.0 Claims Auto-Adjudication Engine - Latency Optimization Demo")
    print("Sequential (Sync) Execution vs. Dependency-Layered Batch (Async) Execution")
    print("="*80)
    
    context = create_sample_claim_context()
    
    # We will use the 'mock' LLM provider to run inside the test/demo environment reliably.
    # The async optimization is active for local/openai LLM providers as well.
    pipeline = ClaimsAdjudicationPipeline(llm_provider="mock")
    
    # 1. Execute Synchronously
    print("[1/3] Running Claim Adjudication Synchronously...")
    start_sync = time.time()
    sync_decision = pipeline.adjudicate_claim(context)
    duration_sync_measured = (time.time() - start_sync) * 1000
    print_decision_summary(sync_decision, "SYNCHRONOUS")
    
    # 2. Execute Asynchronously
    print("[2/3] Running Claim Adjudication Asynchronously (Concurrent Batch)...")
    start_async = time.time()
    async_decision = await pipeline.adjudicate_claim_async(context)
    duration_async_measured = (time.time() - start_async) * 1000
    print_decision_summary(async_decision, "ASYNCHRONOUS")
    
    # 3. Compare Timing
    print("[3/3] Performance Comparison Results:")
    print(f"  Synchronous Execution Time:  {duration_sync_measured:.2f}ms (Engine recorded: {sync_decision.processing_duration_ms:.2f}ms)")
    print(f"  Asynchronous Execution Time: {duration_async_measured:.2f}ms (Engine recorded: {async_decision.processing_duration_ms:.2f}ms)")
    
    speedup = (duration_sync_measured / duration_async_measured) if duration_async_measured > 0 else 1.0
    print(f"  Measured Speedup:            {speedup:.2f}x faster")
    
    # Export decisions
    with open("claim_decision_sync_output.json", "w") as f:
        json.dump(sync_decision.model_dump(mode="json"), f, indent=2, default=str)
    with open("claim_decision_async_output.json", "w") as f:
        json.dump(async_decision.model_dump(mode="json"), f, indent=2, default=str)
    print("  Decisions exported to JSON files.")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main_async())
