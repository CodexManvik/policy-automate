# Agent Implementation Compliance Report
Cross-reference: `Product_and_Policy_Solution_Documents.md` vs `src/`

---

## Summary Table

| TSD Component | Section | Implemented | Source File | Status |
|---|---|---|---|---|
| Context Builder | 4.1 | YES | `schemas.py` + `main.py` | Full |
| Product Memory Store | 4.2 | YES | `product_memory.py` | Full |
| AI Planner | 4.3 | YES | `planner.py` | Full |
| AI Execution Agent | 4.4 | YES | `pipeline.py` | Full |
| Calculation Tools (1–10) | 4.5 | YES | `calculators.py` | Full |
| State Manager | 4.6 | YES | `schemas.py` + `pipeline.py` (Gate 7) | Full |
| Decision Composer | 4.7 | YES | `pipeline.py` (`_compose_claim_decision`) | Full* |
| Semantic Execution Agent | 4.4 sub | YES | `semantic_agent.py` | Full |
| Agent Reasoning Logger | 12.1 | YES | `agent_reasoning.py` | Full |
| Observability / Metrics | 12.2 | YES | `metrics.py` | Full |
| Gate 1: Policy Validation | 5 | YES | `pipeline.py` `_gate_1_policy_validation` | Full |
| Gate 2: Member Validation | 5 | YES | `pipeline.py` `_gate_2_member_validation` | Full |
| Gate 3: Coverage Validation | 5 | YES | `pipeline.py` `_gate_3_coverage_validation` | Full |
| Gate 4: Waiting Period | 5 | YES | `pipeline.py` `_execute_waiting_period_check` | Full |
| Gate 5: Exclusion Validation | 5 | YES | `pipeline.py` `_gate_5_exclusion_validation` | Partial |
| Gate 6: Financial Computation | 5 | YES | `pipeline.py` `_gate_6_financial_computation` | Full |
| Gate 7: State Update | 5 | YES | `pipeline.py` `_gate_7_state_update` | Full |
| DAG / Dependency Graph | 6 | YES | `planner.py` `DAGBuilder` | Full |
| Parallel Execution | 6.3 | YES | `pipeline.py` `_execute_plan_async` | Full |
| ReAssure Forever state machine | 7.1 | YES | `pipeline.py` Gate 7 + `calculators.py` | Full |
| Lock the Clock state machine | 7.2 | YES | `calculators.py` Tool 6 + Gate 7 | Full |
| Booster+ accumulation logic | 7.3 | YES | `calculators.py` Tool 7 + Gate 7 | Full |
| Mid-term Endorsements | 8 | YES | `pipeline.py` `_apply_endorsements` | Full |
| Confidence Scoring (3-tier) | 9.2 | YES | `pipeline.py` `_execute_plan` | Full |
| Fail-safe routing | 3.5 | YES (fixed today) | `pipeline.py` | Full |
| Manual Review Exception Payload | 12 | YES | `schemas.py` `ManualReviewExceptionPayload` | Defined only |
| PAS Reconciliation | 11 | PARTIAL | `schemas.py` `pas_submission_payload` | Stub only |

---

## Detail by Component

### 4.1 Context Builder — IMPLEMENTED
`ClaimContext` in [schemas.py](file:///c:/Project/nivabupa/policy%20automate/src/schemas.py) is the canonical output. It covers every API source the TSD lists:

| TSD API | Schema Field |
|---|---|
| Policy API | `context.policy: PolicyData` |
| Member API | `context.member: MemberData` |
| Claims History API | `context.history: ClaimsHistoryData` |
| Porting/Migration API | `context.porting: PortingMigrationData` |
| Network API | `context.network: NetworkData` |
| Benefit Balance API | `context.benefit_balance: BenefitBalanceData` |
| Lifetime State API | `context.lifetime_state: LifetimeStateData` |
| Endorsement API | `context.endorsements: List[EndorsementData]` |

**Gap:** There is no actual HTTP client assembling this context from live APIs. It is assembled at the API layer in [main.py](file:///c:/Project/nivabupa/policy%20automate/src/main.py) by deserialising the inbound JSON body. This is correct for demo/validation phase but would need an integration client for production.

---

### 4.2 Product Memory Store — IMPLEMENTED
[product_memory.py](file:///c:/Project/nivabupa/policy%20automate/src/product_memory.py) implements `ProductMemoryStore`:
- Loads versioned product JSON from `docs/` on startup
- Caches by version string (`get_product_memory(version)`)
- Exposes `filter_rules(gate, variant, benefit_bucket)` for the planner
- Contains `R3_TBL_RATE_TABLES` for Lock the Clock actuarial math
- Contains `mutual_exclusivity_constraints` for all constraint checks

**Gap:** The TSD describes a file-system store with multiple dated JSON versions (`v1.0_2024-04-01.json`, `v2.0_2025-01-15.json`). The implementation uses a single JSON bundled into the `docs/` folder. Version switching is done at runtime by the `product_json_version` field on `ClaimContext`, but the underlying files are not split per version. Not a blocking gap for demo.

---

### 4.3 AI Planner — IMPLEMENTED
[planner.py](file:///c:/Project/nivabupa/policy%20automate/src/planner.py) contains:
- `DAGBuilder`: constructs adjacency list + tracks in-degrees
- Topological sort (Kahn's algorithm with priority queue)
- `AIPlanner.create_execution_plan(context, line_item)` — filters rules by variant and benefit bucket, builds DAG, returns `ExecutionPlan`
- `ExecutionStep` dataclass matches TSD schema exactly (step, rule_id, gate, reason, execution_type, depends_on)

No gaps.

---

### 4.4 AI Execution Agent — IMPLEMENTED (two variants)

**Sync path:** [pipeline.py](file:///c:/Project/nivabupa/policy%20automate/src/pipeline.py) `_execute_plan` — iterates plan steps, routes deterministic/semantic/hybrid, implements 3-tier confidence routing.

**Async path:** `_execute_plan_async` — groups steps by DAG depth layer, executes independent steps concurrently via `asyncio.gather`.

**Semantic sub-agent:** [semantic_agent.py](file:///c:/Project/nivabupa/policy%20automate/src/semantic_agent.py) `SemanticExecutionAgent`:
- Supports `local` (llama.cpp), `mock`, `openai`, `anthropic` providers
- GBNF grammar constrains structured output to `SemanticAdjudicationPayload` (evaluation_status, reasoning_trace, confidence_score)
- Implements the Gemma 4 reasoning chat template (thinking tokens) for the local path
- Falls back to mock deterministically when LLM server is offline

> [!IMPORTANT]
> **The TSD constraint** (Section 4.4): *"The execution agent CANNOT override a tool's output."*
> This is enforced: deterministic steps call calculator tools directly and use their output verbatim. The semantic agent only handles non-deterministic clauses.

---

### 4.5 Calculation Tools — FULLY IMPLEMENTED

All 10 tools are in [calculators.py](file:///c:/Project/nivabupa/policy%20automate/src/calculators.py):

| TSD Tool | Function | Status |
|---|---|---|
| Tool 1: Waiting Period | `calculate_waiting_period` | Full |
| Tool 2: Room Pro-Rata | `calculate_room_pro_rata` | Full |
| Tool 3: Co-Payment | `calculate_copayment` | Full |
| Tool 4: Deductible | `calculate_deductible` | Full |
| Tool 5: SI Waterfall | `calculate_si_waterfall` | Full |
| Tool 6: Lock the Clock | `calculate_lock_the_clock` | Full (multi-tenure via R3_TBL_RATE_TABLES) |
| Tool 7: Booster+ | `calculate_booster_accumulation` | Full |
| Tool 8: Pre/Post Hosp Window | `validate_pre_post_hosp_window` | Full |
| Tool 9: Hospital Daily Cash | `calculate_hospital_daily_cash` | Full |
| Tool 10: Personal Accident | `calculate_personal_accident_benefit` | Full |

---

### 4.6 State Manager — IMPLEMENTED

- **Per-claim state:** `PerClaimState` in [schemas.py](file:///c:/Project/nivabupa/policy%20automate/src/schemas.py) — tracks running financials, gate flags, confidence, waterfall allocations.
- **Lifetime state:** `LifetimeStateData` in schemas.py — Lock the Clock age lock, ReAssure Forever trigger, Booster+, Convalescence/CI flags, Cash-Bag+ wallet, Live Healthy points.
- **Gate 7** in `pipeline.py` `_gate_7_state_update` writes back to `context.benefit_balance` and `context.lifetime_state` after each claim.

**Gap:** State is only persisted in-memory within a single request. There is no database persistence layer. In production, Gate 7 would need to POST updated lifetime state back to a Lifetime State API.

---

### 4.7 Decision Composer — IMPLEMENTED

`_compose_claim_decision` in [pipeline.py](file:///c:/Project/nivabupa/policy%20automate/src/pipeline.py):
- Aggregates line item decisions into `ClaimDecision`
- Priority routing: `PENDING_REVIEW > ASSISTED_REVIEW > financial outcome`
- Aggregates `DeductionBreakdown` by type
- Builds `SIWaterfallBreakdown` from state
- Populates `pas_submission_payload` with full structured payload
- As of today: now also populates `review_reasons` list from failed traces

> [!NOTE]
> **`ASSISTED_REVIEW`** state (Section 9.2) was fully added. The TSD mentions it as a routing state but the original composer only surfaced `PENDING_REVIEW` and hard financial decisions. Both sync and async paths now correctly route semantic ambiguity to `ASSISTED_REVIEW`.

---

### 9.2 Confidence Routing — IMPLEMENTED

Three-tier model from TSD Section 9.2:

| TSD Threshold | Implementation |
|---|---|
| `>= 0.90` | `AUTO` — deterministic financial decision emitted |
| `0.70–0.90` | `ASSISTED_REVIEW` — pre-populated, flagged for ops |
| `< 0.70` | `PENDING_REVIEW` — full manual queue |
| Semantic `FAILED` (non-exclusion) | `ASSISTED_REVIEW` — today's fix |

---

### 12.1 Observability — IMPLEMENTED

[agent_reasoning.py](file:///c:/Project/nivabupa/policy%20automate/src/agent_reasoning.py) logs all events to `agent_reasoning.log`:
- `PLANNER_DAG_COMPILED` — every execution plan
- `TOOL_CALLED` — all calculator invocations with inputs/outputs
- `LLM_INFERENCE` — prompt, raw response, confidence
- `GATE_EVALUATION` — every rule evaluation result
- `CONFIDENCE_ROUTING` — every routing decision
- `MUTUAL_EXCLUSIVITY_VALIDATION` — constraint checks
- `ENDORSEMENT_APPLIED` — mid-term endorsement mutations

[metrics.py](file:///c:/Project/nivabupa/policy%20automate/src/metrics.py) implements `PipelineMetricsEngine` — tracks all metrics from TSD Section 12.2.

---

## What Is NOT Implemented

| TSD Requirement | Status | Notes |
|---|---|---|
| **Context Builder HTTP clients** | NOT BUILT | No actual API calls to Policy/Member/Network APIs. Input comes in as JSON body. |
| **Lifetime state persistence** | NOT BUILT | Gate 7 mutates in-memory only. Needs POST to a Lifetime State API. |
| **PAS Reconciliation comparator** | NOT BUILT | `pas_submission_payload` is generated correctly but no comparator service exists. |
| **ManualReviewExceptionPayload routing** | SCHEMA ONLY | `ManualReviewExceptionPayload` is defined in schemas.py but never emitted via a dedicated endpoint. The decision itself routes correctly; there is no separate exception queue push. |
| **Multi-version product JSON files** | NOT BUILT | All versions point to the same bundled JSON. Version binding at `get_product_memory(version)` works but files are not truly split. |
| **MEDICAL REVIEW** routing tier | NOT BUILT | TSD Section 9.2 defines a fourth routing path: `any_exclusion_triggered AND confidence < 0.80 → MEDICAL REVIEW`. The implementation only has 3 tiers (PENDING/ASSISTED/AUTO). |
| **Fraud flag check** Gate 1 | NOT BUILT | Gate 1 checks status/premium/dates. TSD also says "Not fraud-flagged" — no fraud flag field on `PolicyData`. |
