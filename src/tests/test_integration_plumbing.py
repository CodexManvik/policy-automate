"""
Integration smoke tests for context builder and mock gateways.
"""

import socket
import threading
import time
import asyncio
import pytest
import uvicorn
from fastapi import FastAPI
from integration.mock_routers import router as mock_router
from integration.context_builder import assemble_claim_context
from pipeline import ClaimsAdjudicationPipeline
from schemas import ClaimDecision


def find_free_port() -> int:
    """Helper to locate a free port dynamically for background server hosting"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def local_server_url() -> str:
    """Fixture to launch a background uvicorn server serving the mock routers"""
    app = FastAPI()
    app.include_router(mock_router)
    
    port = find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    
    # Wait briefly for server startup
    time.sleep(0.5)
    
    yield f"http://127.0.0.1:{port}"
    
    server.should_exit = True
    thread.join(timeout=2.0)


@pytest.mark.anyio
async def test_full_integration_pipeline_smoke(local_server_url):
    """
    Smoke test to:
    1. Initialize the mock server.
    2. Invoke assemble_claim_context to pull parallel lookup payloads.
    3. Pass context into pipeline for adjudication.
    4. Assert output matches ClaimDecision payload and trace is intact.
    """
    # Raw line item representation from hospital billing machine
    raw_line_items = [
        {
            "line_item_id": "LI-9921A",
            "description": "Standard Appendectomy procedure",
            "claimed_amount": 100000.0,
            "expense_date": "2025-06-15T10:00:00Z",
            "benefit_bucket": "Expenses during Hospitalization",
            "admission_date": "2025-06-15T08:00:00Z",
            "discharge_date": "2025-06-17T12:00:00Z",
            "hospitalization_hours": 52.0,
            "actual_room_rent": 6000.0,
            "room_category_claimed": "Single Private Room",
            "treatment_type": "Allopathic",
            "condition_diagnosed": "Appendicitis",
            "accident_related": False,
            "emergency": True,
            "room_charges": 12000.0,
            "nursing_charges": 4000.0,
            "medical_practitioner_fees": 35000.0,
            "ot_charges": 25000.0
        }
    ]

    # Assemble claim context using HTTP lookups against mock routing server
    context = await assemble_claim_context(
        claim_id="CLM-10023",
        policy_id="POL-1001",
        member_id="MEM-9921",
        provider_id="PROV-551",
        raw_line_items=raw_line_items,
        base_url=local_server_url
    )

    # Validate mapped values in ClaimContext
    assert context.claim_id == "CLM-10023"
    assert context.policy.policy_id == "POL-1001"
    assert context.policy.variant == "Classic"
    assert context.member.member_id == "MEM-9921"
    assert context.network.provider_id == "PROV-551"
    assert len(context.line_items) == 1
    assert len(context.endorsements) == 1
    assert context.endorsements[0].endorsement_id == "END-882"

    # Forward context into Adjudication Pipeline
    pipeline = ClaimsAdjudicationPipeline(llm_provider="local")
    decision = pipeline.adjudicate_claim(context)

    # Capture and verify decision trace outputs
    assert isinstance(decision, ClaimDecision)
    assert decision.claim_id == "CLM-10023"
    assert decision.total_claimed == 100000.0
    assert len(decision.decision_trace) > 0
    assert decision.claim_decision in ("APPROVED", "PARTIALLY_APPROVED", "REJECTED", "ASSISTED_REVIEW", "PENDING_REVIEW")
