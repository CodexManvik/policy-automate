"""
FastAPI mock routers simulating core enterprise microservice API endpoints.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from integration.gateway_schemas import (
    PolicyApiResponse, MemberApiResponse, ClaimsHistoryApiResponse,
    PortingApiResponse, NetworkApiResponse, BenefitBalanceApiResponse,
    LifetimeStateApiResponse, EndorsementApiResponse, EndorsementItem
)

router = APIRouter(prefix="/api/external")

# Import seed databases from decoupled claim_data module
from integration.claim_data import (
    POLICY_DATA, MEMBER_DATA, CLAIMS_HISTORY_DATA, PORTING_DATA,
    NETWORK_DATA, BALANCES_DATA, LIFETIME_STATE_DATA, ENDORSEMENT_DATA
)


@router.get("/policy/{policy_id}", response_model=PolicyApiResponse)
async def get_policy(policy_id: str):
    if policy_id not in POLICY_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return PolicyApiResponse.model_validate(POLICY_DATA[policy_id])


@router.get("/member/{member_id}", response_model=MemberApiResponse)
async def get_member(member_id: str):
    if member_id not in MEMBER_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return MemberApiResponse.model_validate(MEMBER_DATA[member_id])


@router.get("/claims-history/{policy_id}/{member_id}", response_model=ClaimsHistoryApiResponse)
async def get_claims_history(policy_id: str, member_id: str):
    key = f"{policy_id}_{member_id}"
    if key not in CLAIMS_HISTORY_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return ClaimsHistoryApiResponse.model_validate(CLAIMS_HISTORY_DATA[key])


@router.get("/porting/{policy_id}", response_model=PortingApiResponse)
async def get_porting(policy_id: str):
    if policy_id not in PORTING_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return PortingApiResponse.model_validate(PORTING_DATA[policy_id])


@router.get("/network-provider/{provider_id}", response_model=NetworkApiResponse)
async def get_network_provider(provider_id: str):
    if provider_id not in NETWORK_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return NetworkApiResponse.model_validate(NETWORK_DATA[provider_id])


@router.get("/balances/{policy_id}/{member_id}", response_model=BenefitBalanceApiResponse)
async def get_balances(policy_id: str, member_id: str):
    key = f"{policy_id}_{member_id}"
    if key not in BALANCES_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return BenefitBalanceApiResponse.model_validate(BALANCES_DATA[key])


@router.get("/lifetime-state/{policy_id}", response_model=LifetimeStateApiResponse)
async def get_lifetime_state(policy_id: str):
    if policy_id not in LIFETIME_STATE_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return LifetimeStateApiResponse.model_validate(LIFETIME_STATE_DATA[policy_id])


@router.get("/endorsements/{policy_id}", response_model=EndorsementApiResponse)
async def get_endorsements(policy_id: str):
    if policy_id not in ENDORSEMENT_DATA:
        raise HTTPException(status_code=404, detail="Entity not found in Enterprise Ledger")
    return EndorsementApiResponse.model_validate(ENDORSEMENT_DATA[policy_id])
