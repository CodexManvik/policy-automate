# ReAssure 3.0 Claims Auto-Adjudication Engine

A production-grade health insurance claims auto-adjudication system implementing the ReAssure 3.0 (R3) product rule set. The engine combines a deterministic 7-gate execution pipeline with a locally-deployed LLM for clinical semantic reasoning, exposing a real-time streaming API consumed by a React/TypeScript web dashboard.

---

## Architecture Overview

The system is composed of five independently runnable processes:

```
Developer Machine
├── PostgreSQL 16         docker-compose up -d           (port 5432)
├── pgAdmin 4             docker-compose up -d           (port 5050)
├── llama.cpp server      ./server -m model.gguf         (port 8080)
├── FastAPI backend       uvicorn src.main:app --reload  (port 8000)
└── Vite dev server       pnpm run dev                   (port 5173)
```

### Core Subsystems

**1. AI Planner (`src/planner.py`)**
Before executing any gate, the `AIPlanner` loads all applicable `RuleBlueprint` objects from `ProductMemoryStore` filtered by policy variant (Classic / Select / Elite) and constructs a Directed Acyclic Graph (DAG) using `depends_on` rule metadata. Kahn's algorithm produces a topologically sorted `ExecutionPlan`. The plan is reproducible and logged for audit replay.

**2. 7-Gate Adjudication Pipeline (`src/pipeline.py`, `src/pipeline_modules/`)**
Every claim traverses an ordered gate sequence. Any gate returning `FAILED` or `EXCLUSION_ACTIVE` terminates the pipeline immediately with a populated audit trace.

```
GATE 1  Policy Validation         Deterministic
  GATE_1_POLICY_STATUS            Status=Active, premium paid, grace period
  GATE_1_DATE_RANGE               Claim date within policy start/end

GATE 2  Member Validation         Deterministic
  GATE_2_ELIGIBILITY              Member eligibility active
  GATE_2_ADDITION_DATE            Admission >= member date of addition

GATE 3  Coverage Validation       Hybrid (deterministic + semantic)
  GATE_3_VARIANT_FILTER           Variant eligibility for benefit bucket
  GATE_3_RIDER_OPT_IN             Rider opt-in verification
  R3_BEN_003_DURATION             Minimum 2h stay (24h for alt treatments)
  R3_BEN_007_PRECON               Domiciliary 3-criteria check
  Semantic: LLM coverage assess.  Clinical necessity validation

GATE 4  Waiting Period            Hybrid
  GATE_4_ACCIDENT_EXEMPT          Accident waives all waiting periods
  GATE_4_INITIAL_WAIT             30-day initial waiting period
  GATE_4_SPECIFIED_DISEASE        24-month specified disease exclusion list
  GATE_4_PED_WAIT                 36-month PED waiting period
  GATE_4_PORTABILITY              Porting credit deduction

GATE 5  Exclusion Validation      Semantic (LLM-driven)
  GATE_5_DETERMINISTIC_EXCL       Hard-coded exclusion list filter
  GATE_5_SEMANTIC_AGENT           LLM analyzes discharge_summary +
                                  condition_diagnosed for cosmetic,
                                  diagnostic-only, or OPD exclusions

GATE 6  Financial Computation     Deterministic (Tools 1-5)
  Tool 2: Room pro-rata           Proportional deduction across all bills
  Tool 3: Co-payment              Policy + network + age-based co-pay
  Tool 4: Deductible              Annual aggregate deductible tracking
  Tool 5: SI waterfall            Base SI -> Booster+ -> ReAssure Forever

GATE 7  State Update              Deterministic (Tools 6-8)
  Tool 6: Lock the Clock          Premium age-lock for eligible members
  Tool 7: Booster accumulation    Booster+ pool update
  Tool 8a: Hospital Daily Cash    HDC benefit computation
  Tool 8b: Personal Accident      PA payout based on injury classification
```

**3. Semantic Execution Agent (`src/semantic_agent.py`)**
A singleton initialized at startup supporting three runtime-selectable LLM backends:

- `local` (default): Connects to a locally-deployed `llama.cpp` server running Gemma-4 E4B QAT. Uses a dual-path strategy — `/completion` with Gemma reasoning chat template (chain-of-thought enabled), falling back to `/v1/chat/completions`. GBNF grammar enforced when `REASONING_ON=false` to guarantee valid JSON output.
- `openai`: Routes to `gpt-4o-mini` via the OpenAI Python SDK with `temperature=0.0` and `response_format=json_object`.
- Test suites only: LLM calls are intercepted by `conftest.py` fixtures; no live LLM required to run the test suite.

All LLM calls are dispatched via `asyncio.run_in_executor` (thread pool). The event loop is never blocked.

**4. FastAPI Backend (`src/main.py`)**
Async FastAPI layer with structured JSON logging, `X-Request-ID` tracing, CORS restriction, and optional API key authentication. The pipeline singleton is initialized inside a lifespan context manager; startup fails hard if the configured LLM is unreachable.

**5. React/TypeScript Frontend (`frontend/`)**
Single-page application served by Vite. Submits `ClaimContext` payloads to the streaming adjudication endpoint and renders each `DecisionTrace` event in real-time via Server-Sent Events as gates complete. Includes multi-file document upload with OCR-backed LLM field extraction.

---

## Confidence-Based Routing (Four-Tier)

Every LLM call returns a `confidence_score` (0.0–1.0). The system maps this to one of four routing outcomes, all configurable at runtime via `.env`:

| Confidence Range | Decision                                       | Queue                         |
| ---------------- | ---------------------------------------------- | ----------------------------- |
| `>= 0.90`        | `APPROVED` / `PARTIALLY_APPROVED` / `REJECTED` | Fully automated               |
| `[0.70, 0.90)`   | `ASSISTED_REVIEW`                              | Operations review queue       |
| `[0.50, 0.70)`   | `MEDICAL_REVIEW`                               | Clinical expert queue         |
| `< 0.50`         | `PENDING_REVIEW`                               | Senior auditor queue          |

---

## Prerequisites

| Dependency        | Version  | Purpose                                  |
| ----------------- | -------- | ---------------------------------------- |
| Python            | 3.11+    | Backend runtime                          |
| Node.js           | 20+      | Frontend build toolchain                 |
| pnpm              | 8+       | Frontend package manager                 |
| Docker + Compose  | any      | PostgreSQL 16 + pgAdmin                  |
| llama.cpp server  | current  | Local LLM inference (Gemma-4 E4B QAT)   |
| Tesseract OCR     | 5+       | Scanned PDF/image extraction (optional)  |

Tesseract is only required for OCR on image-based PDFs. Native-text PDFs work without it.

Windows install: `winget install UB-Mannheim.TesseractOCR`

---

## Environment Setup

Copy `.env.example` to `.env` and configure:

```bash
# LLM backend: local | openai | mock (mock for test suites only)
LLM_PROVIDER=local
LLM_URL=http://127.0.0.1:8080

# Required only when LLM_PROVIDER=openai
OPENAI_API_KEY=sk-proj-...

# Enable chain-of-thought reasoning in local LLM (Gemma-4)
REASONING_ON=true

# Four-tier confidence routing thresholds
AUTO_APPROVE_THRESHOLD=0.90
ASSISTED_REVIEW_THRESHOLD=0.70
MEDICAL_REVIEW_THRESHOLD=0.50

# CORS — restrict to frontend origin
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# PostgreSQL (must match docker-compose.yml)
DATABASE_URL=postgresql+asyncpg://nivabupa:nivabupa_dev_secret@localhost:5432/adjudication
POSTGRES_USER=nivabupa
POSTGRES_PASSWORD=nivabupa_dev_secret
POSTGRES_DB=adjudication

# Optional: enable endpoint authentication (omit in development)
ADJUDICATION_API_KEY=

# Observability
TELEMETRY_FILE=logs/metrics_telemetry.jsonl
LOG_LEVEL=INFO
```

---

## Installation

**1. Start the PostgreSQL database:**

```bash
docker-compose up -d
```

pgAdmin is available at `http://localhost:5050` (credentials from `.env`).

**2. Install Python dependencies:**

```bash
pip install -r requirements.txt
```

Or with `uv`:

```bash
uv pip install -r requirements.txt
```

**3. Run database migrations:**

```bash
alembic upgrade head
```

**4. Install frontend dependencies:**

```bash
cd frontend
pnpm install
```

---

## Running

**1. Start the local LLM server** (required for `LLM_PROVIDER=local`):

```bash
./llama-server -m gemma-4-e4b-qat.gguf --port 8080
```

**2. Start the FastAPI backend:**

```bash
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

The backend performs a startup probe against the configured LLM URL. It will refuse to bind if the LLM is unreachable.

**3. Start the frontend development server:**

```bash
cd frontend
pnpm run dev
```

Frontend is served at `http://localhost:5173`.

---

## API Documentation

Interactive documentation is auto-generated by FastAPI:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

### Endpoint Reference

**Observability**

| Method | Path         | Auth | Description                                                        |
| ------ | ------------ | ---- | ------------------------------------------------------------------ |
| `GET`  | `/health`    | None | Liveness probe. Returns version, LLM provider, pipeline readiness. |
| `GET`  | `/readiness` | None | Returns 503 until pipeline is fully initialized.                   |

**Adjudication**

| Method | Path                         | Auth             | Description                                                                                   |
| ------ | ---------------------------- | ---------------- | --------------------------------------------------------------------------------------------- |
| `POST` | `/api/v2/adjudicate`         | Optional API Key | Synchronous adjudication. Returns `ClaimDecision` with full audit trace and deduction detail. |
| `POST` | `/api/v2/adjudicate/stream`  | Optional API Key | Server-Sent Events. Emits `DecisionTrace` per gate in real-time, then final `ClaimDecision`.  |
| `POST` | `/api/v2/adjudicate/summary` | Optional API Key | Accepts a `ClaimDecision`. Returns LLM-generated markdown explanation for non-approval cases. |
| `POST` | `/api/v1/adjudicate`         | Optional API Key | Deprecated alias for v2. Identical behaviour — retained for backward compatibility.           |

**Document Processing**

| Method | Path                       | Auth             | Description                                                                          |
| ------ | -------------------------- | ---------------- | ------------------------------------------------------------------------------------ |
| `POST` | `/api/v2/extract-document` | Optional API Key | `multipart/form-data`. Accepts PDF or image. Returns `DocumentExtractionResult` with structured clinical fields parsed by the LLM. |

---

## Running Tests

Tests run without a live LLM — all external calls are intercepted by the `conftest.py` autouse fixture.

```bash
$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest src/tests/ -x -q --tb=short
```

Current coverage: 67 tests, 0 failures.

| Test File                      | Scope                                                                   |
| ------------------------------ | ----------------------------------------------------------------------- |
| `test_phase2_integration.py`   | End-to-end pipeline, DAG construction, confidence routing tiers         |
| `test_rule_scenarios.py`       | Gate-level rule scenarios, MEDICAL_REVIEW routing, financial calculators |
| `test_new_features.py`         | AI summary endpoint, document extraction schema, LLM cache              |
| `test_hardening.py`            | Edge cases, error handling, malformed inputs                            |
| `test_calculators.py`          | All deterministic calculator functions (Tools 1–8)                      |
| `test_metrics.py`              | Telemetry engine, concordance simulation, JSONL output                  |
| `test_integration_plumbing.py` | HTTP endpoint integration via TestClient, SSE stream plumbing           |
| `test_property_invariants.py`  | Property-based invariant checks on financial outputs                    |

---

## Technology Stack

| Component               | Technology             | Version        |
| ----------------------- | ---------------------- | -------------- |
| Backend framework       | FastAPI                | >= 0.111       |
| Language                | Python                 | 3.11+          |
| Data validation         | Pydantic v2            | >= 2.5         |
| Async ORM               | SQLAlchemy             | >= 2.0.30      |
| Async DB driver         | asyncpg                | >= 0.29        |
| Schema migrations       | Alembic                | >= 1.13        |
| LLM inference (local)   | llama.cpp              | current        |
| LLM SDK (cloud option)  | openai                 | >= 1.30        |
| PDF text extraction     | pypdf                  | >= 4.2         |
| Image OCR               | pytesseract + Pillow   | >= 0.3.13 / >= 10.3 |
| File upload parsing     | python-multipart       | >= 0.0.9       |
| Frontend framework      | React + TypeScript     | 18 / 5         |
| Frontend build tool     | Vite                   | 8              |
| Frontend package mgr    | pnpm                   | 8+             |
| Database                | PostgreSQL             | 16             |
| Container runtime       | Docker Compose         | 3.9            |

---

## Project Structure

```
policy automate/
├── src/
│   ├── main.py                    FastAPI application, lifespan, all routes
│   ├── pipeline.py                ClaimsAdjudicationPipeline entry point
│   ├── planner.py                 AI Planner: DAG construction, topo sort
│   ├── schemas.py                 Pydantic v2 models (ClaimContext, ClaimDecision, etc.)
│   ├── semantic_agent.py          SemanticExecutionAgent: LLM call routing
│   ├── calculators.py             Deterministic financial tools (Tools 1–8)
│   ├── product_memory.py          ProductMemoryStore: rule blueprint loader
│   ├── document_extractor.py      OCR + LLM document field extraction
│   ├── metrics.py                 PipelineMetricsEngine: telemetry + concordance
│   ├── agent_reasoning.py         AgentReasoningLogger: per-call LLM audit logs
│   ├── config.py                  pydantic-settings configuration
│   ├── pipeline_modules/
│   │   ├── execution.py           Gate execution engine, confidence routing
│   │   ├── helpers.py             Decision builders, MEDICAL/ASSISTED/PENDING helpers
│   │   ├── graph_exporter.py      Execution graph HTML export
│   │   └── calculators_integration.py  Tool-to-gate wiring
│   ├── integration/
│   │   ├── context_builder.py     Assembles ClaimContext from 8 external gateways
│   │   └── mock_routers.py        Local HTTP simulation of all external APIs
│   ├── db/                        SQLAlchemy ORM models, async session factory
│   └── tests/                     pytest test suite (67 tests)
├── frontend/
│   ├── src/
│   │   ├── App.tsx                Single-page application
│   │   ├── hooks/
│   │   │   ├── useClaimContext.ts ClaimContext state assembly and mutations
│   │   │   └── useAdjudication.ts SSE streaming adjudication state
│   │   ├── services/
│   │   │   └── adjudicationApi.ts Typed HTTP service layer (all API calls)
│   │   ├── data/
│   │   │   └── presets.ts         10 pre-built demo claim scenarios
│   │   └── types/
│   │       └── claims.ts          TypeScript types mirroring backend schemas
│   └── vite.config.ts
├── docs/
│   └── Product and Policy Rules Extraction.json  R3 rule set (version R3_v2.1)
├── alembic/                       Database migration scripts
├── graphs/                        Auto-generated adjudication graph HTML files
├── logs/                          Agent reasoning logs, claim context snapshots
├── metrics_telemetry.jsonl        Append-only adjudication telemetry
├── docker-compose.yml             PostgreSQL 16 + pgAdmin4
├── requirements.txt               Python dependencies
├── architecture.md                Full system architecture reference
└── .env                           Runtime configuration (not committed)
```

---

## Observability

**Agent Reasoning Log** (`logs/agent_reasoning.log`): Every LLM call is logged with the full system prompt, user prompt, raw response, structured output, confidence score, and token usage.

**Per-Claim Context Snapshots** (`logs/claims/<claim_id>_ctx_<ts>.json`): The full `ClaimContext` payload is written to disk before adjudication begins, enabling post-hoc audit replay.

**Telemetry** (`metrics_telemetry.jsonl`): Append-only JSONL file recording per-run decisions, per-gate latencies, token consumption, and PAS reconciliation concordance.

**HTTP Tracing**: Every response carries `X-Request-ID` (caller-supplied or auto-generated UUID) and `X-Processing-Time-Ms`.

**Structured Logging**: All backend logs are emitted as single-line JSON objects, compatible with Datadog, Splunk, and CloudWatch.

---

## Security

| Control                 | Implementation                                                                                           |
| ----------------------- | -------------------------------------------------------------------------------------------------------- |
| API Authentication      | `APIKeyHeader` with `secrets.compare_digest()` (constant-time, timing-attack resistant)                  |
| CORS Policy             | Restricted to `CORS_ORIGINS` from `.env`                                                                 |
| Secret Management       | All secrets loaded exclusively from `.env` via `pydantic-settings`. Nothing hardcoded.                  |
| Internal Error Masking  | Unhandled exceptions return a generic 500. Stack traces logged internally only.                          |
| Input Validation        | All request bodies validated by Pydantic v2. Invalid payloads return structured `422` with field detail. |
| DB Monetary Precision   | All INR columns are `NUMERIC(18,2)`. No floating-point drift on financial calculations.                   |
| Concurrent SI Writes    | Row-level locks on `benefit_balances` and `lifetime_states` to prevent concurrent SI overdraw.           |
