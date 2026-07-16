"""
Database seed script — imports all mock data from integration.claim_data
into the PostgreSQL ledger.

Run once after `create_all_tables()` or an Alembic upgrade:
    python -m db.seed

The script is idempotent: each upsert uses INSERT ... ON CONFLICT DO NOTHING
so re-running it against an already-seeded database is safe.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path when running directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.engine import create_all_tables, get_db_session
from db.models import (
    Policy, Member, ClaimsHistory, PortingRecord,
    NetworkProvider, BenefitBalance, LifetimeState, Endorsement,
)
from integration.claim_data import (
    POLICY_DATA, MEMBER_DATA, CLAIMS_HISTORY_DATA, PORTING_DATA,
    NETWORK_DATA, BALANCES_DATA, LIFETIME_STATE_DATA, ENDORSEMENT_DATA,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
_log = logging.getLogger("db.seed")


def _parse_dt(value: str | None):
    """Parse an ISO datetime string to a naive datetime (UTC)."""
    if value is None:
        return None
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        return value
    # Handle both "Z" and "+00:00" suffixes
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


async def seed() -> None:
    _log.info("Creating tables if not already present...")
    await create_all_tables()

    async with get_db_session() as session:
        # ── Policies ──────────────────────────────────────────────────────────
        _log.info("Seeding %d policies...", len(POLICY_DATA))
        for pid, raw in POLICY_DATA.items():
            stmt = pg_insert(Policy).values(
                policy_id=raw["policy_id"],
                status=raw["status"],
                product_code=raw["product_code"],
                policy_variant=raw["policy_variant"],
                policy_start_date=_parse_dt(raw["policy_start_date"]),
                policy_end_date=_parse_dt(raw["policy_end_date"]),
                base_sum_insured=raw["base_sum_insured"],
                optional_riders=raw.get("optional_riders", []),
                co_pay_option=raw.get("co_pay_option"),
                deductible_option=raw.get("deductible_option"),
                room_category_entitled=raw["room_category_entitled"],
                premium_paid=raw.get("premium_paid", True),
                policy_type=raw.get("policy_type", "individual"),
                hospital_daily_cash_amount=raw.get("hospital_daily_cash_amount"),
                pa_sum_insured=raw.get("pa_sum_insured"),
            ).on_conflict_do_nothing(index_elements=["policy_id"])
            await session.execute(stmt)
        _log.info("Policies seeded.")

        # ── Members ───────────────────────────────────────────────────────────
        _log.info("Seeding %d members...", len(MEMBER_DATA))
        for mid, raw in MEMBER_DATA.items():
            stmt = pg_insert(Member).values(
                member_id=raw["member_id"],
                policy_id=raw["policy_id"],
                name=raw.get("name", "Unknown"),
                age=raw["age"],
                relation_type=raw["relationship"],
                date_of_addition=_parse_dt(raw["date_of_addition"]),
                ped_declarations=raw.get("ped_declarations", []),
                eligibility_active=raw.get("eligibility_active", True),
            ).on_conflict_do_nothing(index_elements=["member_id"])
            await session.execute(stmt)
        _log.info("Members seeded.")

        # ── Claims History ────────────────────────────────────────────────────
        _log.info("Seeding %d claims history records...", len(CLAIMS_HISTORY_DATA))
        for key, raw in CLAIMS_HISTORY_DATA.items():
            stmt = pg_insert(ClaimsHistory).values(
                policy_id=raw["policy_id"],
                member_id=raw["member_id"],
                prior_claims_count=raw.get("prior_claims_count", 0),
                total_prior_amount_paid=raw.get("total_prior_amount_paid", 0.0),
                cumulative_exclusions_triggered=raw.get("cumulative_exclusions_triggered", []),
                deductible_consumed_ytd=raw.get("deductible_consumed_ytd", 0.0),
                last_claim_date=_parse_dt(raw.get("last_claim_date")),
                claim_free_years=raw.get("claim_free_years", 0),
            ).on_conflict_do_nothing(index_elements=None)
            # On conflict for the composite unique: use named constraint
            stmt = pg_insert(ClaimsHistory).values(
                policy_id=raw["policy_id"],
                member_id=raw["member_id"],
                prior_claims_count=raw.get("prior_claims_count", 0),
                total_prior_amount_paid=raw.get("total_prior_amount_paid", 0.0),
                cumulative_exclusions_triggered=raw.get("cumulative_exclusions_triggered", []),
                deductible_consumed_ytd=raw.get("deductible_consumed_ytd", 0.0),
                last_claim_date=_parse_dt(raw.get("last_claim_date")),
                claim_free_years=raw.get("claim_free_years", 0),
            ).on_conflict_do_nothing(constraint="uq_claims_history_policy_member")
            await session.execute(stmt)
        _log.info("Claims history seeded.")

        # ── Porting Records ───────────────────────────────────────────────────
        _log.info("Seeding %d porting records...", len(PORTING_DATA))
        for pid, raw in PORTING_DATA.items():
            stmt = pg_insert(PortingRecord).values(
                policy_id=raw["policy_id"],
                is_ported_policy=raw.get("is_ported_policy", False),
                waiting_period_credit_months=raw.get("waiting_period_credit_months", 0),
                moratorium_eligible_months=raw.get("moratorium_eligible_months", 0),
                continuous_coverage_months=raw.get("continuous_coverage_months", 0),
            ).on_conflict_do_nothing(index_elements=["policy_id"])
            await session.execute(stmt)
        _log.info("Porting records seeded.")

        # ── Network Providers ─────────────────────────────────────────────────
        _log.info("Seeding %d network providers...", len(NETWORK_DATA))
        for prid, raw in NETWORK_DATA.items():
            stmt = pg_insert(NetworkProvider).values(
                provider_id=raw["provider_id"],
                hospital_name=raw["hospital_name"],
                network_tier=raw["network_tier"],
                heads_up_recommended=raw.get("heads_up_recommended", False),
            ).on_conflict_do_nothing(index_elements=["provider_id"])
            await session.execute(stmt)
        _log.info("Network providers seeded.")

        # ── Benefit Balances ──────────────────────────────────────────────────
        _log.info("Seeding %d benefit balance records...", len(BALANCES_DATA))
        for key, raw in BALANCES_DATA.items():
            stmt = pg_insert(BenefitBalance).values(
                policy_id=raw["policy_id"],
                member_id=raw["member_id"],
                base_si_remaining=raw.get("base_si_remaining", 0.0),
                booster_plus_remaining=raw.get("booster_plus_remaining", 0.0),
                reassure_forever_pool=raw.get("reassure_forever_pool", 0.0),
                cash_bag_plus_wallet_balance=raw.get("cash_bag_plus_wallet_balance", 0.0),
                hospital_cash_days_used=raw.get("hospital_cash_days_used", 0),
                personal_accident_limit_remaining=raw.get("personal_accident_limit_remaining", 0.0),
            ).on_conflict_do_nothing(constraint="uq_benefit_balance_policy_member")
            await session.execute(stmt)
        _log.info("Benefit balances seeded.")

        # ── Lifetime States ───────────────────────────────────────────────────
        _log.info("Seeding %d lifetime state records...", len(LIFETIME_STATE_DATA))
        for pid, raw in LIFETIME_STATE_DATA.items():
            stmt = pg_insert(LifetimeState).values(
                policy_id=raw["policy_id"],
                lock_the_clock_age_locked=raw.get("lock_the_clock_age_locked", False),
                current_premium_age=raw.get("current_premium_age", 0),
                reassure_forever_triggered=raw.get("reassure_forever_triggered", False),
                reassure_forever_triggered_date=_parse_dt(raw.get("reassure_forever_triggered_date")),
                reassure_forever_triggered_claim_id=raw.get("reassure_forever_triggered_claim_id"),
                lock_the_clock_unlocked_date=_parse_dt(raw.get("lock_the_clock_unlocked_date")),
                convalescence_claimed=raw.get("convalescence_claimed", False),
                critical_illness_claimed=raw.get("critical_illness_claimed", False),
                critical_illness_type=raw.get("critical_illness_type"),
                booster_plus_accumulated=raw.get("booster_plus_accumulated", 0.0),
                booster_plus_last_updated=_parse_dt(raw.get("booster_plus_last_updated")),
                live_healthy=raw.get("live_healthy"),
                cash_bag_plus=raw.get("cash_bag_plus"),
            ).on_conflict_do_nothing(index_elements=["policy_id"])
            await session.execute(stmt)
        _log.info("Lifetime states seeded.")

        # ── Endorsements ──────────────────────────────────────────────────────
        total_endorsements = sum(len(v.get("endorsements", [])) for v in ENDORSEMENT_DATA.values())
        _log.info("Seeding %d endorsements...", total_endorsements)
        for pid, container in ENDORSEMENT_DATA.items():
            for raw_end in container.get("endorsements", []):
                stmt = pg_insert(Endorsement).values(
                    endorsement_id=raw_end["endorsement_id"],
                    policy_id=raw_end["policy_id"],
                    type=raw_end["type"],
                    effective_date=_parse_dt(raw_end["effective_date"]),
                    mutated_fields=raw_end.get("mutated_fields", {}),
                ).on_conflict_do_nothing(index_elements=["endorsement_id"])
                await session.execute(stmt)
        _log.info("Endorsements seeded.")

    _log.info("Seed complete. Database is ready.")


if __name__ == "__main__":
    asyncio.run(seed())
