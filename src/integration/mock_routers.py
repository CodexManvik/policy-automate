"""
FastAPI mock routers simulating core enterprise microservice API endpoints.

Data source priority:
  1. PostgreSQL (via db.repositories) — used when DATABASE_URL is configured
     and the database is reachable.
  2. In-memory seed dicts (integration.claim_data) — fallback for local dev
     or when Postgres is not yet available.

The DB path is attempted first; any connection/lookup error falls back to
the in-memory dicts transparently, so existing tests continue to pass
without a running Postgres instance.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from integration.gateway_schemas import (
    PolicyApiResponse, MemberApiResponse, ClaimsHistoryApiResponse,
    PortingApiResponse, NetworkApiResponse, BenefitBalanceApiResponse,
    LifetimeStateApiResponse, EndorsementApiResponse, EndorsementItem,
    GetAuthTokenRequest, AuthTokenResponse, GetPolicyDetailsRequest,
    GetPolicyDetailsByMobileRequest, GetClaimHistoryRequest, GetPolicyDataRequest,
)
from integration.claim_data import (
    POLICY_DATA, MEMBER_DATA, CLAIMS_HISTORY_DATA, PORTING_DATA,
    NETWORK_DATA, BALANCES_DATA, LIFETIME_STATE_DATA, ENDORSEMENT_DATA,
)

router = APIRouter(prefix="/api/external")
_log = logging.getLogger("mock_routers")

# ---------------------------------------------------------------------------
# DB availability check — lazy, cached per-process
# ---------------------------------------------------------------------------

_DB_ENABLED: Optional[bool] = None  # None = not yet checked


def _db_enabled() -> bool:
    """Return True if DATABASE_URL is configured in the environment."""
    global _DB_ENABLED
    if _DB_ENABLED is None:
        _DB_ENABLED = bool(os.getenv("DATABASE_URL", "").strip())
        if _DB_ENABLED:
            _log.info("DATABASE_URL detected — mock_routers will use PostgreSQL backend")
        else:
            _log.info("DATABASE_URL not set — mock_routers using in-memory seed data")
    return _DB_ENABLED


# ---------------------------------------------------------------------------
# Unified fetch helpers — try DB, fall back to in-memory
# ---------------------------------------------------------------------------

async def _fetch_policy(policy_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_policy
            async with get_db_session() as session:
                return await get_policy(session, policy_id)
        except Exception as exc:
            _log.warning("DB policy fetch failed (%s) — using in-memory fallback", exc)
    return POLICY_DATA.get(policy_id)


async def _fetch_member(member_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_member
            async with get_db_session() as session:
                return await get_member(session, member_id)
        except Exception as exc:
            _log.warning("DB member fetch failed (%s) — using in-memory fallback", exc)
    return MEMBER_DATA.get(member_id)


async def _fetch_claims_history(policy_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_claims_history
            async with get_db_session() as session:
                return await get_claims_history(session, policy_id, member_id)
        except Exception as exc:
            _log.warning("DB claims history fetch failed (%s) — using in-memory fallback", exc)
    key = f"{policy_id}_{member_id}"
    if key in CLAIMS_HISTORY_DATA:
        return CLAIMS_HISTORY_DATA[key]
    prefix = f"{policy_id}_"
    for k, v in CLAIMS_HISTORY_DATA.items():
        if k.startswith(prefix):
            return v
    return None


async def _fetch_porting(policy_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_porting
            async with get_db_session() as session:
                return await get_porting(session, policy_id)
        except Exception as exc:
            _log.warning("DB porting fetch failed (%s) — using in-memory fallback", exc)
    return PORTING_DATA.get(policy_id)


async def _fetch_network_provider(provider_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_network_provider
            async with get_db_session() as session:
                return await get_network_provider(session, provider_id)
        except Exception as exc:
            _log.warning("DB network provider fetch failed (%s) — using in-memory fallback", exc)
    return NETWORK_DATA.get(provider_id)


async def _fetch_balances(policy_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_benefit_balance
            async with get_db_session() as session:
                return await get_benefit_balance(session, policy_id, member_id)
        except Exception as exc:
            _log.warning("DB balances fetch failed (%s) — using in-memory fallback", exc)
    return BALANCES_DATA.get(f"{policy_id}_{member_id}")


async def _fetch_lifetime_state(policy_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_lifetime_state
            async with get_db_session() as session:
                return await get_lifetime_state(session, policy_id)
        except Exception as exc:
            _log.warning("DB lifetime state fetch failed (%s) — using in-memory fallback", exc)
    return LIFETIME_STATE_DATA.get(policy_id)


async def _fetch_endorsements(policy_id: str) -> Optional[Dict[str, Any]]:
    if _db_enabled():
        try:
            from db.engine import get_db_session
            from db.repositories import get_endorsements
            async with get_db_session() as session:
                return await get_endorsements(session, policy_id)
        except Exception as exc:
            _log.warning("DB endorsements fetch failed (%s) — using in-memory fallback", exc)
    return ENDORSEMENT_DATA.get(policy_id)


# ---------------------------------------------------------------------------
# Legacy GET endpoints (used by porting, network, balances, lifetime-state)
# ---------------------------------------------------------------------------

@router.get("/policy/{policy_id}", response_model=PolicyApiResponse)
async def get_policy(policy_id: str):
    data = await _fetch_policy(policy_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return PolicyApiResponse.model_validate(data)


@router.get("/member/{member_id}", response_model=MemberApiResponse)
async def get_member(member_id: str):
    data = await _fetch_member(member_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return MemberApiResponse.model_validate(data)


@router.get("/claims-history/{policy_id}/{member_id}", response_model=ClaimsHistoryApiResponse)
async def get_claims_history(policy_id: str, member_id: str):
    data = await _fetch_claims_history(policy_id, member_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return ClaimsHistoryApiResponse.model_validate(data)


@router.get("/porting/{policy_id}", response_model=PortingApiResponse)
async def get_porting(policy_id: str):
    data = await _fetch_porting(policy_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return PortingApiResponse.model_validate(data)


@router.get("/network-provider/{provider_id}", response_model=NetworkApiResponse)
async def get_network_provider(provider_id: str):
    data = await _fetch_network_provider(provider_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return NetworkApiResponse.model_validate(data)


@router.get("/balances/{policy_id}/{member_id}", response_model=BenefitBalanceApiResponse)
async def get_balances(policy_id: str, member_id: str):
    data = await _fetch_balances(policy_id, member_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return BenefitBalanceApiResponse.model_validate(data)


@router.get("/lifetime-state/{policy_id}", response_model=LifetimeStateApiResponse)
async def get_lifetime_state(policy_id: str):
    data = await _fetch_lifetime_state(policy_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return LifetimeStateApiResponse.model_validate(data)


@router.get("/endorsements/{policy_id}", response_model=EndorsementApiResponse)
async def get_endorsements(policy_id: str):
    data = await _fetch_endorsements(policy_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return EndorsementApiResponse.model_validate(data)


# ---------------------------------------------------------------------------
# CaseAPI POST endpoints (matching Postman collection)
# ---------------------------------------------------------------------------

@router.post("/caseapi/api/auth/getauthtoken", response_model=AuthTokenResponse)
async def get_auth_token(req: GetAuthTokenRequest):
    if not req.UserID or not req.Client_id:
        raise HTTPException(status_code=400, detail="Invalid credential payload")
    return AuthTokenResponse(access_token="mocked_caseapi_bearer_token_123456789")


@router.post("/caseapi/api/policy/getcustomerpolicydetail", response_model=PolicyApiResponse)
async def get_customer_policy_detail(req: GetPolicyDetailsRequest):
    data = await _fetch_policy(req.PolicyNumber)
    if data is None:
        raise HTTPException(status_code=404, detail="Policy not found in Enterprise Ledger")
    return PolicyApiResponse.model_validate(data)


@router.post("/caseapi/api/policy/getpolicydetailsbymoblienumber", response_model=PolicyApiResponse)
async def get_policy_details_by_mobile(req: GetPolicyDetailsByMobileRequest):
    data = await _fetch_policy(req.PolicyNumber)
    if data is None:
        raise HTTPException(status_code=404, detail="Policy not found in Enterprise Ledger")
    return PolicyApiResponse.model_validate(data)


@router.post("/caseapi/api/claim/getclaimhistory", response_model=ClaimsHistoryApiResponse)
async def get_claim_history_post(req: GetClaimHistoryRequest):
    data = await _fetch_claims_history(req.PolicyNo_COI, req.Membership_No_ID)
    if data is None:
        raise HTTPException(status_code=404, detail="Claims history not found for policy")
    return ClaimsHistoryApiResponse.model_validate(data)


@router.post("/caseapi/api/policy/getpolicydata", response_model=EndorsementApiResponse)
async def get_policy_data_post(req: GetPolicyDataRequest):
    data = await _fetch_endorsements(req.PolicyNo_COI)
    if data is None:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return EndorsementApiResponse.model_validate(data)
