# Quick Start Guide

## Phase 1: Core Execution Engine - Get Running in 5 Minutes

### Step 1: Install Dependencies (1 minute)

```bash
# Navigate to project directory
cd "c:\Project\nivabupa\policy automate"

# Install Python dependencies
pip install -r requirements.txt
```

**Requirements:**
- Python 3.11+
- pydantic>=2.5.0
- python-dateutil>=2.8.2

### Step 2: Run Calculator Tests (1 minute)

```bash
# Verify all calculation tools work correctly
python src/test_calculators.py
```

**Expected Output:**
```
======================================================================
Running Calculator Unit Tests
======================================================================

✓ Test passed: Room pro-rata (no breach)
✓ Test passed: Room pro-rata (with breach)
✓ Test passed: Co-payment (base only)
✓ Test passed: Co-payment (stacking penalties)
✓ Test passed: Deductible (partial consumption)
✓ Test passed: Deductible (exhausted)
✓ Test passed: SI Waterfall (base only)
✓ Test passed: SI Waterfall (base + booster)
✓ Test passed: SI Waterfall (all pools)
✓ Test passed: Waiting period (accident exemption)
✓ Test passed: Waiting period (initial 30 days)
✓ Test passed: Pre/Post window (pre-eligible)
✓ Test passed: Pre/Post window (post-eligible)

======================================================================
Test Results: 13 passed, 0 failed
======================================================================
```

### Step 3: Run Full Pipeline Demo (2 minutes)

```bash
# Execute complete adjudication pipeline with sample claim
python src/example_usage.py
```

**Expected Output:**
```
================================================================================
ReAssure 3.0 Claims Auto-Adjudication Engine - Phase 1 Demo
Deterministic Calculation Tools + 7-Gate Pipeline
================================================================================

[1/3] Creating sample claim context...
  ✓ Claim ID: CLM-2025-001234
  ✓ Policy: POL-2025-001 (Select)
  ✓ Member: John Doe (Age 35)
  ✓ Base SI: ₹10,00,000
  ✓ Line Items: 1

[2/3] Initializing adjudication pipeline...
  ✓ Pipeline initialized

[3/3] Executing 7-gate adjudication...
  → Gate 1: Policy Validation
  → Gate 2: Member Validation
  → Gate 3: Coverage Validation
  → Gate 4: Waiting Period Validation
  → Gate 5: Exclusion Validation
  → Gate 6: Financial Computation
    ↳ Tool 2: Room Pro-Rata Calculator
    ↳ Tool 3: Co-Payment Calculator (with stacking)
    ↳ Tool 4: Deductible Calculator
    ↳ Tool 5: SI Waterfall Calculator
  → Gate 7: State Update
  ✓ Adjudication complete in XX.XXms

================================================================================
                    CLAIM ADJUDICATION DECISION: CLM-2025-001234
================================================================================

Overall Decision: PARTIALLY_APPROVED
Confidence Score: 100.00%
Processing Time: XX.XXms

                              FINANCIAL SUMMARY
--------------------------------------------------------------------------------
Total Claimed:     ₹  150,000.00
Total Admissible:  ₹  XXX,XXX.XX
Total Payable:     ₹  XXX,XXX.XX
Total Deductions:  ₹   XX,XXX.XX

[... detailed breakdown ...]

[Optional] Exporting decision to JSON...
  ✓ Decision exported to: claim_decision_output.json

================================================================================
Phase 1 Demo Complete!
================================================================================
```

### Step 4: Explore the Code (1 minute)

**Key Files to Understand:**

1. **`src/schemas.py`** (450 lines)
   - Input: `ClaimContext` - assembled from APIs
   - Output: `ClaimDecision` - full adjudication result
   - All nested models

2. **`src/calculators.py`** (650 lines)
   - 8 pure Python calculation functions
   - No AI, deterministic only
   - Tools 1-8 from technical design

3. **`src/pipeline.py`** (550 lines)
   - `ClaimsAdjudicationPipeline` class
   - 7 sequential gates
   - Financial computation ordering

4. **`src/example_usage.py`** (200 lines)
   - Sample claim creation
   - Pipeline execution
   - Pretty-printed output

## Understanding the Output

### Claim Decision Structure

```json
{
  "claim_id": "CLM-2025-001234",
  "claim_decision": "PARTIALLY_APPROVED",
  "total_claimed": 150000.0,
  "total_payable": 120000.0,
  "deduction_breakdown": {
    "room_pro_rata": 5000.0,
    "co_payment": 15000.0,
    "deductible": 0.0,
    "penalties": 10000.0
  },
  "si_waterfall_breakdown": {
    "amount_from_base_si": 100000.0,
    "amount_from_booster": 20000.0,
    "amount_from_forever": 0.0
  },
  "line_items": [...],
  "decision_trace": [...]
}
```

### Decision Trace (Audit Trail)

Every rule execution is logged:

```json
{
  "step": 1,
  "rule_id": "R3_BEN_004",
  "rule_name": "Room Pro-Rata",
  "gate": "financial_computation",
  "inputs": {"eligible": 3000, "actual": 5000},
  "evaluation": "DEDUCTION_APPLIED",
  "reason": "Pro-rata deduction: ₹5,000",
  "source_section": "6.2.4(d)"
}
```

## Common Use Cases

### Use Case 1: Test a Specific Calculator

```python
from calculators import calculate_copayment

result = calculate_copayment(
    admissible_amount=100000.0,
    base_copay_percent=0.20,
    benefit_bucket="Expenses during Hospitalization",
    heads_up_penalty=True,
    tiered_network_penalty=False,
    prolonged_hosp_penalty=True
)

print(f"Total Co-pay: ₹{result.copay_amount:,.2f}")
print(f"Payable: ₹{result.payable_amount:,.2f}")
print(f"Breakdown: {result.copay_breakdown}")
```

### Use Case 2: Create Custom Claim

```python
from datetime import datetime
from schemas import *
from pipeline import ClaimsAdjudicationPipeline

# Build your claim context
context = ClaimContext(
    claim_id="CLM-TEST-001",
    claim_received_at=datetime.utcnow(),
    policy=PolicyData(...),
    member=MemberData(...),
    # ... other required fields
    line_items=[
        LineItemData(
            line_item_id="LI-001",
            claimed_amount=200000.0,
            # ... other fields
        )
    ]
)

# Run adjudication
pipeline = ClaimsAdjudicationPipeline()
decision = pipeline.adjudicate_claim(context)

# Access results
print(decision.claim_decision)
print(f"Payable: ₹{decision.total_payable:,.2f}")
```

### Use Case 3: Inspect Specific Deductions

```python
for line_item in decision.line_items:
    print(f"\nLine Item: {line_item.line_item_id}")
    for deduction in line_item.deductions:
        print(f"  {deduction.deduction_type}: ₹{deduction.amount:,.2f}")
        print(f"  Reason: {deduction.reason}")
        print(f"  Rule: {deduction.rule_id}")
```

## Troubleshooting

### Issue: Import errors

**Solution:** Make sure you're in the right directory and packages are installed

```bash
cd "c:\Project\nivabupa\policy automate"
pip install -r requirements.txt
```

### Issue: Pydantic validation errors

**Solution:** Check your input data matches schema requirements

```python
# All required fields must be provided
# Use Pydantic's validation to catch issues early
try:
    context = ClaimContext(...)
except ValidationError as e:
    print(e.json())
```

### Issue: Calculator returns unexpected results

**Solution:** Review the input parameters and logic in `calculators.py`

```python
# All calculators have detailed docstrings
help(calculate_room_pro_rata)

# Check the calculation logic directly in calculators.py
# All formulas are documented with section references
```

## Next Steps

1. **Understand the Architecture**
   - Read `Product_and_Policy_Solution_Documents.md`
   - Review Section 4.5 (Calculation Tools)
   - Review Section 5 (Gate Execution Sequence)

2. **Explore the Data Models**
   - Open `src/schemas.py`
   - Study `ClaimContext` structure
   - Understand `ClaimDecision` output

3. **Modify the Example**
   - Edit `src/example_usage.py`
   - Change claim amounts, room categories, co-pay settings
   - See how decisions change

4. **Add Custom Logic**
   - Phase 2 will add AI components
   - You can extend calculators for new rules
   - Follow the same pure function pattern

## Questions?

Refer to:
- `README.md` - Full documentation
- `PROJECT_STRUCTURE.md` - Code organization
- Technical design documents in root folder

---

**Ready for Production?** Phase 1 provides the deterministic calculation engine. Phase 2+ will add AI reasoning, product JSON parsing, and full API integration.
