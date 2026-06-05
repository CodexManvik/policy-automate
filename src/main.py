"""
FastAPI Microservice Layer for Claims 2.0 Auto-Adjudication Engine
Exposes high-performance, strictly-typed endpoints for real-time adjudication
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import logging
from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError
from schemas import ClaimContext, ClaimDecision
from pipeline import ClaimsAdjudicationPipeline

# Initialize logging system
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("claims_adjudication_api")

app = FastAPI(
    title="Claims 2.0 Auto-Adjudication Engine",
    description="State-of-the-art graph-based health insurance claims auto-adjudication service.",
    version="2.0.0"
)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared pipeline instance
import os
pipeline = ClaimsAdjudicationPipeline(
    llm_provider=os.getenv("LLM_PROVIDER", "default"),
    local_llm_url=os.getenv("LLM_URL", "http://localhost:8080")
)

@app.post(
    "/api/v2/adjudicate",
    response_model=ClaimDecision,
    status_code=status.HTTP_200_OK,
    summary="Adjudicate Claim Context",
    description="Processes the claim context payload through dynamic graph-based gate execution."
)
async def process_claim(context: ClaimContext):
    try:
        logger.info(f"Processing claim adjudication request for Claim ID: {context.claim_id}")
        decision = pipeline.adjudicate_claim(context)
        logger.info(f"Successfully processed Claim ID: {context.claim_id} | Decision: {decision.claim_decision}")
        return decision
    except ValidationError as ve:
        logger.error(f"Pydantic Validation failure on Claim Adjudication payload: {ve}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Schema validation failed: {str(ve)}"
        )
    except ValueError as ve:
        logger.error(f"Value verification error during adjudication: {ve}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adjudication parameter violation: {str(ve)}"
        )
    except Exception as e:
        logger.critical(f"Unhandled system crash in ClaimsAdjudicationPipeline: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Adjudication pipeline fatal runtime exception: {str(e)}"
        )
