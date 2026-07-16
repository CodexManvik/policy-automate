"""
Core context orchestrator — assembles the unified ClaimContext from 8 external gateways.

Logging strategy:
  - Every gateway call is timed and its HTTP status is logged.
  - Every field-mapping decision that involves a fallback, derivation, or hardcode is logged at INFO.
  - Unexpected or potentially missing values are logged at WARNING.
  - The fully assembled ClaimContext is dumped to logs/claims/<claim_id>_ctx_<ts>.json.
"""

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from schemas import (
    ClaimContext, PolicyData, MemberData, ClaimsHistoryData,
    PortingMigrationData, NetworkData, BenefitBalanceData,
    LifetimeStateData, EndorsementData, LineItemData,
    LiveHealthyData, CashBagPlusData,
)
from integration.gateway_schemas import (
    PolicyApiResponse, MemberApiResponse, ClaimsHistoryApiResponse,
    PortingApiResponse, NetworkApiResponse, BenefitBalanceApiResponse,
    LifetimeStateApiResponse, EndorsementApiResponse,
    GetAuthTokenRequest, AuthTokenResponse, GetPolicyDetailsRequest,
    GetClaimHistoryRequest, GetPolicyDataRequest,
)
from product_memory import resolve_product_version

# ---------------------------------------------------------------------------
# Module-level logger — writes to the same logs/agent_reasoning.log via root
# hierarchy, but also accepted by the AgentReasoningLogger file handler
# because it is named under the same package.
# ---------------------------------------------------------------------------
_log = logging.getLogger("context_builder")

# Directory for per-claim context snapshots
_WORKSPACE_ROOT = Path(__file__).parent.parent.parent
_CLAIMS_LOG_DIR = _WORKSPACE_ROOT / "logs" / "claims"

_GATEWAY_LABELS = [
    "policy",
    "member",
    "claims_history",
    "porting",
    "network_provider",
    "benefit_balances",
    "lifetime_state",
    "endorsements",
]


def _default_json_serializer(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return repr(obj)


def _dump_context_snapshot(claim_id: str, payload: dict) -> None:
    """Write the assembled context to a JSON file in logs/claims/."""
    try:
        _CLAIMS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_id = claim_id.replace("/", "_").replace("\\", "_")
        out_path = _CLAIMS_LOG_DIR / f"{safe_id}_ctx_{ts}.json"
        with open(str(out_path), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=_default_json_serializer)
        _log.info(
            "CONTEXT_SNAPSHOT_WRITTEN | claim=%s | file=%s | bytes=%d",
            claim_id, out_path.name, out_path.stat().st_size,
        )
    except OSError as exc:
        _log.warning(
            "CONTEXT_SNAPSHOT_WRITE_FAILED | claim=%s | error=%s", claim_id, exc
        )


async def assemble_claim_context(
    claim_id: str,
    policy_id: str,
    member_id: str,
    provider_id: str,
    raw_line_items: list,
    base_url: str = "http://localhost:8000",
) -> ClaimContext:
    """
    Assemble the unified ClaimContext by concurrently querying the 8 external gateways.
    Emits structured log lines at each assembly stage.
    """
    _log.info(
        "CONTEXT_BUILD_START | claim=%s | policy=%s | member=%s | provider=%s | line_items=%d",
        claim_id, policy_id, member_id, provider_id, len(raw_line_items),
    )

    # ── 1. Concurrent gateway fetch ──────────────────────────────────────────
    t0 = time.monotonic()
    
    display_urls = [
        "/caseapi/api/policy/getcustomerpolicydetail",
        f"/member/{member_id}",
        "/caseapi/api/claim/getclaimhistory",
        f"/porting/{policy_id}",
        f"/network-provider/{provider_id}",
        f"/balances/{policy_id}/{member_id}",
        f"/lifetime-state/{policy_id}",
        "/caseapi/api/policy/getpolicydata"
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step A: Authenticate
        auth_req = GetAuthTokenRequest(
            UserID="MOBILE_APP",
            Client_id="mock_client_id",
            Identifier_Code="mock_identifier"
        )
        auth_resp = await client.post(
            f"{base_url}/api/external/caseapi/api/auth/getauthtoken",
            json=auth_req.model_dump(),
            headers={
                "x-apigw-api-id": "mock_api_gateway_id",
                "Content-Type": "application/json"
            }
        )
        if auth_resp.status_code != 200:
            raise ValueError(
                f"External CaseAPI authentication failed: HTTP {auth_resp.status_code}: {auth_resp.text[:200]}"
            )
            
        token_data = AuthTokenResponse.model_validate(auth_resp.json())
        bearer_token = token_data.access_token

        # Step B: Concurrently fetch all gateways (using auth tokens where required)
        auth_headers = {
            "x-apigw-api-id": "mock_api_gateway_id",
            "access_token": bearer_token,
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        tasks = [
            # index 0: Policy (POST)
            client.post(
                f"{base_url}/api/external/caseapi/api/policy/getcustomerpolicydetail",
                json={"PolicyNumber": policy_id},
                headers=auth_headers
            ),
            # index 1: Member (GET)
            client.get(f"{base_url}/api/external/member/{member_id}"),
            # index 2: Claims History (POST)
            client.post(
                f"{base_url}/api/external/caseapi/api/claim/getclaimhistory",
                json={
                    "PolicyNo_COI": policy_id,
                    "Membership_No_ID": member_id,
                    "AllowedInactiveRecord": "Y"
                },
                headers=auth_headers
            ),
            # index 3: Porting (GET)
            client.get(f"{base_url}/api/external/porting/{policy_id}"),
            # index 4: Network Provider (GET)
            client.get(f"{base_url}/api/external/network-provider/{provider_id}"),
            # index 5: Balances (GET)
            client.get(f"{base_url}/api/external/balances/{policy_id}/{member_id}"),
            # index 6: Lifetime State (GET)
            client.get(f"{base_url}/api/external/lifetime-state/{policy_id}"),
            # index 7: Endorsements (POST)
            client.post(
                f"{base_url}/api/external/caseapi/api/policy/getpolicydata",
                json={"PolicyNo_COI": policy_id},
                headers={
                    "access_token": bearer_token,
                    "Authorization": f"Bearer {bearer_token}",
                    "Content-Type": "application/json"
                }
            )
        ]
        responses = await asyncio.gather(*tasks)

    elapsed_ms = (time.monotonic() - t0) * 1000

    _log.info(
        "GATEWAY_BATCH_COMPLETE | claim=%s | total_elapsed_ms=%.1f | gateways=%d",
        claim_id, elapsed_ms, len(responses),
    )

    # Log individual response statuses
    for label, url, resp in zip(_GATEWAY_LABELS, display_urls, responses):
        level = logging.INFO if resp.status_code == 200 else logging.WARNING
        _log.log(
            level,
            "GATEWAY_RESPONSE | claim=%s | gateway=%s | status=%d | url=%s",
            claim_id, label, resp.status_code, url,
        )

    # Hard-fail on any non-200
    for idx, resp in enumerate(responses):
        if resp.status_code != 200:
            label = _GATEWAY_LABELS[idx]
            _log.error(
                "GATEWAY_FETCH_FAILED | claim=%s | gateway=%s | status=%d | body=%s",
                claim_id, label, resp.status_code, resp.text[:400],
            )
            raise ValueError(
                f"External gateway '{label}' returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

    # ── 2. Parse gateway responses into integration schemas ───────────────────
    _log.info("GATEWAY_PARSE_START | claim=%s", claim_id)
    policy_api      = PolicyApiResponse.model_validate(responses[0].json())
    member_api      = MemberApiResponse.model_validate(responses[1].json())
    history_api     = ClaimsHistoryApiResponse.model_validate(responses[2].json())
    porting_api     = PortingApiResponse.model_validate(responses[3].json())
    network_api     = NetworkApiResponse.model_validate(responses[4].json())
    balances_api    = BenefitBalanceApiResponse.model_validate(responses[5].json())
    lifetime_api    = LifetimeStateApiResponse.model_validate(responses[6].json())
    endorsements_api = EndorsementApiResponse.model_validate(responses[7].json())
    _log.info("GATEWAY_PARSE_OK | claim=%s | endorsements=%d", claim_id, len(endorsements_api.endorsements))

    # ── 3. PolicyData ─────────────────────────────────────────────────────────
    _log.info(
        "BUILD_POLICY | claim=%s | policy_id=%s | variant=%s | base_si=%.2f | status=%s",
        claim_id, policy_api.policy_id, policy_api.policy_variant,
        policy_api.base_sum_insured, policy_api.status,
    )

    # Room rent limit derivation — hardcoded by variant tier
    _room_rent_limit: float | None
    if policy_api.policy_variant == "Select":
        _room_rent_limit = 4000.0
    elif policy_api.policy_variant == "Elite":
        _room_rent_limit = None  # No cap on Elite
    else:
        _room_rent_limit = 3000.0  # Classic default
    _log.info(
        "POLICY_ROOM_RENT | claim=%s | variant=%s | room_rent_limit=%s",
        claim_id, policy_api.policy_variant, _room_rent_limit,
    )

    # Optional riders
    _riders = set(policy_api.optional_riders)
    _log.info("POLICY_RIDERS | claim=%s | riders=%s", claim_id, sorted(_riders))

    # Previously hardcoded fields — now sourced from PolicyApiResponse.
    # Log each to maintain audit trail visibility.
    _log.info(
        "POLICY_API_SOURCED | claim=%s | premium_paid=%s | policy_type=%s "
        "| hospital_daily_cash_amount=%s | pa_sum_insured=%s",
        claim_id,
        policy_api.premium_paid,
        policy_api.policy_type,
        policy_api.hospital_daily_cash_amount,
        policy_api.pa_sum_insured,
    )

    if policy_api.co_pay_option is None:
        _log.info("POLICY_COPAY | claim=%s | co_pay_option=None (no co-pay)", claim_id)
    else:
        _log.info("POLICY_COPAY | claim=%s | co_pay_option=%.1f%%", claim_id, policy_api.co_pay_option)

    if policy_api.deductible_option is None:
        _log.info("POLICY_DEDUCTIBLE | claim=%s | deductible_option=None (no deductible)", claim_id)
    else:
        _log.info("POLICY_DEDUCTIBLE | claim=%s | deductible_option=%.2f", claim_id, policy_api.deductible_option)

    policy_data = PolicyData(
        policy_id=policy_api.policy_id,
        product_code=policy_api.product_code,
        variant=policy_api.policy_variant,
        policy_start_date=policy_api.policy_start_date,
        policy_end_date=policy_api.policy_end_date,
        base_sum_insured=policy_api.base_sum_insured,
        status=policy_api.status,
        premium_paid=policy_api.premium_paid,
        policy_type=policy_api.policy_type,
        policy_term_years=1,                   # hardcoded — derived from date diff in a future iteration
        co_payment_percent=policy_api.co_pay_option,
        annual_aggregate_deductible=policy_api.deductible_option,
        room_category_entitled=policy_api.room_category_entitled,
        borderless_opted="borderless" in _riders,
        borderless_specific_illness_opted="borderless_specific_illness" in _riders,
        unlimited_si_opted="unlimited_si" in _riders,
        modern_treatments_plus_opted="modern_treatments_plus" in _riders,
        air_ambulance_plus_opted="air_ambulance_plus" in _riders,
        heads_up_opted="heads_up" in _riders,
        tiered_network_opted="tiered_network" in _riders,
        room_rent_limit=_room_rent_limit,
        hospital_daily_cash_amount=policy_api.hospital_daily_cash_amount,
        pa_sum_insured=policy_api.pa_sum_insured,
        personal_waiting_period_months=0,      # hardcoded — cleared at enrollment
    )

    # ── 4. MemberData ─────────────────────────────────────────────────────────
    _log.info(
        "BUILD_MEMBER | claim=%s | member_id=%s | name=%s | age=%d | relationship=%s | peds=%s | eligible=%s",
        claim_id, member_api.member_id, member_api.name,
        member_api.age, member_api.relationship,
        member_api.ped_declarations, member_api.eligibility_active,
    )
    # NOTE: entry_age is defaulted to current age — ideally sourced from enrollment record.
    _log.info(
        "MEMBER_ENTRY_AGE_DEFAULTED | claim=%s | using_current_age=%d as entry_age",
        claim_id, member_api.age,
    )

    member_data = MemberData(
        member_id=member_api.member_id,
        policy_id=member_api.policy_id,
        name=member_api.name,
        age=member_api.age,
        entry_age=member_api.age,   # TODO: source from enrollment API when available
        relationship=member_api.relationship,
        date_of_addition=member_api.date_of_addition,
        ped_declarations=member_api.ped_declarations,
        eligibility_active=member_api.eligibility_active,
    )

    # ── 5. ClaimsHistoryData ──────────────────────────────────────────────────
    # All three fields are now sourced directly from the gateway response.
    # `last_claim_date` and `claim_free_years` were previously read via getattr
    # fallbacks because they were absent from ClaimsHistoryApiResponse — those
    # fields have now been added to the schema and the seed data.
    # `deductible_consumed_ytd` replaces the hardcoded 0.0 that caused incorrect
    # double-deductible application when a prior claim had already partially
    # consumed the policy year deductible.
    _last_claim = history_api.last_claim_date
    _claim_free = history_api.claim_free_years
    _deductible_ytd = history_api.deductible_consumed_ytd
    _log.info(
        "BUILD_HISTORY | claim=%s | prior_claims=%d | total_utilized_si=%.2f "
        "| last_claim_date=%s | claim_free_years=%s | deductible_consumed_ytd=%.2f | exclusions=%s",
        claim_id, history_api.prior_claims_count,
        history_api.total_prior_amount_paid,
        _last_claim, _claim_free, _deductible_ytd,
        history_api.cumulative_exclusions_triggered,
    )

    history_data = ClaimsHistoryData(
        policy_id=history_api.policy_id,
        member_id=history_api.member_id,
        prior_claims_count=history_api.prior_claims_count,
        total_utilized_si=history_api.total_prior_amount_paid,
        last_claim_date=_last_claim,
        prior_exclusions_triggered=history_api.cumulative_exclusions_triggered,
        claim_free_years=_claim_free,
    )

    # ── 6. PortingMigrationData ───────────────────────────────────────────────
    _moratorium_eligible = porting_api.moratorium_eligible_months > 0
    _log.info(
        "BUILD_PORTING | claim=%s | ported=%s | prior_coverage_months=%d "
        "| wp_credit_months=%d | moratorium_eligible=%s (months=%d)",
        claim_id, porting_api.is_ported_policy,
        porting_api.continuous_coverage_months,
        porting_api.waiting_period_credit_months,
        _moratorium_eligible, porting_api.moratorium_eligible_months,
    )

    porting_data = PortingMigrationData(
        policy_id=porting_api.policy_id,
        porting_applicable=porting_api.is_ported_policy,
        prior_coverage_months=porting_api.continuous_coverage_months,
        waiting_period_credit_months=porting_api.waiting_period_credit_months,
        moratorium_eligible=_moratorium_eligible,
    )

    # ── 7. NetworkData ────────────────────────────────────────────────────────
    _raw_tier = network_api.network_tier
    if _raw_tier in ("Network", "Excluded"):
        _provider_type = _raw_tier
    else:
        _provider_type = "Non-Network"
    _tiered = _raw_tier == "Tiered"

    if _provider_type == "Non-Network":
        _log.warning(
            "NETWORK_NON_NETWORK | claim=%s | provider_id=%s | raw_tier=%s → mapped to Non-Network",
            claim_id, network_api.provider_id, _raw_tier,
        )
    else:
        _log.info(
            "BUILD_NETWORK | claim=%s | provider_id=%s | hospital=%s | type=%s | tiered=%s | heads_up=%s",
            claim_id, network_api.provider_id, network_api.hospital_name,
            _provider_type, _tiered, network_api.heads_up_recommended,
        )

    network_data = NetworkData(
        provider_id=network_api.provider_id,
        provider_name=network_api.hospital_name,
        provider_type=_provider_type,
        tiered_network_member=_tiered,
        heads_up_recommended=network_api.heads_up_recommended,
    )

    # ── 8. BenefitBalanceData ─────────────────────────────────────────────────
    _log.info(
        "BUILD_BALANCES | claim=%s | base_si_remaining=%.2f | booster_plus=%.2f "
        "| reassure_forever_pool=%.2f | cash_bag_plus=%.2f | hdc_days_used=%d | deductible_ytd=0.0 (hardcoded)",
        claim_id,
        balances_api.base_si_remaining,
        balances_api.booster_plus_remaining,
        balances_api.reassure_forever_pool,
        balances_api.cash_bag_plus_wallet_balance,
        balances_api.hospital_cash_days_used,
    )
    if balances_api.base_si_remaining <= 0:
        _log.warning(
            "BALANCE_SI_EXHAUSTED | claim=%s | base_si_remaining=%.2f — SI fully depleted before this claim",
            claim_id, balances_api.base_si_remaining,
        )

    balance_data = BenefitBalanceData(
        policy_id=balances_api.policy_id,
        base_si_remaining=balances_api.base_si_remaining,
        booster_plus_remaining=balances_api.booster_plus_remaining,
        reassure_forever_pool=balances_api.reassure_forever_pool,
        cash_bag_plus_wallet=balances_api.cash_bag_plus_wallet_balance,
        hospital_cash_days_used=balances_api.hospital_cash_days_used,
        # Fix 2: sourced from claims history API instead of hardcoded 0.0.
        # Prevents double-deductible application when a prior claim in the same
        # policy year has already partially consumed the aggregate deductible.
        deductible_consumed_ytd=_deductible_ytd,
    )

    # ── 9. LifetimeStateData ──────────────────────────────────────────────────
    _forever_triggered = lifetime_api.reassure_forever_triggered
    _forever_date = getattr(lifetime_api, "reassure_forever_triggered_date", None)
    _forever_claim = getattr(lifetime_api, "reassure_forever_triggered_claim_id", None)
    _ltc_locked = lifetime_api.lock_the_clock_age_locked
    _ltc_unlock = getattr(lifetime_api, "lock_the_clock_unlocked_date", None)
    _ci_type = getattr(lifetime_api, "critical_illness_type", None)

    _log.info(
        "BUILD_LIFETIME | claim=%s | forever_triggered=%s | forever_date=%s "
        "| forever_trigger_claim=%s | ltc_locked=%s | ltc_unlock=%s "
        "| current_premium_age=%s | convalescence_claimed=%s | ci_claimed=%s | ci_type=%s",
        claim_id,
        _forever_triggered, _forever_date, _forever_claim,
        _ltc_locked, _ltc_unlock,
        lifetime_api.current_premium_age,
        lifetime_api.convalescence_claimed,
        lifetime_api.critical_illness_claimed, _ci_type,
    )

    if _forever_triggered and _forever_date is None:
        _log.warning(
            "LIFETIME_FOREVER_DATE_MISSING | claim=%s | reassure_forever_triggered=True "
            "but triggered_date not returned by gateway — waterfall may behave unexpectedly",
            claim_id,
        )

    # Live Healthy sub-object
    if lifetime_api.live_healthy:
        _lh = LiveHealthyData(
            current_points=lifetime_api.live_healthy.current_points,
            points_snapshot_date=lifetime_api.live_healthy.points_snapshot_date,
        )
        _log.info(
            "LIFETIME_LIVE_HEALTHY | claim=%s | points=%d | snapshot=%s",
            claim_id, _lh.current_points, _lh.points_snapshot_date,
        )
    else:
        _lh = LiveHealthyData()
        _log.warning(
            "LIFETIME_LIVE_HEALTHY_MISSING | claim=%s | live_healthy not returned by gateway — defaulting to 0 points",
            claim_id,
        )

    # Cash Bag+ sub-object
    if lifetime_api.cash_bag_plus:
        _cbp = CashBagPlusData(
            balance=lifetime_api.cash_bag_plus.balance,
            last_credited=lifetime_api.cash_bag_plus.last_credited,
        )
        _log.info(
            "LIFETIME_CASH_BAG_PLUS | claim=%s | balance=%.2f | last_credited=%s",
            claim_id, _cbp.balance, _cbp.last_credited,
        )
    else:
        _cbp = CashBagPlusData()
        _log.warning(
            "LIFETIME_CASH_BAG_MISSING | claim=%s | cash_bag_plus not returned by gateway — defaulting to 0.0",
            claim_id,
        )

    lifetime_data = LifetimeStateData(
        policy_id=lifetime_api.policy_id,
        reassure_forever_triggered=_forever_triggered,
        reassure_forever_triggered_date=_forever_date,
        reassure_forever_triggered_claim_id=_forever_claim,
        lock_the_clock_age_locked=_ltc_locked,
        lock_the_clock_entry_age=lifetime_api.current_premium_age,  # TODO: source true entry age from member enrollment
        lock_the_clock_unlocked_date=_ltc_unlock,
        lock_the_clock_current_premium_age=lifetime_api.current_premium_age,
        convalescence_claimed=lifetime_api.convalescence_claimed,
        critical_illness_claimed=lifetime_api.critical_illness_claimed,
        critical_illness_type=_ci_type,
        booster_plus_accumulated=getattr(lifetime_api, "booster_plus_accumulated", 0.0),
        booster_plus_last_updated=getattr(lifetime_api, "booster_plus_last_updated", None),
        live_healthy=_lh,
        cash_bag_plus=_cbp,
    )

    # ── 10. Endorsements ─────────────────────────────────────────────────────
    _log.info(
        "BUILD_ENDORSEMENTS | claim=%s | count=%d",
        claim_id, len(endorsements_api.endorsements),
    )
    endorsements_data = []
    for item in endorsements_api.endorsements:
        _log.info(
            "ENDORSEMENT | claim=%s | id=%s | type=%s | effective=%s | mutations=%s",
            claim_id, item.endorsement_id, item.type,
            item.effective_date, list(item.mutated_fields.keys()),
        )
        endorsements_data.append(EndorsementData(
            endorsement_id=item.endorsement_id,
            policy_id=item.policy_id,
            endorsement_type=item.type,
            effective_date=item.effective_date,
            details=item.mutated_fields,
        ))

    # ── 11. Line Items ────────────────────────────────────────────────────────
    _log.info("BUILD_LINE_ITEMS | claim=%s | raw_count=%d", claim_id, len(raw_line_items))
    line_items_data: list[LineItemData] = []
    for idx, item in enumerate(raw_line_items):
        if isinstance(item, LineItemData):
            line_items_data.append(item)
            _log.info(
                "LINE_ITEM | claim=%s | [%d] id=%s | bucket=%s | claimed=%.2f",
                claim_id, idx, item.line_item_id, item.benefit_bucket, item.claimed_amount,
            )
        else:
            validated = LineItemData.model_validate(item)
            line_items_data.append(validated)
            _log.info(
                "LINE_ITEM_VALIDATED | claim=%s | [%d] id=%s | bucket=%s | claimed=%.2f",
                claim_id, idx, validated.line_item_id,
                validated.benefit_bucket, validated.claimed_amount,
            )

    # ── 12. Resolve product version from registry (was hardcoded "R3_v2.1_2025-01-15") ─
    # resolve_product_version picks the latest APPROVED version whose effective date
    # is <= the policy start date, so policies issued at different times get the
    # correct rule set rather than a single pinned string.
    _effective_date = policy_api.policy_start_date.date()
    _resolved_version = resolve_product_version(
        policy_api.product_code,
        policy_api.policy_variant,
        _effective_date,
    )
    if _resolved_version is None:
        _log.warning(
            "PRODUCT_VERSION_NOT_FOUND | claim=%s | product=%s | variant=%s | effective=%s "
            "| falling back to R3_v2.1_2025-01-15",
            claim_id, policy_api.product_code, policy_api.policy_variant, _effective_date,
        )
        _product_json_version = "R3_v2.1_2025-01-15"
    else:
        _product_json_version = _resolved_version["version_id"]
        _log.info(
            "PRODUCT_VERSION_RESOLVED | claim=%s | version=%s | status=%s",
            claim_id, _product_json_version, _resolved_version["status"],
        )

    # ── 13. Assemble final ClaimContext ───────────────────────────────────────
    _log.info("CONTEXT_ASSEMBLE | claim=%s", claim_id)
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
        product_json_version=_product_json_version,
        context_assembled_at=datetime.now(timezone.utc),
    )

    # ── 13. Dump full context snapshot to logs/claims/ ────────────────────────
    _dump_context_snapshot(
        claim_id=claim_id,
        payload={
            "event": "CONTEXT_BUILDER_ASSEMBLED",
            "source": "context_builder.assemble_claim_context",
            "claim_id": claim_id,
            "gateway_elapsed_ms": round(elapsed_ms, 1),
            "endorsement_count": len(endorsements_data),
            "line_item_count": len(line_items_data),
            "context": claim_context.model_dump(mode="json"),
        },
    )

    _log.info(
        "CONTEXT_BUILD_COMPLETE | claim=%s | line_items=%d | endorsements=%d | elapsed_ms=%.1f",
        claim_id, len(line_items_data), len(endorsements_data), elapsed_ms,
    )
    return claim_context
