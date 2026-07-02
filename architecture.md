# Claims Auto-Adjudication Engine — System Architecture

**Product:** ReAssure 3.0 (R3) Claims Auto-Adjudication Engine
**Team:** Nivabupa Policy Automation
**Version:** 2.0.0
**Document status:** Current as of 2026-07-02
**Scope:** Covers every integrated subsystem, data flow, API surface, security model, and operational configuration as deployed.

---

## 1. Executive Summary

This system automates the end-to-end adjudication of health insurance claims under the ReAssure 3.0 product, eliminating manual review for high-confidence decisions and routing borderline cases to human queues with complete audit trails. The architecture combines deterministic rule execution with a locally-deployed large language model (LLM) for clinical semantic reasoning. The entire pipeline runs within organizational infrastructure — no external AI API calls are mandatory for production operation.

**Key outcomes this architecture delivers:**

| Outcome | Mechanism |
|---|---|
| Full adjudication in under 10 seconds (typical) | Async FastAPI + thread-pooled LLM calls |
| Zero event-loop blocking under concurrent load | `asyncio.run_in_executor` for all LLM inference |
| Complete audit trail for every decision | `AgentReasoningLogger` + JSONL telemetry |
| Confidence-gated auto vs. manual routing | Four-tier threshold cascade |
| Clinical document data extraction | OCR + LLM extraction pipeline |
| Real-time decision streaming to UI | Server-Sent Events (SSE) on `/api/v2/adjudicate/stream` |

---

## 2. High-Level Architecture

```
+---------------------------------------------------------------------+
|                        OPERATOR BROWSER                             |
|                   React / Vite SPA (port 5173)                      |
|  +-------------+  +------------------+  +------------------------+ |
|  | Claim Form  |  |  Document Upload |  |  Live Adjudication     | |
|  | (8 context  |  |  Zone            |  |  Panel (SSE stream     | |
|  |  data tabs) |  |  multi-file,     |  |  + telemetry traces)   | |
|  |             |  |  drag+drop)      |  |                        | |
|  +------+------+  +--------+---------+  +----------+-------------+ |
+---------|------------------|--------------------------|--------------+
          | JSON POST        | multipart/form-data      | SSE stream
          v                  v                          v
+---------------------------------------------------------------------+
|                  FastAPI Backend (port 8000)                         |
|  +--------------------------------------------------------------+   |
|  |  main.py - API Gateway                                        |   |
|  |  * CORSMiddleware                                             |   |
|  |  * X-Request-ID tracing middleware                            |   |
|  |  * APIKeyHeader authentication (constant-time compare)        |   |
|  |  * Structured JSON logging (_JsonFormatter -> stdout)         |   |
|  |  * Lifespan-managed pipeline singleton                        |   |
|  +----------------------+---------------------------------------+   |
|                         |                                           |
|     +-------------------+------------------------+                  |
|     v                   v                        v                  |
|  +----------+  +------------------+  +------------------------+    |
|  | /health  |  | Adjudication     |  | Document Extraction    |    |
|  | /ready   |  | Pipeline         |  | /api/v2/extract-doc    |    |
|  +----------+  | (singleton)      |  | pypdf / pytesseract    |    |
|                |                  |  | -> LLM extraction      |    |
|                +--------+---------+  +------------------------+    |
|                         |                                           |
|                         v                                           |
|  +--------------------------------------------------------------+  |
|  |            ClaimsAdjudicationPipeline (pipeline.py)           |  |
|  |  Composed via Python mixins:                                  |  |
|  |  * PipelineHelpersMixin    (helpers.py)                       |  |
|  |  * ValidationGatesMixin    (gates_validation.py)              |  |
|  |  * FinancialsGateMixin     (gate_financials.py)               |  |
|  |  * StateGateMixin          (gate_state.py)                    |  |
|  |  * ExecutionMixin          (execution.py)                     |  |
|  +------+------------------------+------------------+            |  |
|         |                        |                  |            |  |
|         v                        v                  v            |  |
|  +------------+  +------------------+  +--------------------+   |  |
|  | AI Planner |  | Semantic Agent   |  | Deterministic      |   |  |
|  | planner.py |  | semantic_agent   |  | Calculators        |   |  |
|  | DAG + topo |  | .py              |  | calculators.py     |   |  |
|  | sort       |  | local | openai   |  | Tools 1-8          |   |  |
|  +------------+  +--------+---------+  +--------------------+   |  |
|                           |                                      |  |
|                           v                                      |  |
|               +------------------+                               |  |
|               | Local LLM        |                               |  |
|               | llama.cpp server |                               |  |
|               | port 8080        |                               |  |
|               | Gemma-4 / GGUF   |                               |  |
|               +------------------+                               |  |
|                                                                  |  |
|  +--------------------------------------------------------------+  |
|  |  Observability Stack                                          |  |
|  |  * AgentReasoningLogger   -> logs/agent_reasoning.log        |  |
|  |  * Per-claim context dump -> logs/claims/<claim_id>_ctx.json |  |
|  |  * PipelineMetricsEngine  -> metrics_telemetry.jsonl         |  |
|  +--------------------------------------------------------------+  |
|                                                                     |
|  +--------------------------------------------------------------+  |
|  |  Persistence Layer                                            |  |
|  |  PostgreSQL 16 (Docker) + SQLAlchemy 2.0 async (asyncpg)     |  |
|  |  Alembic migrations                                           |  |
|  +--------------------------------------------------------------+  |
+---------------------------------------------------------------------+
```

---

## 3. API Endpoints (Complete Reference)

All endpoints are served by FastAPI on `http://127.0.0.1:8000`.
Interactive documentation: `/docs` (Swagger UI), `/redoc` (ReDoc).

### 3.1 Observability

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe. Returns pipeline readiness flag, version, LLM provider. |
| `GET` | `/readiness` | None | Readiness probe. Returns 503 until pipeline is fully initialized. |

### 3.2 Adjudication

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/adjudicate` | Optional API Key | Synchronous full adjudication (v1 — deprecated alias). Returns `ClaimDecision`. |
| `POST` | `/api/v2/adjudicate` | Optional API Key | Synchronous full adjudication (v2 — current). Returns `ClaimDecision`. |
| `POST` | `/api/v2/adjudicate/stream` | Optional API Key | Server-Sent Events streaming. Emits `DecisionTrace` events in real-time as each gate completes, followed by final `ClaimDecision`. Used by the Live Adjudication Panel. |
| `POST` | `/api/v2/adjudicate/summary` | Optional API Key | Accepts a `ClaimDecision` payload. Generates markdown-formatted natural language explanation via LLM. Used for `REJECTED` and `PARTIALLY_APPROVED` cases. |

### 3.3 Document Processing

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v2/extract-document` | Optional API Key | `multipart/form-data` file upload. Accepts PDF or image. Extracts raw text, sends to LLM for structured field parsing, returns `DocumentExtractionResult`. |

### 3.4 Integration Mocks (Development Only)

Registered via `integration/mock_routers.py`. Simulates all 8 external gateway APIs to eliminate real API dependencies during local development.

---

## 4. The 7-Gate Adjudication Pipeline

Every claim passes through an ordered sequence of gates. The AI Planner constructs a Directed Acyclic Graph (DAG) from rule dependencies and topologically sorts it into a linear execution plan before the first gate fires.

```
ClaimContext
     |
     v
GATE 1: Policy Validation         [Deterministic]
  * GATE_1_POLICY_STATUS           Status=Active, premium paid,
  * GATE_1_DATE_RANGE              grace period, claim date in range
     | PASS
     v
GATE 2: Member Validation         [Deterministic]
  * GATE_2_ELIGIBILITY             Member eligibility active,
  * GATE_2_ADDITION_DATE           admission >= date of addition
     | PASS
     v
GATE 3: Coverage Validation       [Hybrid: deterministic + semantic]
  * GATE_3_VARIANT_FILTER          Variant eligibility for benefit
  * GATE_3_RIDER_OPT_IN            Rider opt-in check
  * R3_BEN_003_DURATION            Min 2h stay (24h alt treatment)
  * R3_BEN_007_PRECON              Domiciliary 3-criteria check
  * Semantic: coverage assessment  LLM validates clinical coverage
     | PASS
     v
GATE 4: Waiting Period            [Hybrid]
  * GATE_4_ACCIDENT_EXEMPT         Accident waives all wait periods
  * GATE_4_INITIAL_WAIT            30-day initial wait
  * GATE_4_SPECIFIED_DISEASE       24-month specified disease list
  * GATE_4_PED_WAIT                36-month PED wait check
  * GATE_4_PORTABILITY             Porting credit deduction
     | PASS
     v
GATE 5: Exclusion Validation      [Semantic - LLM-driven]
  * GATE_5_DETERMINISTIC_EXCL      Hard-coded exclusion list filter
  * GATE_5_SEMANTIC_AGENT          LLM analyzes discharge_summary +
                                   condition_diagnosed for cosmetic,
                                   diagnostic-only, or OPD exclusions
     | PASS
     v
GATE 6: Financial Computation     [Deterministic tools]
  * GATE_6_ROOM_RENT               Tool 2: pro-rata deduction
  * GATE_6_PROLONGED_HOSP          >168h co-payment penalty
  * GATE_6_FINANCIAL_WATERFALL     Tool 5: Base SI -> Booster+ ->
  * Tool 3: Co-payment             ReAssure Forever waterfall
  * Tool 4: Deductible             Tool 3 + Tool 4 applied
     |
     v
GATE 7: State Update              [Deterministic]
  * GATE_7_STATE_PERSISTENCE       ReAssure Forever state machine
  * Tool 6: Lock the Clock         Age premium locking
  * Tool 7: Booster accumulation   Booster+ pool update
  * Tool 8: HDC + PA benefit       Hospital Daily Cash, PA payout
     |
     v
ClaimDecision
```

**Early-exit behaviour:** Any gate returning `FAILED` or `EXCLUSION_ACTIVE` terminates the pipeline immediately and returns the decision with the failing rule in the audit trace.

---

## 5. Confidence-Based Routing (Four-Tier)

Every LLM call returns a `confidence_score` (0.0–1.0). The system maps this to one of four adjudication outcomes:

| Confidence Range | Outcome | Routing |
|---|---|---|
| `>= 0.90` | `APPROVED` / `PARTIALLY_APPROVED` / `REJECTED` | Fully automated — no human review |
| `[0.70, 0.90)` | `ASSISTED_REVIEW` | Operations queue — review within SLA |
| `[0.50, 0.70)` | `MEDICAL_REVIEW` | Clinical expert queue |
| `< 0.50` | `PENDING_REVIEW` | Senior auditor queue |

Thresholds are runtime-configurable via `.env` (no code change required to adjust sensitivity).

---

## 6. AI Planner — DAG Construction

**File:** `src/planner.py`

Before executing any gate, the `AIPlanner` class:

1. Loads all applicable `RuleBlueprint` objects from `ProductMemoryStore` filtered by policy variant (Classic / Select / Elite) and benefit bucket.
2. Constructs a `DAGBuilder` graph where each rule node's `depends_on` list forms directed edges.
3. Runs Kahn's algorithm topological sort to produce a linearized `ExecutionPlan`.
4. Each `ExecutionStep` carries: rule ID, gate classification, priority, execution type (`DETERMINISTIC` / `SEMANTIC` / `HYBRID`), optional tool name, and the pre-rendered semantic prompt with claim data injected.

The planner is called once per line item per claim. Its output is a reproducible `ExecutionPlan` that can be logged and re-played for audit purposes.

---

## 7. Semantic Agent — LLM Integration

**File:** `src/semantic_agent.py`

The `SemanticExecutionAgent` is a singleton initialized at startup. It supports three LLM providers selected at runtime via `LLM_PROVIDER` in `.env`:

### 7.1 Provider: `local` (default and production target)

Connects to a locally-deployed `llama.cpp` server (default `http://127.0.0.1:8080`).

**Dual-path strategy:**
- **Path 1 (`/completion`):** Uses the Gemma-4 E4B QAT reasoning chat template. Supports chain-of-thought `<think>…</think>` blocks before the final JSON output. GBNF grammar applied when `REASONING_ON=false` to force valid JSON.
- **Path 2 (`/v1/chat/completions`):** OpenAI-compatible fallback, used automatically if `/completion` is not detected on startup probe.

**No mmproj required.** The LLM receives only text. Document images are converted to text server-side before any LLM call.

### 7.2 Provider: `openai`

Calls `gpt-4o-mini` via the OpenAI Python SDK. Requires `OPENAI_API_KEY` in `.env`. Uses `response_format={"type": "json_object"}` with `temperature=0.0` for deterministic outputs.

### 7.3 Provider: `mock` / `default`

Used in test suites only. Returns pre-defined structured responses without making network calls.

### 7.4 LLM Call Types

| Call Purpose | Trigger | Output Schema |
|---|---|---|
| Exclusion assessment | Gate 5 semantic rules | `SemanticAdjudicationPayload` |
| Coverage validation | Gate 3 semantic rules | `SemanticAdjudicationPayload` |
| Waiting period assessment | Gate 4 semantic rules | `SemanticAdjudicationPayload` |
| Decision summary generation | `POST /api/v2/adjudicate/summary` | `DecisionSummaryPayload` |
| Document field extraction | `POST /api/v2/extract-document` | `DocumentExtractionLLMPayload` |

All LLM calls are wrapped in `asyncio.run_in_executor` (thread pool) so the uvicorn event loop is never blocked.

**Caching:** Results are keyed as `{rule_id}:{rule_type}:{hash(prompt[:500])}` and cached in-memory per pipeline instance to avoid duplicate LLM calls for identical prompts within the same request.

**Timeouts:** Connect = 60s, Read = 300s (configurable via `LLM_CONNECT_TIMEOUT_S` / `LLM_READ_TIMEOUT_S`).

---

## 8. Deterministic Calculators (Tools 1–8)

**File:** `src/calculators.py`

All calculators are pure Python functions with no AI inference. They receive typed inputs and return structured dataclasses.

| Tool | Function | Purpose |
|---|---|---|
| Tool 1 | `calculate_waiting_period` | Computes remaining wait days for initial, specified-disease, and PED periods. Applies porting credit. Accident-related claims bypass all waits. |
| Tool 2 | `calculate_room_pro_rata` | Applies room rent cap deduction proportionally across all associated medical expenses (nursing, doctor, OT fees). |
| Tool 3 | `calculate_copayment` | Applies co-payment percentage (policy-level + network + age-based) to admissible amount. |
| Tool 4 | `calculate_deductible` | Applies annual aggregate deductible, tracks YTD consumption across claims. |
| Tool 5 | `calculate_si_waterfall` | Sequentially draws from Base SI → Booster+ → ReAssure Forever pool. Returns per-pool amounts and shortfall. |
| Tool 6 | `calculate_lock_the_clock` | Pins premium calculation age to entry age for eligible members. |
| Tool 7 | `calculate_booster_accumulation` | Accumulates Booster+ points based on claim-free years. |
| Tool 8a | `calculate_hospital_daily_cash` | Computes HDC benefit based on hospitalization duration and daily rate. |
| Tool 8b | `calculate_personal_accident_benefit` | Computes PA payout based on injury type and PA sum insured. |

---

## 9. Document Extraction Pipeline

**File:** `src/document_extractor.py`
**Endpoint:** `POST /api/v2/extract-document`

```
Browser file input (PDF / PNG / JPG / TIFF / multiple files supported)
          |
          v
   FastAPI UploadFile (multipart/form-data)
          |
          v
   route_extraction(filename, bytes)
     |                         |
     v                         v
  .pdf extension          .png/.jpg/.tiff
     |                         |
  pypdf.PdfReader          Pillow + pytesseract
  (native text)            (OCR engine)
     |                         |
     +----------+--------------+
                v
           raw_text (str)
           [truncated to 4000 chars for LLM context safety]
                |
                v
   build_extraction_prompt()
   -> system_prompt + user_prompt
                |
                v
   SemanticAgent._call_llm()
   -> DocumentExtractionLLMPayload (JSON)
                |
                v
   DocumentExtractionResult
   {filename, raw_text_length, extracted{...}}
                |
                v
   Frontend auto-fills LineItem fields
```

**Extracted fields** (all `Optional`, `null` when not found in document):

| Field | Type | Description |
|---|---|---|
| `discharge_summary` | str | 2-5 sentence clinical narrative |
| `condition_diagnosed` | str | Primary diagnosis / ICD description |
| `admission_date` | ISO 8601 | Hospital admission date |
| `discharge_date` | ISO 8601 | Hospital discharge date |
| `hospitalization_hours` | float | Duration of stay in hours |
| `claimed_amount` | float INR | Total bill amount |
| `room_charges` | float INR | Room rent charges |
| `nursing_charges` | float INR | Nursing charges |
| `medical_practitioner_fees` | float INR | Doctor / specialist fees |
| `ot_charges` | float INR | Operation theatre charges |

**Scanned PDF fallback:** If `pypdf` returns empty text, the extractor reads inline image XObjects from the PDF and OCR's them individually via `pytesseract`.

**System dependency:** `pytesseract` requires the Tesseract binary (`winget install UB-Mannheim.TesseractOCR` on Windows). Native-text PDFs work without it.

**Multi-file support:** The frontend accepts multiple files simultaneously. Each file is independently extracted and tracked with its own status, progress bar, extracted result, and inline detail drawer. The last successfully extracted document's fields overwrite the claim line item form.

---

## 10. Product Memory Layer

**File:** `src/product_memory.py`
**Source:** `docs/Product and Policy Rules Extraction.json` (version `R3_v2.1_2025-01-15`)

Each rule is a `RuleBlueprint`:

| Field | Type | Description |
|---|---|---|
| `rule_id` | str | Unique identifier (e.g., `R3_EXCL_017`) |
| `rule_name` | str | Human-readable name |
| `gate` | `RuleGate` enum | Which of the 7 gates this rule belongs to |
| `execution_type` | `ExecutionType` enum | `DETERMINISTIC` / `SEMANTIC` / `HYBRID` |
| `variant_applicability` | `List[str]` | Classic / Select / Elite |
| `depends_on` | `List[str]` | DAG dependency list (other rule IDs) |
| `semantic_prompt_template` | str | LLM prompt with `{{placeholder}}` variables |
| `tool_required` | str | Maps to a calculator function |
| `priority` | int | Gate-relative ordering (lower = earlier) |
| `confidence_weight` | float | Contribution to composite confidence score |
| `auto_adjudicable` | bool | `False` = mandatory human review regardless of confidence |

The `ProductMemoryStore` is loaded once at startup and held in memory. Product version is logged on every adjudication call for full traceability.

---

## 11. Data Models (Schema Layer)

**File:** `src/schemas.py` — Pydantic v2, strict typing throughout.

### 11.1 Input: ClaimContext

```
ClaimContext
├── claim_id (str)
├── claim_received_at (datetime)
├── product_json_version (str)
├── fraud_flagged (bool)
├── policy (PolicyData)
│   ├── policy_id, variant, status, dates, base_sum_insured
│   ├── Riders: borderless_opted, heads_up_opted, modern_treatments_plus_opted, etc.
│   └── Financial: room_rent_limit, co_payment_percent, annual_aggregate_deductible
├── member (MemberData)
│   ├── member_id, age, entry_age, relationship
│   └── ped_declarations (list of declared pre-existing conditions)
├── history (ClaimsHistoryData)
│   └── prior_claims_count, total_utilized_si, claim_free_years
├── porting (PortingMigrationData)
│   └── prior_coverage_months, waiting_period_credit_months
├── network (NetworkData)
│   └── provider_type: Network / Non-Network / Excluded
├── benefit_balance (BenefitBalanceData)
│   └── base_si_remaining, booster_plus_remaining, reassure_forever_pool
├── lifetime_state (LifetimeStateData)
│   ├── reassure_forever_state: NOT_TRIGGERED | TRIGGERED | ACTIVE | LAPSED
│   └── lock_the_clock_age_locked, booster_plus_accumulated
├── endorsements (List[EndorsementData])
└── line_items (List[LineItemData])
    ├── line_item_id, description, claimed_amount, benefit_bucket
    ├── admission_date, discharge_date, hospitalization_hours
    ├── discharge_summary (clinical text — feeds Gate 5 LLM)
    ├── condition_diagnosed, treatment_type, accident_related
    └── room_charges, nursing_charges, medical_practitioner_fees, ot_charges
```

### 11.2 Output: ClaimDecision

```
ClaimDecision
├── claim_id
├── claim_decision
│     APPROVED | PARTIALLY_APPROVED | REJECTED |
│     ASSISTED_REVIEW | MEDICAL_REVIEW | PENDING_REVIEW
├── confidence_score (0.0–1.0 composite)
├── total_claimed, total_admissible, total_payable, total_deductions
├── deduction_breakdown
│   ├── room_pro_rata, co_payment, deductible
│   ├── non_payable_items, si_cap, sublimit
│   └── prolonged_hosp_penalty, heads_up_penalty, tiered_network_penalty
├── si_waterfall_breakdown
│   └── base_si_used, booster_plus_used, forever_pool_used
├── line_item_decisions (List[LineItemDecision])
│   └── per-line item: payable, deductions, rule evaluations
└── decision_trace (List[DecisionTrace])
    └── step, rule_id, gate, evaluation, reason, confidence, timestamp
```

---

## 12. Database Layer

**Files:** `src/db/` — SQLAlchemy 2.0 async ORM + Alembic migrations
**Engine:** PostgreSQL 16 (Docker Compose)

| Table | Maps To | Notes |
|---|---|---|
| `policies` | `PolicyData` | `NUMERIC(18,2)` for all monetary fields. `JSONB` for riders list. |
| `members` | `MemberData` | FK to `policies`. `JSONB` for PED declarations. |
| `claims_history` | `ClaimsHistoryData` | Tracks SI utilization per policy/member. |
| `porting_records` | `PortingMigrationData` | Waiting period credit history. |
| `network_providers` | `NetworkData` | Provider classification. |
| `benefit_balances` | `BenefitBalanceData` | Mutable — row-level locked on write to prevent concurrent SI overdraw. |
| `lifetime_states` | `LifetimeStateData` | Mutable — ReAssure Forever state machine persisted here. |
| `endorsements` | `EndorsementData` | Mid-term endorsement records. |

**Async driver:** `asyncpg` via `postgresql+asyncpg://` connection string.
**Docker Compose:** PostgreSQL 16-alpine with health-check + pgAdmin4 on port 5050.

---

## 13. Context Builder — External API Integration

**File:** `src/integration/context_builder.py`

Assembles `ClaimContext` by calling 8 external gateways concurrently via `httpx` async client:

| Gateway | Data Pulled |
|---|---|
| Policy API | Policy status, variant, SI, riders, co-pay config |
| Member API | Member eligibility, age, relationship, PED declarations |
| Claims History API | Prior utilization, claim-free years |
| Porting API | Prior coverage months, waiting period credits |
| Network Provider API | Hospital network classification |
| Benefit Balance API | Remaining SI, Booster+, ReAssure Forever pool |
| Lifetime State API | Forever state machine, Lock the Clock status |
| Endorsements API | Active mid-term endorsements |

Each gateway call is individually timed and logged. The assembled `ClaimContext` is dumped to `logs/claims/<claim_id>_ctx_<timestamp>.json` before adjudication begins.

---

## 14. Frontend Application

**Stack:** React 18 + TypeScript + Vite
**Runtime URL:** `http://localhost:5173`

### 14.1 Component Architecture

```
App.tsx (single-page application)
├── State Layer
│   ├── useClaimContext()    complete ClaimContext assembly and field mutations
│   └── useAdjudication()   streaming adjudication submission + state
├── Left Column: Claim Data Entry
│   ├── Preset selector (Classic HC, Select HC, Elite HC, Floater scenarios)
│   ├── Member DB sync (live fetch from mock gateways by member ID)
│   ├── Policy Data accordion
│   ├── Member Data accordion
│   ├── Endorsements accordion
│   ├── Claim Line Items accordion (all bill fields, discharge summary textarea)
│   └── Document Upload Zone (multi-file, PDF/image, drag+drop)
│       ├── Per-file progress bar during LLM extraction
│       ├── File list with VIEW/REMOVE controls per file
│       └── Inline detail drawer: extracted fields + image thumbnail
└── Right Column: Live Engine
    ├── Telemetry Control Header
    ├── Live Scan Engine (SSE trace panel — real-time gate results)
    └── Decision Results Panel
        ├── Decision badge (APPROVED / PARTIAL / REJECTED / REVIEW tier)
        ├── Financial breakdown grid (claimed / admissible / payable / deductions)
        ├── SI Waterfall visualization
        ├── AI-generated decision summary (markdown, auto-triggered for non-approval)
        └── Per-line-item decision cards
```

### 14.2 Service Layer

**File:** `frontend/src/services/adjudicationApi.ts`

| Function | HTTP Call | Purpose |
|---|---|---|
| `adjudicateClaim(context)` | `POST /api/v2/adjudicate` | Synchronous adjudication |
| `adjudicateClaimStream(context, ...)` | `POST /api/v2/adjudicate/stream` | SSE streaming adjudication |
| `getClaimSummary(decision)` | `POST /api/v2/adjudicate/summary` | AI narrative summary |
| `extractDocument(file)` | `POST /api/v2/extract-document` | Document OCR + LLM extraction |

### 14.3 End-to-End Data Flow: Document Upload → Adjudication

```
1.  User drops file(s) onto upload zone (PDF or image, multiple supported)
2.  Each file immediately registered in uploadedDocs[] with status=uploading
3.  extractDocument(file) POSTs to /api/v2/extract-document
4.  Backend: pypdf/pytesseract -> raw text -> LLM prompt -> DocumentExtractionResult
5.  Frontend: non-null fields auto-applied to line_items[0] via updateLineItem()
6.  User reviews pre-filled form, edits if needed
7.  User clicks "Initiate Adjudication"
8.  adjudicateClaimStream() opens SSE connection to /api/v2/adjudicate/stream
9.  Each DecisionTrace event streams in real-time -> Live Scan Panel updates
10. Final ClaimDecision received -> Decision Results Panel renders
11. If REJECTED/PARTIAL: getClaimSummary() fires asynchronously -> AI summary appears
```

---

## 15. Observability and Audit

### 15.1 Agent Reasoning Logger

**File:** `src/agent_reasoning.py`

Every LLM call produces a structured log entry written to `logs/agent_reasoning.log`:
- `claim_id`, `line_item_id`, `rule_id`
- LLM provider + URL used
- Full `system_prompt` and `user_prompt` sent
- Raw LLM response string
- Parsed `structured_output`
- `confidence_score`
- `requires_manual_review` flag

Per-claim context snapshots written to `logs/claims/<claim_id>_ctx_<ts>.json`.

### 15.2 Telemetry Metrics Engine

**File:** `src/metrics.py`

`PipelineMetricsEngine` records every adjudication run to `metrics_telemetry.jsonl` (append-only, thread-safe file lock). Each record:
- Claim ID, final decision, confidence score
- Per-gate latencies (ms)
- Per-tool latencies (ms)
- PAS reconciliation delta (simulated 92% concordance baseline)
- ISO 8601 timestamp

### 15.3 HTTP Request Tracing

Every HTTP response carries:
- `X-Request-ID` — echoed from request header or auto-generated UUID
- `X-Processing-Time-Ms` — total request duration in milliseconds

### 15.4 Structured JSON Logging

`_JsonFormatter` writes every log record as a single-line JSON object to stdout, compatible with Datadog, Splunk, CloudWatch, and equivalent log aggregators.

---

## 16. Security Model

| Control | Implementation |
|---|---|
| API Authentication | `APIKeyHeader` with `secrets.compare_digest()` (constant-time comparison, timing-attack resistant). Disabled when `ADJUDICATION_API_KEY` is unset (development only). |
| CORS Policy | Restricted to `CORS_ORIGINS` from `.env`. Default: `localhost:5173` and `127.0.0.1:5173` only. |
| Secret Management | All secrets (`OPENAI_API_KEY`, `DATABASE_URL`, `ADJUDICATION_API_KEY`, DB credentials) loaded exclusively from `.env` via `pydantic-settings`. Nothing hardcoded. |
| Internal Error Masking | Unhandled exceptions return a generic 500 message. Full stack trace is logged internally only — never exposed in the API response body. |
| Input Validation | All API request bodies validated by Pydantic v2 before touching business logic. Invalid payloads return structured `422` errors with field-level detail. |
| DB Monetary Precision | All INR columns are `NUMERIC(18,2)` — no float drift on financial calculations. |
| Concurrent SI Writes | PostgreSQL row-level locks on `benefit_balances` and `lifetime_states` tables to prevent concurrent sum insured overdraw. |

---

## 17. Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `local` | `local` / `openai` / `mock` |
| `LLM_URL` | `http://127.0.0.1:8080` | llama.cpp server base URL |
| `OPENAI_API_KEY` | (blank) | Required for `openai` provider only |
| `REASONING_ON` | `true` | Enable chain-of-thought thinking in local LLM |
| `CONFIDENCE_THRESHOLD` | `0.90` | Minimum confidence for auto-adjudication |
| `AUTO_APPROVE_THRESHOLD` | `0.90` | Confidence >= this → automatic approval |
| `ASSISTED_REVIEW_THRESHOLD` | `0.70` | Confidence in [0.70, 0.90) → ops review queue |
| `MEDICAL_REVIEW_THRESHOLD` | `0.50` | Confidence in [0.50, 0.70) → clinical review queue |
| `CORS_ORIGINS` | `localhost:5173,...` | Comma-separated allowed frontend origins |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async PostgreSQL connection string |
| `ADJUDICATION_API_KEY` | (blank) | API key for endpoint auth (omit to disable in dev) |
| `TELEMETRY_FILE` | `logs/metrics_telemetry.jsonl` | Telemetry output path |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `LLM_CONNECT_TIMEOUT_S` | `60` | Connect timeout for local LLM (seconds) |
| `LLM_READ_TIMEOUT_S` | `300` | Read timeout for local LLM (seconds) |

---

## 18. Deployment Topology

### Current: Development

```
Developer Machine
├── PostgreSQL 16     docker-compose up -d                (port 5432)
├── pgAdmin 4         docker-compose up -d                (port 5050)
├── llama.cpp server  ./server -m model.gguf              (port 8080)
├── FastAPI backend   uvicorn src.main:app --reload       (port 8000)
└── Vite dev server   pnpm run dev                        (port 5173)
```

### Production Path (not yet deployed)

- Backend containerized, deployed behind an internal load balancer
- PostgreSQL replaced by a managed RDS instance
- LLM inference moved to GPU instance or internal inference cluster
- `ADJUDICATION_API_KEY` rotated via secrets manager
- CORS locked to production frontend domain only
- Structured logs forwarded to central aggregator (Datadog / Splunk)

---

## 19. Test Coverage

**Framework:** pytest 7+, pytest-asyncio, httpx TestClient

**Current status: 67 tests, 0 failures (as of 2026-07-02)**

| Test File | Scope |
|---|---|
| `test_phase2_integration.py` | End-to-end pipeline smoke tests, rule filtering, DAG construction, semantic agent mocking, confidence routing tiers |
| `test_integration_plumbing.py` | HTTP endpoint integration tests via TestClient, SSE stream plumbing |
| `test_new_features.py` | AI summary endpoint, document extraction schema, newer gate logic |

**Run command:**
```bash
$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest src/tests/ -x -q --tb=short
```

---

## 20. Technology Stack Summary

| Component | Technology | Version |
|---|---|---|
| Backend framework | FastAPI | >= 0.111 |
| Language | Python | 3.11+ |
| Data validation | Pydantic v2 | >= 2.5 |
| Async ORM | SQLAlchemy | >= 2.0.30 |
| Async DB driver | asyncpg | >= 0.29 |
| Schema migrations | Alembic | >= 1.13 |
| LLM inference (local) | llama.cpp | Current |
| LLM SDK (cloud fallback) | openai | >= 1.30 |
| PDF text extraction | pypdf | >= 4.2 |
| Image OCR | pytesseract + Pillow | >= 0.3.13 / >= 10.3 |
| File upload parsing | python-multipart | >= 0.0.9 |
| Frontend framework | React + TypeScript | 18 / 5 |
| Frontend build tool | Vite | 8 |
| Database | PostgreSQL | 16 |
| Container runtime | Docker Compose | 3.9 |
| HTTP client (frontend) | Native fetch + SSE | — |
