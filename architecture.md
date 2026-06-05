# Architecture Documentation: Health Claims Auto-Adjudication Engine

This document provides a technical specification of the Niva-Bupa auto-adjudication engine prototype (ReAssure 3.0). It details the structural design, orchestration flow, validation gates, dynamic rules mapping, and engineering decisions behind the prototype.

---

## 1. Architectural Overview

The system is designed as a dynamic, pipeline-based rule execution graph. It decouples core insurance mathematics (deterministic calculators) from system state, policy wording, and table specifications. 

### Ingestion and Execution Flow
```
[Claim XML/JSON Payload]
          │
          ▼
    [ClaimContext] ────────────────────────┐
          │                                │
          ▼                                ▼
[AI Planner (planner.py)] ──► [ProductMemoryStore (product_memory.py)]
          │                                │ (Loads rules, tables, parameters)
          ▼                                │
[Dynamic execution DAG]                     │
          │                                │
          ▼                                │
[Pipeline Orchestrator (pipeline.py)] ◄────┘
          │
          ├──► Gate 1: Policy Validation (Deterministic)
          ├──► Gate 2: Member Validation (Deterministic)
          ├──► Gate 3: Coverage Validation (Dynamic Filter)
          ├──► Gate 4: Waiting Period Validation (Hybrid)
          ├──► Gate 5: Exclusion Validation (Semantic/Hybrid)
          │       └──► [Semantic Agent (semantic_agent.py)] (Mock LLM)
          │
          ├──► Gate 6: Financial Computation (Sequential Calculators)
          │       ├──► Room Pro-rata
          │       ├──► Prolonged Hosp Penalty
          │       ├──► HeadsUp & Tiered Network Penalties
          │       ├──► Annual Deductible
          │       ├──► stacked Co-payment
          │       └──► SI Waterfall
          │
          └──► Gate 7: State Update (State tracking)
```

The system operates on three distinct layers:
1. **Schema Layer (`schemas.py`)**: Defines input and output Pydantic v2 validation models.
2. **Knowledge and Rules Layer (`product_memory.py`, `planner.py`)**: Dynamically resolves rules, tables (e.g. room category co-payments schedule, specified disease lists), and constructs the dependency-ordered execution graph (DAG).
3. **Execution Layer (`pipeline.py`, `calculators.py`, `semantic_agent.py`)**: Runs validation logic, routes non-structured logic to the semantic agent, runs pure mathematical calculators, and updates policy/member lifetime state.

---

## 2. Core Architectural Components

### 2.1 Dynamic Rules Engine (`product_memory.py`)
Rather than hardcoding rules and parameters within code, the engine is version-aware and driven by a static rules extraction file (`Product and Policy Rules Extraction.txt`).
- **`RuleBlueprint`**: A dataclass mapping each rule's metadata (ID, Gate, variant applicability, depends_on, benefit bucket filters, preconditions, tool mapping, and rule-level exemptions).
- **`ProductMemoryStore`**: Ingests, caches, and indexes rules. It supports:
  - **Dynamic Table Resolving**: Stores tables like `R3_TBL_005` (Annexure V Room Co-payments) and `R3_TBL_007` (Specified Disease lists).
  - **Version-based Caching**: Keeps separate instances of rule sets cached by version string (`R3_v2.1_2025-01-15`) allowing cross-version claims handling.
  - **Longest-Match String Resolution**: Dynamically maps rooms (e.g., "Semi-Private Room") to co-payment schedules using longest-match keywords to prevent false positives (e.g., "private" incorrectly matching "semi-private").

### 2.2 Dependency-Ordered Planner (`planner.py`)
The `AIPlanner` reads rule preconditions and `depends_on` lists from `ProductMemoryStore` to dynamically compile an `ExecutionPlan`. 
- **Topological Sorting**: Builds a Directed Acyclic Graph (DAG) of execution steps. This ensures that rules like waiting period validation execute before coverage validation, and coverage validation executes before financial calculators.
- **Dynamic Routing**: Restructures the execution graph per variant, opting-in optional rules (e.g., HeadsUp, Tiered Network, Borderless) only if selected in the policy payload.

### 2.3 Pure Mathematical Calculators (`calculators.py`)
All calculators are written as stateless Python functions. They take explicit inputs (including custom rules tables/lists retrieved from product memory) and return structured dataclass outputs. This ensures calculators are decoupled, side-effect-free, and easy to unit test.
- **Tool 1 (`calculate_waiting_period`)**: Calculates elapsed time since inception, applying continuous coverage and portability credits. Supports accident day-1 coverage, 30-day initial waiting periods, 24-month specified disease wait lists, 36-month pre-existing disease (PED) waits, and capped 48-month insurer-imposed personal waiting periods.
- **Tool 2 (`calculate_room_pro_rata`)**: Calculates the pro-rata ratio if room limits are breached, applying deductions on associated medical expenses (room charges, nursing charges, practitioner fees, and operation theatre charges).
- **Tool 3 (`calculate_copayment`)**: Stacks base co-payments with optional penalties: HeadsUp breach (+20%), Tiered Network breach (+20%), prolonged hospitalization (+10%), and room category co-payment. It enforces benefit-level co-pay exemptions.
- **Tool 4 (`calculate_deductible`)**: Applies annual aggregate deductibles, tracking year-to-date (YTD) utilization across specified benefit buckets.
- **Tool 5 (`calculate_si_waterfall`)**: Sequentially draws from Base SI, Booster+ SI, and the ReAssure Forever pool.
- **Tool 6 (`calculate_lock_the_clock`)**: Locks premium age. For multi-tenure policies, if a claim is paid, it returns premium delta metrics and triggers downstream logic to flag the claim for manual premium adjustment calculations.
- **Tool 7 (`calculate_booster_accumulation`)**: Increases Booster+ SI by the unused Base SI at renewal (capped at a multiplier) or proportionally reduces it if the Base SI is downgraded.
- **Tool 8 (`validate_pre_post_hosp_window`)**: Validates pre-hospitalization (60 days) and post-hospitalization (180 days) windows and ensures condition-relation checks pass.

### 2.4 Hybrid & Semantic Reasoning Agent (`semantic_agent.py`)
Rules are categorized as:
- **DETERMINISTIC**: Processed solely via mathematical or strict logical calculators.
- **SEMANTIC**: Handled via natural language processing (e.g., verifying if a treatment was cosmetic or diagnostics-only).
- **HYBRID**: First evaluated deterministically. If the confidence is below a defined threshold, the request falls back to the semantic agent.

---

## 3. Policy and Claim Validation Mechanisms

### 3.1 Gate 1: Policy Validation
Policy validation is the first gate in the execution pipeline. It checks fundamental policy properties:
- **Status Validation**: The system verifies that `context.policy.status` is exactly `"Active"`. If the status is "Lapsed", "Suspended", or "Void", the claim is rejected.
- **Premium Check**: Verifies `context.policy.premium_paid` is `True`. If `False`, it checks `context.policy.grace_period_active`. If both are `False`, the gate fails.
- **Date Check**: The system validates that the claim's admission/expense date falls within the `policy_start_date` and `policy_end_date` bounds.

### 3.2 Gate 2: Member Validation
Validates the claimant's identity and contract status:
- **Eligibility Check**: Checks `context.member.eligibility_active` is `True`.
- **Addition Date Check**: Ensures the line item date is equal to or after `context.member.date_of_addition`. If an endorsement of type `"MemberAddition"` is present, the pipeline sets continuous coverage parameters for the member to zero relative to the endorsement date, re-initiating the 30-day initial waiting period for that member.

### 3.3 Gate 3: Coverage Validation
This gate checks if the claimed benefit category is covered:
- **Variant Coverage**: Filters rules in product memory by the active variant (`Classic`, `Select`, or `Elite`) and checks if the benefit category is supported.
- **Preconditions**: Enforces preconditions. For example, "Home Care / Domiciliary Treatment" requires three positive flags: doctor-advised, continuous line of treatment, and daily monitoring chart signed by the doctor. If any are missing, the line item is rejected.
- **Optional Opt-In**: Verifies if optional benefits (e.g., Hospital Daily Cash, Personal Accident) are active in the claim context before granting eligibility.

### 3.4 Gate 4: Waiting Period Validation
Ensures the claim does not fall inside active waiting periods:
- **Accident Day-1 Exemption**: If `accident_flag` is True, it bypasses all waiting periods.
- **Initial 30-Day Period**: Evaluated for non-accident claims if continuous coverage is less than 12 months.
- **Specified Disease List (24 Months)**: Checked using the dynamic list from `R3_TBL_007`. An overlap matcher checks whether condition strings (e.g., `"Cataract Surgery"`) match keywords or phrases in the table.
- **Pre-Existing Disease (PED) (36 Months)**: Evaluated if the claimant has declared PEDs in `context.member.ped_declarations`.
- **Insurer-Imposed Personal Waiting Period**: Checked against `context.policy.personal_waiting_period_months` (capped at 48 months).

### 3.5 Gate 5: Exclusion Validation
Ensures the claimed treatment is not subject to general policy exclusions:
- **Deterministic Exclusions**: Excludes specific treatments deterministically, such as dental treatments (`R3_EXCL_020`) unless caused by an accident.
- **Semantic Exclusions**: Leverages the Semantic Agent to analyze doctor notes and summaries for exclusions like cosmetic surgery (`R3_EXCL_007`) or diagnostics-only admissions (`R3_EXCL_004`).

### 3.6 Gate 6: Financial Computation
This gate applies financial calculations to eligible claims in a strict order:

$$\text{Claimed Amount} \xrightarrow{\text{Step 1: Room Rent Pro-rata}} \text{Admissible Amount} \xrightarrow{\text{Step 2: Penalties}} \text{Amount Post-Penalties} \xrightarrow{\text{Step 3: Deductible}} \text{Amount Post-Deductible} \xrightarrow{\text{Step 4: Co-payment}} \text{Amount Post-Copay} \xrightarrow{\text{Step 5: SI Waterfall}} \text{Payable Payout}$$

1. **Room Pro-Rata**: If the claimed room rent exceeds the variant's room rent limit, a pro-rata ratio is calculated:
   $$\text{Ratio} = \frac{\text{Eligible Room Rent}}{\text{Actual Room Rent}}$$
   The ratio is applied to all associated medical expenses (room charges, nursing, practitioner fees, OT charges).
2. **Prolonged Hospitalization Penalty**: If hospitalization exceeds 7 days (168 hours), a prolonged hospitalization flag is set, which adds a 10% co-payment penalty during the co-payment step.
3. **HeadsUp & Tiered Network Penalties**: Checks if intimation was sent within time windows or if a non-recommended network provider was used. If breached, respective 20% co-payment penalties are flagged.
4. **Aggregate Deductible**: If an annual aggregate deductible is defined, it reduces the admissible amount by the remaining deductible balance and updates the YTD consumed balance. Deductible checks are bypassed for exempt benefits like health check-ups.
5. **Stacked Co-payment**: Accumulates all applicable co-payment fractions:
   $$\text{Total Co-pay \%} = \text{Base Co-pay \%} + \text{HeadsUp Penalty (20\%)} + \text{Tiered Network Penalty (20\%)} + \text{Prolonged Hosp Penalty (10\%)} + \text{Room Category Co-pay \%}$$
   Applying the stacked co-payment:
   $$\text{Co-pay Amount} = \text{Admissible Amount} \times \text{Total Co-pay \%}$$
   $$\text{Payable Amount} = \text{Admissible Amount} - \text{Co-pay Amount}$$
   Exempt benefits are bypassed.
6. **Sum Insured Waterfall**: Resolves the payable amount against available pools: Base Sum Insured -> Booster+ Sum Insured -> ReAssure Forever pool (up to the original base sum insured limit per claim).

---

## 4. Key Engineering Decisions

### 4.1 Decoupled Stateless Calculators
**Decision**: Calculators are designed as pure Python functions, accepting configuration parameters (like specified disease lists or exempt benefits) directly through their function signatures.
**Rationale**: This keeps mathematical logic separate from database access or rule-parsing components, making the codebase easier to debug, test, and adapt to changing specifications.

### 4.2 Timezone Coercion and Normalization
**Decision**: The system enforces datetime validation by coercing naive and aware timestamps into timezone-aware UTC objects at execution boundaries.
**Rationale**: Mixing offset-naive and offset-aware datetimes in Python causes runtime `TypeErrors`. Handling this at the pipeline boundary ensures calculations in the core engine remain robust.

### 4.3 Room Rent Co-pay Keywords Matcher
**Decision**: Implemented a longest-match keyword resolution strategy for variant-specific room co-payments.
**Rationale**: A basic substring check would flag "semi-private" as "private", leading to incorrect co-payments. Checking the longest match ensures the correct room category co-payment is applied.
