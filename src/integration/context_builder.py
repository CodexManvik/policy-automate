"""
Core context orchestrator builder to assemble the unified ClaimContext.
"""

import asyncio
from datetime import datetime, timezone
import httpx
from schemas import (
    ClaimContext, PolicyData, MemberData, ClaimsHistoryData,
    PortingMigrationData, NetworkData, BenefitBalanceData,
    LifetimeStateData, EndorsementData, LineItemData,
    LiveHealthyData, CashBagPlusData
)
from integration.gateway_schemas import (
    PolicyApiResponse, MemberApiResponse, ClaimsHistoryApiResponse,
    PortingApiResponse, NetworkApiResponse, BenefitBalanceApiResponse,
    LifetimeStateApiResponse, EndorsementApiResponse
)


async def assemble_claim_context(
    claim_id: str, 
    policy_id: str, 
    member_id: str, 
    provider_id: str, 
    raw_line_items: list,
    base_url: str = "http://localhost:8000"
) -> ClaimContext:
    """
    Assemble the unified ClaimContext by concurrently querying the 8 external gateways.
    """
    async with httpx.AsyncClient() as client:
        # Perform lookups concurrently to parallelize I/O latency
        tasks = [
            client.get(f"{base_url}/api/external/policy/{policy_id}"),
            client.get(f"{base_url}/api/external/member/{member_id}"),
            client.get(f"{base_url}/api/external/claims-history/{policy_id}/{member_id}"),
            client.get(f"{base_url}/api/external/porting/{policy_id}"),
            client.get(f"{base_url}/api/external/network-provider/{provider_id}"),
            client.get(f"{base_url}/api/external/balances/{policy_id}/{member_id}"),
            client.get(f"{base_url}/api/external/lifetime-state/{policy_id}"),
            client.get(f"{base_url}/api/external/endorsements/{policy_id}")
        ]
        responses = await asyncio.gather(*tasks)

    # Check for failure response codes
    for idx, r in enumerate(responses):
        if r.status_code != 200:
            raise ValueError(
                f"External gateway lookup failed for task index {idx} with status "
                f"{r.status_code}: {r.text}"
            )

    # Structural parsing & validation using integration Pydantic schemas
    policy_api = PolicyApiResponse.model_validate(responses[0].json())
    member_api = MemberApiResponse.model_validate(responses[1].json())
    history_api = ClaimsHistoryApiResponse.model_validate(responses[2].json())
    porting_api = PortingApiResponse.model_validate(responses[3].json())
    network_api = NetworkApiResponse.model_validate(responses[4].json())
    balances_api = BenefitBalanceApiResponse.model_validate(responses[5].json())
    lifetime_api = LifetimeStateApiResponse.model_validate(responses[6].json())
    endorsements_api = EndorsementApiResponse.model_validate(responses[7].json())

    # Map responses into internal domain models
    policy_data = PolicyData(
        policy_id=policy_api.policy_id,
        product_code=policy_api.product_code,
        variant=policy_api.policy_variant,
        policy_start_date=policy_api.policy_start_date,
        policy_end_date=policy_api.policy_end_date,
        base_sum_insured=policy_api.base_sum_insured,
        status=policy_api.status,
        premium_paid=True,
        policy_type="individual",
        policy_term_years=1,
        co_payment_percent=policy_api.co_pay_option,
        annual_aggregate_deductible=policy_api.deductible_option,
        room_category_entitled=policy_api.room_category_entitled,
        borderless_opted="borderless" in policy_api.optional_riders,
        borderless_specific_illness_opted="borderless_specific_illness" in policy_api.optional_riders,
        unlimited_si_opted="unlimited_si" in policy_api.optional_riders,
        modern_treatments_plus_opted="modern_treatments_plus" in policy_api.optional_riders,
        air_ambulance_plus_opted="air_ambulance_plus" in policy_api.optional_riders,
        heads_up_opted="heads_up" in policy_api.optional_riders,
        tiered_network_opted="tiered_network" in policy_api.optional_riders,
        room_rent_limit=4000.0 if policy_api.policy_variant == "Select" else (None if policy_api.policy_variant == "Elite" else 3000.0),
        hospital_daily_cash_amount=1000.0,
        pa_sum_insured=100000.0,
        personal_waiting_period_months=0
    )

    member_data = MemberData(
        member_id=member_api.member_id,
        policy_id=member_api.policy_id,
        name=member_api.name,
        age=member_api.age,
        entry_age=member_api.age,
        relationship=member_api.relationship,
        date_of_addition=member_api.date_of_addition,
        ped_declarations=member_api.ped_declarations,
        eligibility_active=member_api.eligibility_active
    )

    history_data = ClaimsHistoryData(
        policy_id=history_api.policy_id,
        member_id=history_api.member_id,
        prior_claims_count=history_api.prior_claims_count,
        total_utilized_si=history_api.total_prior_amount_paid,
        prior_exclusions_triggered=history_api.cumulative_exclusions_triggered
    )

    porting_data = PortingMigrationData(
        policy_id=porting_api.policy_id,
        porting_applicable=porting_api.is_ported_policy,
        prior_coverage_months=porting_api.continuous_coverage_months,
        waiting_period_credit_months=porting_api.waiting_period_credit_months,
        moratorium_eligible=porting_api.moratorium_eligible_months > 0
    )

    network_data = NetworkData(
        provider_id=network_api.provider_id,
        provider_name=network_api.hospital_name,
        provider_type=network_api.network_tier if network_api.network_tier in ("Network", "Excluded") else "Non-Network",
        tiered_network_member=network_api.network_tier == "Tiered",
        heads_up_recommended=network_api.heads_up_recommended
    )

    balance_data = BenefitBalanceData(
        policy_id=balances_api.policy_id,
        base_si_remaining=balances_api.base_si_remaining,
        booster_plus_remaining=balances_api.booster_plus_remaining,
        reassure_forever_pool=balances_api.reassure_forever_pool,
        cash_bag_plus_wallet=balances_api.cash_bag_plus_wallet_balance,
        hospital_cash_days_used=balances_api.hospital_cash_days_used,
        deductible_consumed_ytd=0.0
    )

    lifetime_data = LifetimeStateData(
        policy_id=lifetime_api.policy_id,
        reassure_forever_triggered=lifetime_api.reassure_forever_triggered,
        lock_the_clock_age_locked=lifetime_api.lock_the_clock_age_locked,
        lock_the_clock_entry_age=lifetime_api.current_premium_age,
        lock_the_clock_current_premium_age=lifetime_api.current_premium_age,
        convalescence_claimed=lifetime_api.convalescence_claimed,
        critical_illness_claimed=lifetime_api.critical_illness_claimed,
        live_healthy=LiveHealthyData(
            current_points=lifetime_api.live_healthy.current_points if lifetime_api.live_healthy else 0,
            points_snapshot_date=lifetime_api.live_healthy.points_snapshot_date if lifetime_api.live_healthy else None
        ) if lifetime_api.live_healthy else LiveHealthyData(),
        cash_bag_plus=CashBagPlusData(
            balance=lifetime_api.cash_bag_plus.balance if lifetime_api.cash_bag_plus else 0.0,
            last_credited=lifetime_api.cash_bag_plus.last_credited if lifetime_api.cash_bag_plus else None
        ) if lifetime_api.cash_bag_plus else CashBagPlusData()
    )

    endorsements_data = []
    for item in endorsements_api.endorsements:
        endorsements_data.append(EndorsementData(
            endorsement_id=item.endorsement_id,
            policy_id=item.policy_id,
            endorsement_type=item.type,
            effective_date=item.effective_date,
            details=item.mutated_fields
        ))

    line_items_data = []
    for item in raw_line_items:
        if isinstance(item, LineItemData):
            line_items_data.append(item)
        else:
            line_items_data.append(LineItemData.model_validate(item))

    # Construct unified ClaimContext object
    claim_context = ClaimContext(
        claim_id=claim_id,
        claim_received_at=datetime.now(timezone.utc),
        policy=policy_data,
        member=member_data,
        history=history_data,
        porting=porting_data,
        network=network_data,
        benefit_balance=balance_data,
        lifetime_state=lifetime_data,
        endorsements=endorsements_data,
        line_items=line_items_data,
        product_json_version="R3_v2.1_2025-01-15",
        context_assembled_at=datetime.now(timezone.utc)
    )

    return claim_context
