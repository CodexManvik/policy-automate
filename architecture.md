# Health Claims Auto-Adjudication Engine: Architecture & Technical Specification

This document provides the technical architecture and formal specification of the Niva Bupa claims auto-adjudication engine (ReAssure 3.0). It details the system structure, orchestration pipeline, rule evaluation gates, mathematical formulations, dynamic rules ingestion, and engineering decisions.

---

## 1. Architectural Overview

The adjudication engine is structured as a dynamic, pipeline-based rule execution graph. It decouples the core insurance mathematics (stateless calculators) from policy schemas, versioned rules, table configurations, and running state.

### Ingestion and Execution Pipeline

```
[Claim Payload Input]
          │
          ▼
[ClaimsAdjudicationPipeline]
          │
          ▼
[Step 1: Endorsement Processing]
(Processes MemberAddition, SIEnhancement, etc.
 chronologically, mutating ClaimContext in-place)
          │
          ▼
[Step 2: Mutual Exclusivity Check (Claim Level)]
(Validates conflicting benefit configurations;
 aborts to PENDING_REVIEW if violated)
          │
          ▼
[Loop: For Each Claim Line Item]
          │
          ├──► Gate 1: Policy Validation
          │       └── status check, premium/grace period, policy date range check
          │
          ├──► Gate 2: Member Validation
          │       └── eligibility check, member addition date check
          │
          ├──► Gate 3: Coverage Validation
          │       └── variant filter, optional benefit check, Home Care 3-precondition check
          │
          ├──► Gate 4: Waiting Period Validation
          │       └── accident Day-1, personal wait, initial 30-day, specified disease (R3_TBL_007), PED, portability
          │
          ├──► Gate 5: Exclusion Validation (Deterministic / Semantic / Hybrid)
          │       └── routes to Semantic Execution Agent if required
          │
          ├──► Mutual Exclusivity Check (Line Item Level)
          │       └── (Runs before financial computation; violations route to PENDING_REVIEW)
          │
          ├──► Gate 6: Financial Computation
          │       ├── Step 0: Non-payable items removal (regex word boundaries)
          │       ├── Step 0.5: Modern treatment sublimit (R3_BEN_005)
          │       ├── Special Benefit Branch Check:
          │       │     ├── Hospital Daily Cash Branch (Tool 9) -> Bypasses waterfall, exits loop
          │       │     └── Personal Accident Branch (Tool 10) -> Bypasses waterfall, exits loop
          │       ├── Step 1: Room Rent Pro-Rata (Tool 2) (Itemised component check)
          │       ├── Step 2: Prolonged Hospitalization Penalty (hours > 168)
          │       ├── Step 3: HeadsUp & Tiered Network Penalties
          │       ├── Step 4: Annual Aggregate Deductible (Tool 4)
          │       ├── Step 5: Stacked Co-Payment (Tool 3)
          │       └── Step 6: Sum Insured Waterfall (Tool 5)
          │
          └──► Gate 7: State Update
                  └── Persists SI balances, Forever trigger, Lock the Clock age unlock, deductible YTD
```

### The Three-Tier System Layering

The engine is partitioned into three distinct operational layers:

1. **Schema Layer (`schemas.py`)**: Defines Pydantic v2 data models for claim contexts, line items, and decision payloads, providing structural validation at execution boundaries.
2. **Knowledge and Rules Layer (`product_memory.py`, `planner.py`)**: Resolves rules, parses tables (e.g. room category co-payments, specified disease wait lists), constructs the dependency-ordered execution graph, and performs topological sorting.
3. **Execution Layer (`pipeline.py`, `calculators.py`, `semantic_agent.py`)**: Runs validation logic, routes unstructured checks to the semantic agent, invokes pure mathematical calculators, and updates policy/member lifetime state.

---

## 2. Core Components

### 2.1 ProductMemoryStore

The `ProductMemoryStore` class manages the dynamic ingestion, indexing, and resolution of versioned insurance rules and configuration tables extracted from the product definition files.
- **Version-Aware Caching**: Stores parsed rule packages in `_product_memory_cache` keyed by the product version string. Paths are mapped via `_version_to_path`. The method `get_product_memory(version)` retrieves or instantiates the requested configuration, allowing multi-version claims processing without cross-version contamination.
- **Configuration Tables**: Resolves data tables directly, including `R3_TBL_005` (Room Category Co-payment schedules) and `R3_TBL_007` (Specified Disease/Procedure Waiting List).
- **Mutual Exclusivity Constraints**: Ingests rules prohibiting co-existence of contradictory benefits (e.g., active Deductible alongside Co-payment, or overlapping borderless riders).
- **Longest-Match Room Copay Resolution**: Evaluates room categories via `get_room_copay_percent` using a longest-match strategy. This avoids incorrect categorization (e.g. matching "Semi-Private Room" to "Private Room" limits) by ranking match lengths when looking up the category against the copay schedule.

### 2.2 AIPlanner and DAGBuilder

The `AIPlanner` uses the active rule configurations to construct a custom execution plan for each line item.
- **DAG Construction**: The `DAGBuilder` maps rules as nodes and dependencies as directed edges.
- **Kahn's Algorithm with Priority-Queue Ordering**: Performs a topological sort using a min-heap. Nodes with zero in-degrees are pushed into the heap and sorted using a priority tuple returned by `get_rule_priority_tuple(rule_id)`. The priority tuple is defined as:
  $$\text{Priority Tuple} = (\text{Gate Order Value},\ \text{Rule Priority},\ \text{Rule ID})$$
  This sequence ensures that rules execute in strict gate sequence (Gate 1 through Gate 7) and priority order, producing a deterministic execution path.
- **Cycle Detection Fallback**: If the sorted rules list size is less than the total node count, a cycle is detected. The planner logs a warning and falls back to a priority-based sorting sequence to guarantee execution continuity.

### 2.3 Pure Mathematical Calculators

Calculators are stateless, pure functions implemented in `src/calculators.py`. They do not access external systems or reference global states, taking all configuration and data variables through their input arguments.

1. **Tool 1 (`calculate_waiting_period`)**: Computes elapsed time since policy inception. It checks for accident day-1 exemptions, standard 30-day initial waiting periods, 24-month specified disease wait lists (evaluated against `R3_TBL_007`), 36-month pre-existing disease (PED) waits, and capped 48-month insurer-imposed personal waiting periods. It incorporates continuous coverage credits and portability adjustments.
2. **Tool 2 (`calculate_room_pro_rata`)**: Calculates the pro-rata ratio if room limits are breached:
   $$\text{Pro-rata Ratio} = \frac{\text{Eligible Room Rent Limit}}{\text{Actual Room Rent Claimed}}$$
   Applies this ratio to associated medical expenses (room charges, nursing charges, medical practitioner fees, and operation theatre charges).
3. **Tool 3 (`calculate_copayment`)**: Stacks base co-payments with optional penalties (HeadsUp breach, Tiered Network breach, prolonged hospitalization, and room category co-payment), applying the accumulated copay percentage to the admissible amount while respecting benefit-level exemptions.
4. **Tool 4 (`calculate_deductible`)**: Tracks and applies annual aggregate deductibles, deducting the admissible amount down to the remaining deductible balance and updating the year-to-date (YTD) consumed tracker.
5. **Tool 5 (`calculate_si_waterfall`)**: Distributes claim payouts across available financial pools: Base Sum Insured, Booster+ Sum Insured, and the ReAssure Forever pool, adjusting remaining balances in-place.
6. **Tool 6 (`calculate_lock_the_clock`)**: Locks premium age. For multi-tenure policies, if a claim is paid, it evaluates premium delta metrics and returns `age_unlocked` as `True` to trigger downstream lock-the-clock state updates.
7. **Tool 7 (`calculate_booster_accumulation`)**: Increases Booster+ SI by the unused portion of Base SI at renewal (capped at a multiplier) or proportionally reduces it if the Base SI is downgraded.
8. **Tool 8 (`validate_pre_post_hosp_window`)**: Validates pre-hospitalization (60 days) and post-hospitalization (180 days) windows and ensures condition-relation checks pass.
9. **Tool 9 (`calculate_hospital_daily_cash`)**: Computes daily cash benefits based on hospitalization hours, capped at 30 days annually per policy schedule.
10. **Tool 10 (`calculate_personal_accident_benefit`)**: Computes payouts for accidental death (AD), permanent total disability (PTD), and permanent partial disability (PPD) based on a longest-match injury table lookup, bypassing co-payments and deductibles.

### 2.4 Hybrid & Semantic Reasoning Agent

For rules requiring textual or clinical interpretation rather than logical validation, the engine routes prompts to the `SemanticExecutionAgent`.
- **Three LLM Provider Paths**:
  - `mock`: Simulates responses with predefined confidence scores for testing and validation.
  - `local`: Communicates with a local llama.cpp server hosting quantized instruction-tuned models.
  - `openai`: Utilizes the OpenAI chat completions API.
- **GBNF Grammar Constraints**: For local llama.cpp completions, a GBNF grammar constraints token sampling to ensure the model responds with the structured `SemanticAdjudicationPayload` JSON format, eliminating markdown wrapping and schema hallucinations.
- **Split Timeouts**: Uses `http.client` directly to implement a 5-second connection timeout (for fast fail detection if the server is offline) and a 120-second read timeout (allowing the LLM to complete generation without stalling the runtime).
- **Three-Tier Confidence Routing**:
  - Confidence $\ge 0.90$: Auto-decision applies.
  - $0.70 \le \text{Confidence} < 0.90$: Auto-routes to `ASSISTED_REVIEW` with pre-populated suggestions.
  - Confidence $< 0.70$: Auto-routes to `PENDING_REVIEW` for manual adjudication.
- **Structured Payload Schema**: The agent returns a `SemanticAdjudicationPayload` with the fields:
  - `evaluation_status`: `"PASSED"`, `"EXCLUSION_ACTIVE"`, or `"FAILED"`
  - `reasoning_trace`: String detailing clinical and policy matching details
  - `confidence_score`: Float value between 0.0 and 1.0

---

## 3. Gate-by-Gate Specification

### Gate 1 — Policy Validation
Checks basic policy status and parameters:
- **Status**: Verifies that `context.policy.status` is exactly `"Active"`.
- **Premium Payment**: Verifies that `context.policy.premium_paid` is `True` or `context.policy.grace_period_active` is `True`.
- **Policy Date Range Check**: Verifies that the claim date (admission date, falling back to expense date) is within policy coverage bounds using `_coerce_to_date`:
  $$\text{Policy Start Date} \le \text{Claim Date} \le \text{Policy End Date}$$
  Violations return a `FAILED` trace with `rule_id="GATE_1_DATE_RANGE"`.

### Gate 2 — Member Validation
Validates claimant eligibility:
- **Eligibility**: Verifies that `context.member.eligibility_active` is `True`.
- **Addition Date Check**: Checks if the claim's admission/expense date is on or after the member's addition date:
  $$\text{Claim Date} \ge \text{Member Addition Date}$$
  Violations return a `FAILED` trace with `rule_id="GATE_2_ADDITION_DATE"`.

### Gate 3 — Coverage Validation
Ensures the claimed benefit bucket is covered:
- **Variant Filtering**: Matches the claimed benefit against product configurations to check if it is supported under the active variant (`Classic`, `Select`, or `Elite`).
- **Optional Rider Opt-In**: Verifies if optional benefits (HeadsUp, Tiered Network, Borderless, Borderless Specific Illness) are active in the policy payload before processing claims under these riders.
- **Precondition Verification**: For "Home Care / Domiciliary Treatment", the system enforces three positive flags: `doctor_advised`, `continuous_treatment`, and `daily_monitoring_chart`. If any are missing, the claim is rejected.

### Gate 4 — Waiting Period Validation
Checks if waiting periods are cleared:
- **Accident Day-1 Exemption**: If `accident_related` is `True`, all waiting periods are bypassed.
- **Initial 30-Day waiting period**: Enforced if continuous coverage is less than 12 months.
- **Specified Disease (24 Months)**: Evaluated using list entries from `R3_TBL_007`. A case-insensitive matcher checks if the diagnosis text overlaps with table entries.
- **Pre-Existing Disease (PED) (36 Months)**: Applied if matching conditions exist in `context.member.ped_declarations`.
- **Personal Waiting Period (R3_EXCL_017)**: Checked against `context.policy.personal_waiting_period_months` (capped at 48 months).
- **Portability Credits**: Reduces active waiting periods by `waiting_period_credit_months` if continuous coverage is verified.

### Gate 5 — Exclusion Validation
Filters out non-covered conditions:
- **Deterministic Exclusions**: Excludes treatments like dental procedures (`R3_EXCL_020`) unless caused by an accident.
- **Semantic Exclusions**: Leverages the Semantic Agent to analyze diagnoses (e.g., cosmetic surgery, diagnostics-only admissions).
- **Safety Wrappers**: If semantic evaluation yields confidence below $0.90$, or if the LLM request encounters a network error, the pipeline flags the rule as low-confidence and routes the claim to manual review without hard rejection.

### Gate 6 — Financial Computation
Calculates financial payouts sequentially:

#### Step 0: Non-Payable Items Removal (Annexure)
Checks the claim description against the non-payable keywords list. Uses regex word-boundary matching to prevent false positives. If matched, the item's admissible amount is set to 0.0 immediately.

#### Step 0.5: Modern Treatment Sub-limit (R3_BEN_005)
If the claimed bucket is `"Expenses during Hospitalization"` and matches modern treatment keywords, a 50% base SI sub-limit applies. This limit is bypassed if `modern_treatments_plus_opted` is `True`.

#### Special Benefit Branches
- **Hospital Daily Cash (Tool 9)**: Applies daily cash rates up to a 30-day annual cap. It is exempt from co-payments and deductibles and exits the financial pipeline immediately after calculation.
- **Personal Accident (Tool 10)**: Looks up injury descriptions in the PA payout table. It is exempt from co-payments and deductibles and exits the financial pipeline immediately after calculation.

#### Step 1: Room Pro-Rata (Tool 2)
If `actual_room_rent > 0` and exceeds `context.policy.room_rent_limit`, a pro-rata deduction is calculated. This step requires itemized room rent expense components. If they are missing, the claim is routed to `ASSISTED_REVIEW`.

#### Step 2: Prolonged Hospitalization Flag
If `hospitalization_hours > 168`, a 10% co-payment penalty flag is set.

#### Step 3: HeadsUp & Tiered Network Penalty Flags
Applies a 20% co-payment penalty flag for late intimation (HeadsUp) or if a non-recommended tiered network provider was used.

#### Step 4: Annual Aggregate Deductible (Tool 4)
If an annual deductible is active, it reduces the admissible amount by the remaining deductible balance and updates the YTD consumed tracker.

#### Step 5: Stacked Co-Payment (Tool 3)
Calculates and applies stacked co-payments to non-exempt benefits:
$$\text{Total Co-pay \%} = \text{Base Co-pay \%} + \text{HeadsUp Penalty (20\%)} + \text{Tiered Network Penalty (20\%)} + \text{Prolonged Hosp Penalty (10\%)} + \text{Room Category Co-pay \%}$$
$$\text{Co-pay Amount} = \text{Admissible Amount} \times \text{Total Co-pay \%}$$
$$\text{Payable Amount} = \text{Admissible Amount} - \text{Co-pay Amount}$$

#### Step 6: Sum Insured Waterfall (Tool 5)
Resolves the payable amount against available pools: Base Sum Insured $\to$ Booster+ Sum Insured $\to$ ReAssure Forever pool.
Let $A$ be the payable amount after co-payment. Let $S_{\text{base}}$, $S_{\text{booster}}$, and $S_{\text{forever}}$ be the remaining balances of Base SI, Booster+, and ReAssure Forever pools respectively.
$$\text{Consumption}_{\text{base}} = \min(A,\ S_{\text{base}})$$
$$A_{1} = A - \text{Consumption}_{\text{base}}$$
$$\text{Consumption}_{\text{booster}} = \min(A_{1},\ S_{\text{booster}})$$
$$A_{2} = A_{1} - \text{Consumption}_{\text{booster}}$$
$$\text{Consumption}_{\text{forever}} = \min(A_{2},\ S_{\text{forever}})$$
$$\text{Shortfall} = A_{2} - \text{Consumption}_{\text{forever}}$$
$$\text{Total Payout} = \text{Consumption}_{\text{base}} + \text{Consumption}_{\text{booster}} + \text{Consumption}_{\text{forever}}$$

### Gate 7 — State Update
Persists updated financial balances back to the claim context:
- **Sum Insured**: Deducts consumed amounts from `base_si_remaining`, `booster_plus_remaining`, and `reassure_forever_pool`.
- **ReAssure Forever Trigger**: Sets `reassure_forever_triggered = True` on the first paid claim.
- **Lock the Clock**: Sets `lock_the_clock_unlocked_date` to the current UTC timestamp if `state.lock_the_clock_age_unlocked` is `True`.
- **Deductible YTD**: Updates `deductible_consumed_ytd` with the deductible amount applied this claim.
- **Cash-Bag+**: Documented as `NOT_IMPLEMENTED` pending wallet credit rules.

---

## 4. Cross-Cutting Concerns

### 4.1 Endorsement Processing
Endorsements represent mid-term adjustments to policy terms. The pipeline applies all endorsements whose effective date is on or before the claim's earliest admission/expense date:
- **Ordering**: Sorted and processed in ascending chronological order (`effective_date`).
- **Mutation**: Mutates the active `ClaimContext.policy` and `ClaimContext.member` objects in-place.
- **Future Endorsements**: Skips endorsements with effective dates after the claim event date.
- **Assisted Review Routing**: Complex structure changes (`MemberDeletion`, `IndividualToFloater`, and `FloaterSplit`) reduce the pipeline confidence score, routing the claim to `ASSISTED_REVIEW`.

### 4.2 Mutual Exclusivity Validation
Mutual exclusivity validation runs at two levels:
1. **Claim Level**: Runs before processing any line items. If a violation is found, the pipeline stops execution and routes the claim to `PENDING_REVIEW`.
2. **Line Item Level**: Runs before the financial computation of each line item. Violations set the item status to `PENDING_REVIEW` with zero admissible/payable amounts, preventing conflicting financial calculations.

### 4.3 Three-Tier Confidence Routing
Adjudication results are routed based on the aggregate confidence score:
- **Auto-Decision** ($\text{Confidence} \ge 0.90$): The decision is automatically finalized.
- **Assisted Review** ($0.70 \le \text{Confidence} < 0.90$): Routes the claim to `ASSISTED_REVIEW` with pre-populated suggestions for the human operator.
- **Pending Review** ($\text{Confidence} < 0.70$): Bypasses auto-decisions and routes the claim to `PENDING_REVIEW` for manual adjudication.

### 4.4 Async Execution Path
The pipeline implements an asynchronous method, `adjudicate_claim_async`:
- **DAG Layering**: Groups rules by dependency depth layer.
- **Concurrent Processing**: Executes all independent rules within the same depth layer concurrently using `asyncio.gather`.
- **Non-blocking LLM calls**: Wraps blocking LLM calls in thread pools using `asyncio.to_thread` to prevent stalling the main event loop.

### 4.5 PAS Submission Payload
Constructed in `_compose_claim_decision`. The payload contains the complete structured decision record, including line-item resolutions, traces, applied tools, and calculated ratios, allowing external systems to perform independent revalidation.

### 4.6 Version-Aware Rule Ingestion
The system matches the `product_json_version` field on the incoming `ClaimContext` to load the corresponding rules and tables from memory. Versioned extraction files follow the naming convention:
$$\text{Product and Policy Rules Extraction\_<version>.txt}$$
This ensures that historical policies are adjudicated against their original rules rather than subsequent revisions.

---

## 5. Engineering Decisions

### 5.1 Decoupled Stateless Calculators
- **Decision**: Calculators are designed as pure Python functions, accepting configuration parameters (like specified disease lists or exempt benefits) directly through their function signatures.
- **Rationale**: This keeps mathematical logic separate from database access or rule-parsing components, making the codebase easier to debug, test, and adapt to changing specifications.

### 5.2 Timezone Coercion and Normalization
- **Decision**: The system enforces datetime validation by coercing naive and aware timestamps into timezone-aware UTC objects at execution boundaries.
- **Rationale**: Mixing offset-naive and offset-aware datetimes in Python causes runtime `TypeErrors`. Handling this at the pipeline boundary ensures calculations in the core engine remain robust.

### 5.3 Room Rent Co-pay Keywords Matcher
- **Decision**: Implemented a longest-match keyword resolution strategy for variant-specific room co-payments.
- **Rationale**: A basic substring check would flag "semi-private" as "private", leading to incorrect co-payments. Checking the longest match ensures the correct room category co-payment is applied.

### 5.4 Word-Boundary Regex for Non-Payable Matching
- **Decision**: Substring matches are restricted using word boundaries:
  $$\text{Regex Pattern} = \text{r'(?<!\textbackslash w)'} + \text{escaped\_keyword} + \text{r'(?!\textbackslash w)'}$$
- **Rationale**: Standard substring matching causes false positives. For example, matching "administration" would flag clinical descriptions like "administration of anaesthesia" as a non-payable administrative fee. Word boundary matching ensures only exact term matches are caught.

### 5.5 Itemised Expense Component Requirement for Pro-Rata
- **Decision**: The pro-rata calculation is blocked and the claim routed to `ASSISTED_REVIEW` if the itemized bill components (room charges, nursing, medical practitioner fees, OT charges) are not populated.
- **Rationale**: Using the total claimed amount as a proxy when room limits are breached results in inaccurate deductions. Requiring itemized components ensures calculations remain compliant with policy guidelines.

### 5.6 Dedicated Benefit Branches in Gate 6
- **Decision**: Hospital Daily Cash (Tool 9) and Personal Accident (Tool 10) claims exit the financial computation loop early.
- **Rationale**: Both benefits are co-pay and deductible exempt, and their payouts are calculated using fixed schedules rather than SI waterfall allocations. Handling them in early branches prevents unnecessary waterfall calculations and incorrect deductions.

### 5.7 Split Connect/Read Timeouts for Local LLM
- **Decision**: Uses `http.client` directly with a 5-second socket connection timeout and a 120-second read timeout.
- **Rationale**: Standard library options like `urllib` only support a single timeout covering both phases. Splitting timeouts allows the system to fail fast if the local server is offline while giving the model sufficient time to generate responses.

### 5.8 GBNF Grammar Constraint
- **Decision**: Constrains the llama.cpp token sampler using a custom GBNF grammar.
- **Rationale**: Local models can fail to output structured JSON even when prompted. GBNF grammar forces the output to match the `SemanticAdjudicationPayload` schema, preventing JSON parsing errors and token hallucinations.
