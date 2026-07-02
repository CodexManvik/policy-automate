"""
FastAPI Microservice Layer for Claims 2.0 Auto-Adjudication Engine

Production-grade implementation:
  - Lifespan context manager for graceful startup/shutdown
  - Structured JSON logging with X-Request-ID tracing
  - Centralized pydantic-settings configuration
  - Async adjudication handler (no event-loop blocking)
  - Restricted CORS from settings
  - /health and /readiness endpoints
"""

from __future__ import annotations

import sys
import uuid
import logging
import json
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent))

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, Security, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import ValidationError
import secrets

from config import settings
from schemas import (
    ClaimContext, ClaimDecision, DecisionSummaryPayload, ClaimSummaryResponse,
    DecisionTrace, DocumentExtractionResult, DocumentExtractionLLMPayload,
)
from agent_reasoning import AgentReasoningLogger


# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id  # type: ignore[attr-defined]
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)


_configure_logging()
logger = logging.getLogger("claims_adjudication_api")


# ---------------------------------------------------------------------------
# Pipeline singleton — initialized inside lifespan, not at import time
# ---------------------------------------------------------------------------

_pipeline: "ClaimsAdjudicationPipeline | None" = None  # noqa: F821


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """
    Startup: initialize the adjudication pipeline (loads rules from disk, warms LLM provider).
    Shutdown: log clean exit.
    """
    global _pipeline

    from pipeline import ClaimsAdjudicationPipeline

    logger.info(
        "Initializing ClaimsAdjudicationPipeline",
        extra={"llm_provider": settings.llm_provider, "llm_url": settings.llm_url},
    )
    try:
        _pipeline = ClaimsAdjudicationPipeline(
            use_ai=True,
            confidence_threshold=settings.auto_approve_threshold,
            assisted_review_threshold=settings.assisted_review_threshold,
            llm_provider=settings.llm_provider,
            local_llm_url=settings.llm_url,
        )
        logger.info("Pipeline initialized successfully — ready to adjudicate")
    except Exception as exc:
        logger.critical("Pipeline initialization failed: %s", exc, exc_info=True)
        # Re-raise so uvicorn exits with a non-zero code instead of binding
        # a socket that will serve 500s on every request.
        raise RuntimeError("Startup failed: pipeline could not be initialized") from exc

    yield

    logger.info("ClaimsAdjudicationPipeline shutting down")
    _pipeline = None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Claims 2.0 Auto-Adjudication Engine",
    description=(
        "State-of-the-art graph-based health insurance claims auto-adjudication service "
        "implementing the ReAssure 3.0 product rules."
    ),
    version=settings.app_version,
    lifespan=_lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Middleware — CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

from integration.mock_routers import router as mock_router
app.include_router(mock_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error("Request validation failed: %s", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body": exc.body},
    )

# ---------------------------------------------------------------------------
# Middleware — X-Request-ID tracing
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000.0
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Processing-Time-Ms"] = f"{duration_ms:.2f}"
    logger.debug(
        "Request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        },
    )
    return response


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    tags=["Observability"],
)
async def health_check():
    """Returns service liveness. Does not check downstream dependencies."""
    return {
        "status": "ok",
        "service": "claims-adjudication-engine",
        "version": settings.app_version,
        "llm_provider": settings.llm_provider,
        "pipeline_ready": _pipeline is not None,
    }


@app.get(
    "/readiness",
    status_code=status.HTTP_200_OK,
    summary="Readiness probe",
    tags=["Observability"],
)
async def readiness_check():
    """Returns 200 only if the pipeline is fully initialized and ready to serve."""
    if _pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pipeline not yet initialized — service is starting up",
        )
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# API Key authentication dependency
# ---------------------------------------------------------------------------

_api_key_scheme = APIKeyHeader(
    name=settings.api_key_header_name,
    auto_error=False,   # We raise ourselves for a controlled error message
    description="Shared API key. Required when ADJUDICATION_API_KEY is configured.",
)


async def verify_api_key(api_key: str | None = Security(_api_key_scheme)) -> None:
    """
    FastAPI dependency: validates the incoming API key against the configured secret.

    Behaviour:
      - If settings.adjudication_api_key is None, authentication is disabled and the
        request passes through unconditionally.  This is safe only in local dev.
      - If the header is missing or the key does not match, returns 401.
      - Uses secrets.compare_digest for constant-time comparison to prevent
        timing-based side-channel attacks.
    """
    configured_key = settings.adjudication_api_key
    if configured_key is None:
        # Authentication not configured — allow all requests (dev mode only).
        return

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key. Supply it in the '{settings.api_key_header_name}' header.",
            headers={"WWW-Authenticate": f"APIKey header={settings.api_key_header_name}"},
        )

    # Constant-time comparison prevents timing oracle attacks.
    if not secrets.compare_digest(api_key.encode(), configured_key.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": f"APIKey header={settings.api_key_header_name}"},
        )


# ---------------------------------------------------------------------------
# Adjudication endpoints — v1 and v2 share the same handler
# ---------------------------------------------------------------------------

async def _adjudicate_handler(context: ClaimContext) -> ClaimDecision:
    """
    Core handler shared by v1 and v2 endpoints.

    Uses the async adjudication path so that blocking LLM/tool calls are
    dispatched to a thread pool and do not stall the uvicorn event loop.
    """
    if _pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Adjudication pipeline is not available — service may be starting up",
        )

    logger.info("Adjudicating claim: %s", context.claim_id)

    # Persist the full ClaimContext to a dedicated per-claim log file before adjudication.
    # Non-fatal: any write error is caught inside log_claim_context and only logged as WARNING.
    try:
        AgentReasoningLogger.log_claim_context(
            context.claim_id,
            context.model_dump(mode="json")
        )
    except Exception:
        pass  # Logging failures must never block adjudication

    try:
        decision: ClaimDecision = await _pipeline.adjudicate_claim_async(context)
        logger.info(
            "Claim adjudicated",
            extra={
                "claim_id": context.claim_id,
                "decision": decision.claim_decision,
                "confidence": decision.confidence_score,
            },
        )
        return decision

    except ValidationError as exc:
        logger.warning("Schema validation failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Claim payload schema validation failed: {exc}",
        ) from exc

    except ValueError as exc:
        logger.warning("Value error during adjudication of %s: %s", context.claim_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adjudication parameter violation: {exc}",
        ) from exc

    except Exception as exc:
        # Do NOT expose internal exception text in the response body.
        logger.critical(
            "Unhandled pipeline exception for claim %s",
            context.claim_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal adjudication engine error — contact support with request ID.",
        ) from exc


@app.post(
    "/api/v1/adjudicate",
    response_model=ClaimDecision,
    status_code=status.HTTP_200_OK,
    summary="Adjudicate Claim Context (v1 — deprecated alias)",
    description="Processes the claim context payload through the 7-gate dynamic execution graph.",
    tags=["Adjudication"],
)
async def adjudicate_v1(
    context: ClaimContext,
    _: None = Depends(verify_api_key),
) -> ClaimDecision:
    return await _adjudicate_handler(context)


@app.post(
    "/api/v2/adjudicate",
    response_model=ClaimDecision,
    status_code=status.HTTP_200_OK,
    summary="Adjudicate Claim Context (v2)",
    description="Processes the claim context payload through the 7-gate dynamic execution graph.",
    tags=["Adjudication"],
)
async def adjudicate_v2(
    context: ClaimContext,
    _: None = Depends(verify_api_key),
) -> ClaimDecision:
    return await _adjudicate_handler(context)


@app.post(
    "/api/v2/adjudicate/stream",
    status_code=status.HTTP_200_OK,
    summary="Adjudicate Claim Context with live streaming updates",
    description="Streams each decision trace in real-time as it executes, followed by the final decision.",
    tags=["Adjudication"],
)
async def adjudicate_stream(
    context: ClaimContext,
    _: None = Depends(verify_api_key),
) -> StreamingResponse:
    if _pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Adjudication pipeline is not available — service may be starting up",
        )

    logger.info("Streaming adjudication for claim: %s", context.claim_id)

    try:
        AgentReasoningLogger.log_claim_context(
            context.claim_id,
            context.model_dump(mode="json")
        )
    except Exception:
        pass

    async def event_generator():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_trace(trace: DecisionTrace):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "trace", "data": trace.model_dump()})

        async def run_adjudication():
            try:
                decision = await _pipeline.adjudicate_claim_async(context, on_trace=on_trace)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "decision", "data": decision.model_dump()})
            except Exception as e:
                logger.error("Error in streaming adjudication: %s", e, exc_info=True)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "data": str(e)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        adjudication_task = asyncio.create_task(run_adjudication())

        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, default=str)}\n\n"

        await adjudication_task

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post(
    "/api/v2/extract-document",
    response_model=DocumentExtractionResult,
    status_code=status.HTTP_200_OK,
    summary="Extract structured fields from a clinical document",
    description=(
        "Accepts a PDF or image upload (discharge summary, hospital bill, etc.). "
        "Extracts raw text via pypdf or pytesseract, then sends it to the configured "
        "LLM to parse structured claim fields. Returns a DocumentExtractionResult "
        "suitable for auto-filling the claim form."
    ),
    tags=["Documents"],
)
async def extract_document(
    file: UploadFile = File(..., description="PDF or image file to extract data from."),
    _: None = Depends(verify_api_key),
) -> DocumentExtractionResult:
    if _pipeline is None or _pipeline.semantic_agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pipeline or semantic agent is not available.",
        )

    from document_extractor import route_extraction, build_extraction_prompt

    data = await file.read()
    filename = file.filename or "upload"

    logger.info("Document extraction request: %s (%d bytes)", filename, len(data))

    try:
        raw_text = route_extraction(filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if not raw_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract any text from the uploaded document. Ensure it is a readable PDF or clear image.",
        )

    system_prompt, user_prompt = build_extraction_prompt(raw_text)

    try:
        loop = asyncio.get_running_loop()
        raw_llm_response = await loop.run_in_executor(
            None,
            _pipeline.semantic_agent._call_llm,
            system_prompt,
            user_prompt,
            DocumentExtractionLLMPayload,
        )
        extracted = DocumentExtractionLLMPayload.model_validate_json(raw_llm_response)
    except Exception as exc:
        logger.error("LLM extraction failed for %s: %s", filename, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM extraction failed: {exc}",
        ) from exc

    logger.info(
        "Document extracted: %s | raw_chars=%d | diagnosis=%s",
        filename, len(raw_text), extracted.condition_diagnosed,
    )
    return DocumentExtractionResult(
        filename=filename,
        raw_text_length=len(raw_text),
        extracted=extracted,
    )


@app.post(
    "/api/v2/adjudicate/summary",
    response_model=ClaimSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate AI Adjudication Summary",
    description="Asynchronously generates a natural language summary of a claim rejection or partial approval.",
    tags=["Adjudication"],
)
async def generate_claim_summary(
    decision: ClaimDecision,
    _: None = Depends(verify_api_key),
) -> ClaimSummaryResponse:
    if _pipeline is None or _pipeline.semantic_agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic agent is not initialized or available."
        )

    # Format the decision details for the LLM
    traces_summary = []
    for t in decision.decision_trace:
        if t.evaluation in ("FAILED", "EXCLUSION_ACTIVE", "ASSISTED_REVIEW", "PENDING_REVIEW"):
            traces_summary.append(f"- Rule {t.rule_id} ({t.rule_name}) in {t.gate}: {t.reason}")

    traces_str = "\n".join(traces_summary) if traces_summary else "None"

    user_prompt = f"""
    Analyze the following health insurance auto-adjudication decision:
    - Claim ID: {decision.claim_id}
    - Final Decision: {decision.claim_decision}
    - Total Claimed: {decision.total_claimed:.2f} INR
    - Total Admissible: {decision.total_admissible:.2f} INR
    - Total Payable: {decision.total_payable:.2f} INR
    - Total Deductions: {decision.total_deductions:.2f} INR
    - Room Rent Pro-Rata Deduction: {decision.deduction_breakdown.room_pro_rata:.2f} INR
    - Co-payment: {decision.deduction_breakdown.co_payment:.2f} INR
    - Deductible: {decision.deduction_breakdown.deductible:.2f} INR
    - Non-Payable Items Excluded: {decision.deduction_breakdown.non_payable_items:.2f} INR

    Triggered Exclusion / Waiting Period Rules and Errors:
    {traces_str}

    Please explain:
    1. The core reason(s) for the decision (why it was rejected or partially approved).
    2. The exact financial breakdown (what was deducted and why).
    3. What the final payable amount is and if any personal limits were exceeded or riders applied.
    """

    system_prompt = (
        "You are an expert health insurance claims auto-adjudication advisor.\n"
        "Your task is to analyze the decision and financial breakdown of a claim, and write a detailed, professional, human-friendly summary of the outcome.\n"
        "The summary should be structured in markdown with bullet points, explaining exactly why deductions occurred or why the claim was rejected.\n"
        "Do NOT write any preamble, conversational greeting, or signature. Start directly with the explanation.\n"
        "RESPONSE FORMAT CONTRACT:\n"
        "The response MUST contain a single raw JSON object matching this schema:\n"
        '{{"summary": "your detailed markdown-formatted summary here"}}'
    )

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        raw_response = await loop.run_in_executor(
            None,
            _pipeline.semantic_agent._call_llm,
            system_prompt,
            user_prompt,
            DecisionSummaryPayload
        )
        
        parsed = DecisionSummaryPayload.model_validate_json(raw_response)
        return ClaimSummaryResponse(summary=parsed.summary)
    except Exception as exc:
        logger.error("Failed to generate AI summary: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate summary: {str(exc)}"
        )
