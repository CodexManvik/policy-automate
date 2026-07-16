"""
Repository layer — async data access objects for each domain entity.

Each repository function accepts an AsyncSession and returns a dict
matching the shape expected by the mock_routers response models.
This keeps the router layer thin and the DB access centralized.

SELECT ... FOR UPDATE is used on the mutable tables (BenefitBalance,
LifetimeState) to provide row-level pessimistic locking during the
adjudication write-path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Policy, Member, ClaimsHistory, PortingRecord,
    NetworkProvider, BenefitBalance, LifetimeState, Endorsement,
)

_log = logging.getLogger("db.repositories")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict (excludes relationships)."""
    data = {
        col.key: getattr(row, col.key)
        for col in row.__mapper__.column_attrs
    }
    if "relation_type" in data:
        data["relationship"] = data.pop("relation_type")
    return data


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

async def get_policy(session: AsyncSession, policy_id: str) -> Optional[Dict[str, Any]]:
    stmt = select(Policy).where(Policy.policy_id == policy_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    data = _row_to_dict(row)
    # Ensure numeric types are plain float for Pydantic compatibility
    for f in ("base_sum_insured", "co_pay_option", "deductible_option",
              "hospital_daily_cash_amount", "pa_sum_insured"):
        if data.get(f) is not None:
            data[f] = float(data[f])
    return data


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------

async def get_member(session: AsyncSession, member_id: str) -> Optional[Dict[str, Any]]:
    stmt = select(Member).where(Member.member_id == member_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Claims History
# ---------------------------------------------------------------------------

async def get_claims_history(
    session: AsyncSession, policy_id: str, member_id: str
) -> Optional[Dict[str, Any]]:
    stmt = select(ClaimsHistory).where(
        ClaimsHistory.policy_id == policy_id,
        ClaimsHistory.member_id == member_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        # Fallback: find any history row for this policy (covers partial member_id omission)
        stmt_fallback = select(ClaimsHistory).where(
            ClaimsHistory.policy_id == policy_id
        ).limit(1)
        result = await session.execute(stmt_fallback)
        row = result.scalar_one_or_none()
    if row is None:
        return None
    data = _row_to_dict(row)
    data.pop("id", None)  # Internal PK — not part of the API schema
    for f in ("total_prior_amount_paid", "deductible_consumed_ytd"):
        if data.get(f) is not None:
            data[f] = float(data[f])
    return data


# ---------------------------------------------------------------------------
# Porting
# ---------------------------------------------------------------------------

async def get_porting(session: AsyncSession, policy_id: str) -> Optional[Dict[str, Any]]:
    stmt = select(PortingRecord).where(PortingRecord.policy_id == policy_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Network Provider
# ---------------------------------------------------------------------------

async def get_network_provider(session: AsyncSession, provider_id: str) -> Optional[Dict[str, Any]]:
    stmt = select(NetworkProvider).where(NetworkProvider.provider_id == provider_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Benefit Balances  (read-only path — write-path uses FOR UPDATE in pipeline)
# ---------------------------------------------------------------------------

async def get_benefit_balance(
    session: AsyncSession, policy_id: str, member_id: str, for_update: bool = False
) -> Optional[Dict[str, Any]]:
    stmt = select(BenefitBalance).where(
        BenefitBalance.policy_id == policy_id,
        BenefitBalance.member_id == member_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    data = _row_to_dict(row)
    data.pop("id", None)
    for f in ("base_si_remaining", "booster_plus_remaining", "reassure_forever_pool",
              "cash_bag_plus_wallet_balance", "personal_accident_limit_remaining"):
        if data.get(f) is not None:
            data[f] = float(data[f])
    return data


# ---------------------------------------------------------------------------
# Lifetime State  (read-only path — write-path uses FOR UPDATE in pipeline)
# ---------------------------------------------------------------------------

async def get_lifetime_state(
    session: AsyncSession, policy_id: str, for_update: bool = False
) -> Optional[Dict[str, Any]]:
    stmt = select(LifetimeState).where(LifetimeState.policy_id == policy_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Endorsements
# ---------------------------------------------------------------------------

async def get_endorsements(
    session: AsyncSession, policy_id: str
) -> Optional[Dict[str, Any]]:
    stmt = select(Endorsement).where(Endorsement.policy_id == policy_id)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    # Return in the shape EndorsementApiResponse expects
    return {
        "endorsements": [_row_to_dict(r) for r in rows]
    }
