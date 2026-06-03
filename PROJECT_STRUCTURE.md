# Project Structure

```
policy automate/
│
├── README.md                          # Project overview and documentation
├── PROJECT_STRUCTURE.md               # This file
├── requirements.txt                   # Python dependencies
│
├── Product_and_Policy_Solution_Documents.md   # Technical architecture spec
├── Product and Policy Rules Extraction.txt    # Rule blueprints (JSON)
├── ReAssure30_Policy_Wordings.md              # Policy terms document
│
└── src/                               # Source code
    ├── __init__.py                    # Package initialization
    │
    ├── schemas.py                     # Pydantic v2 data models
    │   ├── ClaimContext (Input)
    │   │   ├── PolicyData
    │   │   ├── MemberData
    │   │   ├── ClaimsHistoryData
    │   │   ├── PortingMigrationData
    │   │   ├── NetworkData
    │   │   ├── BenefitBalanceData
    │   │   ├── LifetimeStateData
    │   │   ├── EndorsementData
    │   │   └── LineItemData
    │   │
    │   ├── ClaimDecision (Output)
    │   │   ├── LineItemDecision
    │   │   ├── DeductionDetail
    │   │   ├── DecisionTrace
    │   │   ├── DeductionBreakdown
    │   │   └── SIWaterfallBreakdown
    │   │
    │   └── PerClaimState (Runtime)
    │
    ├── calculators.py                 # 8 Deterministic calculation tools
    │   ├── Tool 1: calculate_waiting_period()
    │   ├── Tool 2: calculate_room_pro_rata()
    │   ├── Tool 3: calculate_copayment()
    │   ├── Tool 4: calculate_deductible()
    │   ├── Tool 5: calculate_si_waterfall()
    │   ├── Tool 6: calculate_lock_the_clock()
    │   ├── Tool 7: calculate_booster_accumulation()
    │   └── Tool 8: validate_pre_post_hosp_window()
    │
    ├── pipeline.py                    # 7-Gate execution pipeline
    │   └── ClaimsAdjudicationPipeline
    │       ├── Gate 1: _gate_1_policy_validation()
    │       ├── Gate 2: _gate_2_member_validation()
    │       ├── Gate 3: _gate_3_coverage_validation()
    │       ├── Gate 4: _gate_4_waiting_period_validation()
    │       ├── Gate 5: _gate_5_exclusion_validation()
    │       ├── Gate 6: _gate_6_financial_computation()
    │       │   ├── Step 1: Room Pro-Rata
    │       │   ├── Step 2: Prolonged Hosp Penalty
    │       │   ├── Step 3: HeadsUp/Tiered Penalties
    │       │   ├── Step 4: Deductible
    │       │   ├── Step 5: Co-Payment (stacked)
    │       │   └── Step 6: SI Waterfall
    │       └── Gate 7: State Update (placeholder)
    │
    ├── example_usage.py               # Demo script
    │   ├── create_sample_claim_context()
    │   ├── print_decision_summary()
    │   └── main()
    │
    └── test_calculators.py            # Unit tests
        └── 13 test functions for all calculators
```

## Key Design Patterns

### 1. Data Flow
```
External APIs → ClaimContext → Pipeline → ClaimDecision → PAS
```

### 2. Gate Execution
```
Sequential gates, fail-fast on rejection
Each gate: validation → trace → state update → next gate
```

### 3. Financial Computation Order
```
CRITICAL: Must follow exact sequence from Technical Design
Room Pro-Rata → Penalties → Deductible → Co-Pay → SI Waterfall
```

### 4. Calculator Pattern
```python
# All calculators return dataclass results
# Pure functions, no side effects
# Type-safe inputs and outputs

def calculate_xxx(
    input1: type,
    input2: type,
    ...
) -> XxxResult:
    # Deterministic logic
    return XxxResult(...)
```

### 5. State Management
```
Per-Claim State: Lives during single claim processing
Lifetime State: Persists across claims and renewals
```

## File Sizes (Approximate)

- schemas.py: ~450 lines
- calculators.py: ~650 lines  
- pipeline.py: ~550 lines
- example_usage.py: ~200 lines
- test_calculators.py: ~400 lines

**Total: ~2,250 lines of Python code**

## Execution Flow

```
1. Assemble ClaimContext from external APIs
2. Initialize ClaimsAdjudicationPipeline()
3. Call pipeline.adjudicate_claim(context)
4. Pipeline processes each line item:
   a. Gate 1-5: Validations (fail-fast)
   b. Gate 6: Financial computation (deterministic tools)
   c. Compose LineItemDecision
5. Aggregate all line items into ClaimDecision
6. Return decision with full audit trail
```

## Testing Strategy

```bash
# Run calculator unit tests
python src/test_calculators.py

# Run full pipeline demo
python src/example_usage.py
```

## Phase 1 Constraints

✅ **Implemented:**
- Complete data models (input/output)
- All 8 calculation tools
- 7-gate sequential pipeline
- Full audit trail generation
- Deterministic execution

❌ **Not in Phase 1:**
- AI/LLM calls
- Product JSON parsing
- API integrations
- PAS reconciliation
- Confidence scoring model
- Manual review routing

## Next Phase Requirements

**Phase 2 will add:**
1. AI Planner (rule selection, DAG construction)
2. AI Execution Agent (tool orchestration)
3. Product Memory Store (versioned JSON)
4. LLM integration (semantic reasoning)
5. Confidence scoring
6. Manual review routing logic

---

**Version:** Phase 1 - v1.0.0  
**Last Updated:** 2026-06-02
