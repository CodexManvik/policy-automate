# ReAssure 3.0 Claims Auto-Adjudication Engine

**Phase 1: Core Execution Engine with Deterministic Calculators**

## Overview

AI-First Constrained Execution system for automated health insurance claims adjudication implementing the ReAssure 3.0 product rules.

### Architecture Principles

1. **Structured Memory, Not Learned Behavior** - Product JSON is single source of truth
2. **Deterministic Sequencing** - Rule execution order defined by dependency graphs
3. **Tools for Math, AI for Reasoning** - All arithmetic goes through calculator tools
4. **Stateful Across Claims** - Lifetime flags and balances persist beyond individual claims
5. **Fail-Safe, Not Fail-Open** - Missing data routes to manual review
6. **Auditable to the Clause** - Every decision traces back to rule_id and section_ref
7. **PAS is Final Authority** - AI recommends; PAS validates

## System Components

### Phase 1 Deliverables

```
src/
├── schemas.py          # Pydantic v2 data models (Input/Output)
├── calculators.py      # 8 Deterministic calculation tools
├── pipeline.py         # 7-Gate sequential execution engine
└── example_usage.py    # Demo and testing script
```

## Installation

### Prerequisites

- Python 3.11+
- pip or uv package manager

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Or using uv (recommended)
uv pip install -r requirements.txt
```

## Usage

### Basic Example

```python
from datetime import datetime
from schemas import ClaimContext, PolicyData, MemberData, LineItemData
from pipeline import ClaimsAdjudicationPipeline

# Create claim context (assembled from APIs)
context = ClaimContext(
    claim_id="CLM-2025-001",
    claim_received_at=datetime.utcnow(),
    policy=PolicyData(...),
    member=MemberData(...),
    line_items=[LineItemData(...)]
)

# Initialize pipeline
pipeline = ClaimsAdjudicationPipeline()

# Execute adjudication
decision = pipeline.adjudicate_claim(context)

print(f"Decision: {decision.claim_decision}")
print(f"Payable: ₹{decision.total_payable:,.2f}")
```

### Run Demo

```bash
cd src
python example_usage.py
```

## 7-Gate Execution Sequence

The pipeline processes each claim through 7 sequential gates:

1. **Policy Validation** - Policy active? Premium paid? Not lapsed?
2. **Member Validation** - Member exists? Eligible? Age valid?
3. **Coverage Validation** - Benefit bucket covered? Variant supports?
4. **Waiting Period Validation** - PED/Specific Disease/Initial waits cleared?
5. **Exclusion Validation** - Standard exclusions (Excl01-Excl18) checked?
6. **Financial Computation** - Deductions applied in correct sequence
7. **State Update** - Update SI balances and lifetime flags

### Gate 6: Financial Computation Sequence

**Critical Ordering** (from Technical Design Section 6.2):

1. Room Pro-Rata (Tool 2) - **Must execute first**
2. Prolonged Hospitalization Penalty (10% if >7 days without notice)
3. HeadsUp/Tiered Network Penalties (20% each)
4. Annual Aggregate Deductible (Tool 4) - **Before co-payment**
5. Co-Payment (Tool 3) - **Stacks all penalties**
6. SI Waterfall (Tool 5) - **Final step**

## Calculation Tools

### Tool 1: Waiting Period Calculator
- PED: 36 months (reduced by portability)
- Specified Disease: 24 months (except Accident day-1, Cancer 30-day)
- Initial Wait: 30 days (except Accident)

### Tool 2: Room Pro-Rata Calculator
```
IF actual_room_rent > eligible_room_rent:
    ratio = eligible / actual
    payable = ratio × associated_medical_expenses
```

### Tool 3: Co-Payment Calculator (Stacking)
```
total_copay = base_copay + heads_up_penalty + tiered_penalty 
              + prolonged_hosp_penalty + room_copay
```

### Tool 4: Deductible Calculator
```
deductible_this_claim = min(claim_amount, remaining_deductible)
payable = claim_amount - deductible_this_claim
```

### Tool 5: SI Waterfall Calculator
```
Step 1: Draw from Base SI
Step 2: Draw from Booster+
Step 3: Draw from ReAssure Forever (if triggered)
```

### Tool 6: Lock the Clock Calculator
Age locked at entry until first claim paid

### Tool 7: Booster+ Accumulation Calculator
Unutilized Base SI carries forward (max 10x for Elite)

### Tool 8: Pre/Post Hospitalization Window Validator
Pre: 60 days before admission
Post: 180 days after discharge

## Data Models

### Input: ClaimContext
```python
ClaimContext(
    claim_id: str
    policy: PolicyData          # From Policy API
    member: MemberData          # From Member API
    history: ClaimsHistoryData  # From Claims History API
    porting: PortingMigrationData
    network: NetworkData
    benefit_balance: BenefitBalanceData
    lifetime_state: LifetimeStateData
    line_items: List[LineItemData]
)
```

### Output: ClaimDecision
```python
ClaimDecision(
    claim_decision: "APPROVED" | "PARTIALLY_APPROVED" | "REJECTED"
    total_claimed: float
    total_payable: float
    deduction_breakdown: DeductionBreakdown
    si_waterfall_breakdown: SIWaterfallBreakdown
    line_items: List[LineItemDecision]
    decision_trace: List[DecisionTrace]  # Full audit trail
    confidence_score: float
)
```

## Key Features

✅ **Deterministic Calculations** - All math in pure Python functions
✅ **Full Audit Trail** - Every decision traces to rule_id and section_ref
✅ **Stateful Processing** - Tracks lifetime flags and accumulated balances
✅ **Mutual Exclusivity** - Co-payment and Deductible cannot coexist
✅ **Penalty Stacking** - Co-payment properly stacks multiple penalties
✅ **Room Pro-Rata** - Associated Medical Expenses correctly calculated
✅ **SI Waterfall** - Sequential draw from Base → Booster+ → Forever

## Next Steps (Phase 2+)

- [ ] AI Planner for rule selection and DAG construction
- [ ] AI Execution Agent with tool orchestration
- [ ] Product Memory Store with versioned JSON
- [ ] Context Builder API integrations
- [ ] PAS reconciliation service
- [ ] Confidence scoring model
- [ ] Manual review routing

## Testing

```bash
# Run example
python src/example_usage.py

# Expected output:
# - Claim decision summary
# - Financial breakdown
# - Deduction details
# - SI waterfall consumption
# - Full audit trace
# - JSON export
```

## Reference Documents

- `Product_and_Policy_Solution_Documents.md` - Technical architecture
- `Product and Policy Rules Extraction.txt` - Rule blueprints (JSON)
- `ReAssure30_Policy_Wordings.md` - Policy terms and conditions

## Version

**Phase 1: v1.0.0**
- Core schemas implemented
- 8 calculation tools operational
- 7-gate pipeline functional
- No AI/LLM calls in Phase 1

---

**Product:** ReAssure 3.0  
**UIN:** NBHHLIP26047V012526  
**Company:** Niva Bupa Health Insurance
