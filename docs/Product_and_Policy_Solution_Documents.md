Product and Policy : Technical Solution Document

27 May 2026

14:38

Technical Solution Design

Niva-Bupa Health Insurance | ReAssure 3.0 | Claims 2.0 Program

Claims 2.0 — AI-First Auto-Adjudication: Detailed Solution Design

Product / Policy / Member Validation Layer
Version: 1.0 Product Reference: ReAssure 3.0 (UIN: NBHHLIP26047V012526) Status: Draft for Review

1. Purpose & Scope

This document provides the detailed technical solution design for the Claims 2.0 AI-first auto-adjudication system, specifically for the Product
/ Policy / Member Validation Layer. It builds upon the high-level solution design and addresses implementation-level concerns including:

•

•

•

•

•

•

•

Exact rule execution sequencing and dependency management

Stateful cross-claim and cross-renewal logic

Mid-term endorsement handling

Confidence scoring and routing criteria

Tool contract specifications

Error handling and fall-back paths

Data model for lifetime state, per-claim state, and audit trail

Scope boundary: This layer receives validated, extracted claim line items from upstream (financial extraction, medical adjudication, tariff
adjudication) and produces a structured adjudication decision for each line item. PAS remains the final revalidation authority.

2. System Context

3. Core Design Principles

#

Principle

Rationale

1 Structured memory, not learned

Product JSON is the single source of truth for rules. AI interprets but does not invent rules.

behavior

2 Deterministic sequencing

Rule execution order is defined in the JSON via dependency graphs, not inferred at runtime.

3 Tools for math, AI for reasoning

All arithmetic goes through calculator tools. AI handles clause selection, ambiguity
resolution, and explanation.

4 Stateful across claims

Lifetime flags, accumulated balances, and age-lock state persist beyond individual claims.

5 Fail-safe, not fail-open

Missing data or low confidence routes to manual review. No invented data. No silent
approvals.

New Section 3 Page 1

approvals.

6 Auditable to the clause

Every decision traces back to a rule_id, section_ref, page_no, and specific inputs used.

7 PAS is final authority

AI recommends; PAS validates. Mismatches feed back into rule improvement.

4. Component Architecture

4.1 Context Builder
Responsibility: Assemble all data needed for adjudication from external APIs into a single claim context object. Data Sources:

API

Policy API

Data Retrieved

Status, product code, variant, dates, SI, riders, endorsements, co-pay option,
deductible option, room category

Used By

All gates

Member API

Member ID, age, relationship, addition date, PED declarations, eligibility flags

Member gate, waiting period
gate

Claims History API

Prior utilization, benefit consumption, claim frequency, prior exclusions triggered

SI gate, lifetime flags, Booster+

Porting/Migration
API

Network API

Credit transfer, waiting period credits, moratorium eligibility, prior coverage months Waiting period gate

Provider recognition, tiered network status, excluded provider match, HeadsUp
recommendation

Coverage gate, co-pay
calculation

Benefit Balance
API

Remaining SI, Booster+ balance, ReAssure Forever pool, Cash-Bag+ wallet, Hospital
Cash used, PA limits

Financial gate

Lifetime State API

Lock the Clock age status, ReAssure Forever triggered flag, Convalescence claimed
flag, Critical Illness claimed flag

State-dependent rules

Endorsement API Mid-term changes (member additions, SI enhancements, plan conversions) with

effective dates

All gates (determines which
rules apply)

Output: A single ClaimContext object containing all the above, timestamped and versioned.

{
  "claim_id": "CLM-2025-001234",
  "claim_received_at": "2025-05-05T10:30:00Z",
  "policy": {},
  "member": {},
  "history": {},
  "porting": {},
  "network": {},
  "benefit_balance": {},
  "lifetime_state": {},
  "endorsements": [],
  "line_items": [],
  "product_json_version": "R3_v2.1_2025-01-15"
}

4.2 Product Memory Store
Responsibility: Store and serve the exact versioned product JSON for any given policy. Key Design Decisions:

•
•
•
•

Each policy binds to a specific product JSON version at issuance

Endorsements may trigger a version upgrade (e.g., new rider added that exists only in newer JSON)

The store is immutable — new versions are appended, never overwritten
Lookup is by (product_id, variant, effective_date) tuple

Storage Schema:

product_json_store/

R3/

v1.0_2024-04-01.json

v2.0_2025-01-15.json

v2.1_2025-03-01.json

metadata.json (version history, change log, SME approval status)

New Section 3 Page 2

4.3 AI Planner
Responsibility: Given the claim context and product JSON, determine which rules apply and in what order they should execute. How it
works:

•

•

•

•

•

•

•

•

•

Load the product JSON's rule_blueprints array

Filter rules by:

benefit_category matching the claim's benefit bucket

variant_applicability matching the policy variant

preconditions that are potentially satisfiable given the claim context

mutual_exclusivity constraints (skip rules for benefits not opted)

Build a Directed Acyclic Graph (DAG) from depends_on fields

Topologically sort the DAG to produce execution order

Output an Execution Plan — an ordered list of rule evaluations

Execution Plan Structure:

{

"plan_id": "PLAN-2025-001234",

"claim_id": "CLM-2025-001234",

"line_item_id": "LI-001",

"execution_steps": [

{"step": 1, "rule_id": "R3_EXCL_003", "gate": "waiting_period", "reason": "30-day initial wait check"},

{"step": 2, "rule_id": "R3_EXCL_002", "gate": "waiting_period", "reason": "Specified disease 24-month check"},

{"step": 3, "rule_id": "R3_EXCL_001", "gate": "waiting_period", "reason": "PED 36-month check"},

{"step": 4, "rule_id": "R3_BEN_003", "gate": "coverage", "reason": "Hospitalization eligibility"},

{"step": 5, "rule_id": "R3_BEN_004", "gate": "financial", "reason": "Room pro-rata if applicable"},

{"step": 6, "rule_id": "R3_GEN_002", "gate": "financial", "reason": "Prolonged hospitalization penalty"},

{"step": 7, "rule_id": "R3_FIN_001", "gate": "financial", "reason": "Annual aggregate deductible"},

{"step": 8, "rule_id": "R3_FIN_002", "gate": "financial", "reason": "Co-payment"},

{"step": 9, "rule_id": "R3_SUM_001", "gate": "financial", "reason": "SI waterfall consumption"}

],

"confidence": 0.95

}

4.4 AI Execution Agent
Responsibility: Execute each step in the plan, calling tools for calculations, updating state, and producing decisions. Execution Loop:

FOR each step in execution_plan:

•
•
•
•
•
•
•
•
•
•
•
•
•
•

Load rule from product JSON by rule_id

Evaluate preconditions against claim context

If ALL preconditions met → proceed

If ANY precondition fails → mark rule as "not_applicable", skip

If precondition data is MISSING → route to exception

IF rule requires calculation:

Call appropriate tool with inputs from claim context

Receive deterministic output

IF rule is semantic/interpretive:

AI reasons over the clause text and claim evidence

Produces a decision with confidence score

Update running state (deductions, balances, flags)

Emit decision trace entry

IF confidence < threshold → flag for review, continue

New Section 3 Page 3

•

IF confidence < threshold → flag for review, continue

Key Constraint: The execution agent CANNOT override a tool's output. If the co-pay tool says 20%, the agent uses 20%. The agent's role is to
determine which tool to call with which inputs, not to second-guess the result.

4.5 Calculation Tools

Each tool is a deterministic function with defined inputs and outputs. No AI inference inside tools.

Tool 1: Waiting Period Calculator

Input: condition, policy_inception_date, continuous_coverage_months,

portability_credit_months, si_enhancement_date, accident_flag, cancer_flag

Output: { exclusion_active: bool, remaining_days: int, rule_applied: string }

Logic:
•
•
•
•
•

PED: 36 months from inception (or reduced by portability credit)

Specified disease: 24 months (except Accident day-1, Cancer 30-day)

Initial wait: 30 days (except Accident)

If SI enhanced: waiting applies afresh to enhanced portion only

If portability: reduce by prior coverage months

Tool 2: Room Pro-Rata Calculator

Input: eligible_room_rent, actual_room_rent, associated_medical_expenses

Output: { payable_amount: number, pro_rata_ratio: number, deduction: number }

Logic:

IF actual_room_rent > eligible_room_rent:

ratio = eligible_room_rent / actual_room_rent

payable = ratio * associated_medical_expenses

ELSE:

payable = associated_medical_expenses (no deduction)

Associated Medical Expenses = Room Rent + Nursing Charges + Medical Practitioner Fees + OT Charges

Tool 3: Co-Payment Calculator

Input: admissible_amount, copay_percent, benefit_bucket,

heads_up_penalty: bool, tiered_network_penalty: bool,

prolonged_hosp_penalty: bool, room_category_copay_percent

Output: { copay_amount: number, payable_amount: number, copay_breakdown: object }

Logic:

base_copay = admissible_amount * copay_percent

heads_up_copay = admissible_amount * 0.20 IF heads_up_penalty

tiered_copay = admissible_amount * 0.20 IF tiered_network_penalty

prolonged_copay = admissible_amount * 0.10 IF prolonged_hosp_penalty

room_copay = admissible_amount * room_category_copay_percent IF room breach

total_copay = base_copay + heads_up_copay + tiered_copay + prolonged_copay + room_copay

payable = admissible_amount - total_copay

Note: Co-pay does NOT apply to: Annual Health Check-up, Live Healthy, Second Medical Opinion, Shared Accommodation Cash, e-
consultation, Personal Accident, Hospital Daily Cash.

Tool 4: Deductible Calculator

Input: claim_amount, annual_deductible_limit, deductible_consumed_ytd, benefit_bucket

Output: { deductible_applied: number, payable_amount: number, deductible_remaining: number }

New Section 3 Page 4

Logic:

remaining_deductible = annual_deductible_limit - deductible_consumed_ytd

deductible_this_claim = min(claim_amount, remaining_deductible)

payable = claim_amount - deductible_this_claim

Note: Deductible does NOT apply to same benefits as co-pay exemptions.

Tool 5: Sum Insured Waterfall Calculator

Input: payable_amount, base_si_remaining, booster_plus_remaining,

reassure_forever_pool, reassure_forever_triggered,

unlimited_si_opted

Output: {

amount_from_base_si: number,

amount_from_booster: number,

amount_from_forever: number,

total_paid: number,

shortfall: number,

updated_base_si: number,

updated_booster: number,

updated_forever_pool: number

}

Logic:

remaining = payable_amount

// Step 1: Draw from Base SI

from_base = min(remaining, base_si_remaining)

remaining -= from_base

// Step 2: Draw from Booster+

from_booster = min(remaining, booster_plus_remaining)

remaining -= from_booster

// Step 3: Draw from ReAssure Forever (if triggered)

IF reassure_forever_triggered AND NOT unlimited_si_opted:

max_from_forever = min(base_si_original, reassure_forever_pool)

from_forever = min(remaining, max_from_forever)

remaining -= from_forever

shortfall = remaining // Amount not payable due to SI exhaustion

Tool 6: Lock the Clock Calculator

Input: entry_age, current_age, claim_paid_flag, policy_type (individual/floater),

policy_term_years, claim_in_year, member_claiming

Output: {

age_for_premium: int,

age_locked: bool,

additional_premium_delta: number,

deduct_from_payout: number

}

New Section 3 Page 5

}

Logic:

IF claim_paid_flag == false:

age_for_premium = entry_age // locked

ELSE:

age_for_premium = current_age // unlocked

IF multi_tenure_policy:

additional_premium = (current_age_premium - entry_age_premium) * remaining_years

deduct_from_payout = additional_premium

Tool 7: Booster+ Accumulation Calculator

Input: base_si, booster_plus_current, claim_free_year, variant_max_multiplier,

base_si_old (if changed), base_si_new (if changed)

Output: { booster_plus_new: number, accumulation_applied: bool }

Logic:

max_booster = base_si * variant_max_multiplier

IF claim_free_year:

booster_plus_new = min(booster_plus_current + base_si, max_booster)

ELSE IF base_si reduced:

reduction_ratio = base_si_new / base_si_old

booster_plus_new = booster_plus_current * reduction_ratio

ELSE:

booster_plus_new = booster_plus_current // no change

Tool 8: Pre/Post Hospitalization Window Validator

Input: expense_date, admission_date, discharge_date,

pre_hosp_days_limit, post_hosp_days_limit,

expense_condition, hospitalization_condition

Output: { eligible: bool, window_type: "pre"|"post"|"outside", days_from_event: int }

Logic:

IF expense_date < admission_date:

days_before = admission_date - expense_date

eligible = (days_before <= pre_hosp_days_limit) AND (expense_condition == hospitalization_condition)

ELSE IF expense_date > discharge_date:

days_after = expense_date - discharge_date

eligible = (days_after <= post_hosp_days_limit) AND (expense_condition == hospitalization_condition)

ELSE:

window_type = "during_hospitalization" // handled by main hospitalization benefit

4.6 State Manager

Responsibility: Maintain running state during claim processing and persist lifetime state after claim settlement. Two State Scopes:

Scope

Lifetime

Examples

Per-Claim State Duration of single claim processing

Running deductions, intermediate payable amounts, line-item decisions

Lifetime State

Persists across claims and renewals ReAssure Forever triggered, Lock the Clock age, Booster+ balance, Convalescence

claimed, Critical Illness claimed, Cash-Bag+ wallet

New Section 3 Page 6

Lifetime State Schema:

{
  "policy_id": "POL-2023-56789",
  "lifetime_state": {
    "reassure_forever": {
      "triggered": true,
      "triggered_on_claim": "CLM-2024-000456",
      "triggered_date": "2024-08-15"
    },
    "lock_the_clock": {
      "age_locked": false,
      "entry_age": 25,
      "unlocked_on_claim": "CLM-2024-000456",
      "unlocked_date": "2024-08-15",
      "current_premium_age": 27
    },
    "booster_plus": {
      "accumulated_amount": 3000000,
      "last_updated": "2025-04-01",
      "claim_free_years_count": 3
    },
    "convalescence_benefit": {
      "claimed": false,
      "claimed_on": null
    },
    "critical_illness": {
      "claimed": false,
      "claimed_on": null,
      "illness_type": null
    },
    "cash_bag_plus": {
      "balance": 15000,
      "last_credited": "2025-04-01"
    },
    "live_healthy": {
      "current_points": 2800,
      "points_snapshot_date": "2025-01-01"
    }
  }
}

4.7 Decision Composer
Responsibility: Aggregate all line-item decisions into a claim-level decision with full audit trail. Decision States:

State

APPROVED

Meaning

Full amount payable

PARTIALLY_APPROVED

Some deductions applied (co-pay, deductible, pro-rata, SI cap)

REJECTED

Not payable (exclusion, waiting period, policy lapsed, etc.)

PENDING_REVIEW

Low confidence or missing data — routed to manual

Output Structure:

{

"claim_id": "CLM-2025-001234",

"claim_decision": "PARTIALLY_APPROVED",

"total_claimed": 450000,

"total_admissible": 420000,

"total_payable": 336000,

"total_deductions": 84000,

"deduction_breakdown": {

"room_pro_rata": 30000,

New Section 3 Page 7

"co_payment": 42000,

"non_payable_items": 12000,

"deductible": 0

},

"line_items": [

{

"line_item_id": "LI-001",

"description": "Room charges - 5 days",

"claimed": 50000,

"admissible": 50000,

"payable": 40000,

"decision": "PARTIALLY_APPROVED",

"deductions": [

{"type": "room_pro_rata", "amount": 10000, "rule_id": "R3_BEN_004", "reason": "Room category breach - Select variant, claimed
Suite"}

]

}

],

"decision_trace": [ ... ],

"confidence_score": 0.92,

"manual_review_required": false,

"pas_submission_payload": { ... }

}

5. Gate Execution Sequence

The adjudication follows a strict gate sequence. A claim line item must pass each gate to proceed. Failure at any gate produces a rejection or
routes to review.

GATE EXECUTION SEQUENCE

Gate 1: POLICY VALIDATION

Policy active? Premium paid? Within grace period?

Not lapsed? Not void? Not fraud-flagged?

Correct product version loaded?

Gate 2: MEMBER VALIDATION

Member exists on policy? Relationship valid?

Age within eligible range? Addition date valid?

Not excluded by endorsement?

Gate 3: COVERAGE VALIDATION

Benefit bucket mapped? Treatment type covered?

Variant supports this benefit?

Optional benefit opted? Rider active?

Mutual exclusivity check passed?

Minimum duration met? (2hr / 24hr AYUSH)

New Section 3 Page 8

Gate 4: WAITING PERIOD VALIDATION

Initial 30-day wait cleared?

Specified disease 24-month wait cleared?

PED 36-month wait cleared?

Personal waiting period (up to 48 months) cleared?

Critical Illness 90-day wait cleared?

Portability/migration credits applied?

Gate 5: EXCLUSION VALIDATION

Standard exclusions (Excl01-Excl18) checked?

Specific exclusions (5.2.1-5.2.7) checked?

Provider not excluded?

Treatment not unproven/experimental?

Not investigation-only admission?

Gate 6: FINANCIAL COMPUTATION

Non-payable items removed (Annexure)?

Room pro-rata applied (if room breach)?

Prolonged hospitalization penalty (if applicable)?

HeadsUp / Tiered Network penalty (if applicable)?

Annual Aggregate Deductible applied?

Co-payment applied?

Sub-limits applied (Modern Treatments, etc.)?

SI waterfall consumption computed?

Lock the Clock premium adjustment (if applicable)?

Gate 7: STATE UPDATE

Update SI balances (Base, Booster+, Forever)

Update lifetime flags (if first claim triggers)

Update deductible consumed YTD

Update Cash-Bag+ wallet (if used for co-pay)

6. Rule Dependency Graph

Rules are not independent. The product JSON must encode dependencies so the planner can sequence correctly.

6.1 Dependency Model

Each rule blueprint gains a depends_on field:

{

"rule_id": "R3_FIN_002",

"name": "Co-payment",

"depends_on": ["R3_BEN_004", "R3_GEN_002", "R3_BEN_016", "R3_BEN_017"],

"note": "Co-pay applies AFTER room pro-rata, prolonged hosp penalty, HeadsUp penalty, Tiered Network penalty"

}

6.2 Critical Ordering Constraints

New Section 3 Page 9

6.2 Critical Ordering Constraints

Must Execute First

Must Execute After

Reason

R3_BEN_004 (Room pro-rata)

R3_FIN_002 (Co-pay)

Co-pay applies on the already-reduced admissible amount

R3_GEN_002 (Prolonged hosp
penalty)

R3_FIN_002 (Co-pay)

Penalty stacks before co-pay

R3_BEN_016 (HeadsUp penalty)

R3_FIN_002 (Co-pay)

HeadsUp 20% is a separate co-pay layer

R3_FIN_001 (Deductible)

R3_SUM_001 (SI waterfall)

Deductible reduces amount before SI consumption

R3_FIN_002 (Co-pay)

All exclusion rules

All waiting period rules

R3_SUM_001 (SI waterfall)

R3_SUM_001 (SI waterfall)

Co-pay reduces amount before SI consumption

All financial rules

All coverage rules

No point computing payable if excluded

Waiting period can override coverage

R3_SUM_002 (ReAssure Forever
trigger)

First claim triggers Forever; need to know if SI was
consumed

6.3 Parallel Execution Opportunities

Rules within the same gate that have no mutual dependencies can execute in parallel:

•
•
•

All exclusion checks (R3_EXCL_001 through R3_EXCL_023) are independent of each other

All waiting period checks are independent of each other

Pre-hospitalization and post-hospitalization window checks are independent

7. Stateful Cross-Claim Logic

7.1 ReAssure Forever State Machine

States: NOT_TRIGGERED → TRIGGERED → ACTIVE (renewed) → LAPSED (break in policy)

Transitions:

NOT_TRIGGERED + first_claim_paid → TRIGGERED

TRIGGERED + renewal_without_break → ACTIVE

ACTIVE + renewal_without_break → ACTIVE (persists)

ACTIVE + break_in_policy → LAPSED (lost forever)

Behavior when ACTIVE:

Each claim can draw up to Base SI from the Forever pool

Forever pool replenishes to Base SI at each renewal

This triggers unlimited times per year

•
•
•

`

7.2 Lock the Clock State Machine
`

States: LOCKED → UNLOCKED

Transitions:

LOCKED + claim_paid_in_trigger_buckets → UNLOCKED

Special cases:

•
•
•

Floater: ANY member's claim unlocks for ENTIRE policy

Multi-individual: ONLY the claiming member unlocks

Multi-tenure: Additional premium for remaining years deducted from payout

7.3 Booster+ Accumulation Logic
`

At each renewal (if claim-free year):

booster_new = min(booster_current + base_si, max_multiplier * base_si)

On SI reduction:

New Section 3 Page 10

On SI reduction:

booster_new = booster_current * (new_base_si / old_base_si)

On individual-to-floater conversion:

floater_booster = min(member_1_booster, member_2_booster, ...)

On floater split

each_policy_booster = floater_booster (if base SI not reduced)

8. Mid-Term Endorsement Handling

8.1 Endorsement Types and Their Impact

Endorsement

Impact on Rules

Add member to floater

New member: all waiting periods fresh. Lock the Clock: recalculate based on eldest member entry age.

Individual → Floater conversion

Booster+: take least of individual amounts. Lock the Clock: eldest member's entry age.

SI Enhancement

SI Reduction

Rider addition

All waiting periods apply afresh to enhanced portion. Booster+ max recalculated.

Booster+ proportionally reduced.

New rider benefits available from endorsement date. Waiting periods may apply.

Member deletion

If eldest member removed from floater: recalculate Lock the Clock age from remaining members.

Plan upgrade (variant change)

Room category changes. Health checkup package changes. Modern Treatment sub-limits may change.

8.2 Endorsement Processing Rule

FOR each endorsement on the policy (ordered by effective_date):

IF endorsement.effective_date <= claim.admission_date:

Apply endorsement effects to claim context

ELSE:

Ignore (endorsement not yet effective at time of claim)

9. Confidence Scoring Model

9.1 Confidence Factors

Factor

High Confidence (>0.9)

Medium (0.7-0.9)

Low (<0.7)

Condition classification

Clearly matches a defined benefit bucket Could map to multiple buckets

Ambiguous or novel condition

Waiting period

Clearly past all waiting periods

Borderline (within days of clearing) Disputed PED declaration

Exclusion match

Clearly not excluded OR clearly excluded

Partial match to exclusion criteria

Requires medical judgment

Treatment necessity

Standard protocol for condition

Multiple valid approaches

Potentially investigation-only

Provider status

Clearly in-network

Network status recently changed

Provider under review

Data completeness

All API data available

Minor fields missing (non-critical)

Critical fields missing

9.2 Routing Rules

IF overall_confidence >= 0.90 AND all_gates_passed:

→ AUTO-APPROVE (send to PAS for revalidation)

IF overall_confidence >= 0.70 AND overall_confidence < 0.90:

→ ASSISTED MODE (pre-populate decision, flag for operations review)

IF overall_confidence < 0.70 OR critical_data_missing:

→ MANUAL REVIEW (route to adjudicator with AI recommendation + reasoning)

IF any_exclusion_triggered AND confidence_on_exclusion < 0.80:

→ MEDICAL REVIEW (route to medical team for clinical judgment)

10. Error Handling & Fallback Paths

Error Scenario

API timeout

Handling

Retry once. If still failed, route claim to exception queue. Never proceed with partial data.

New Section 3 Page 11

Product JSON not found

Hard stop. Log error. Route to operations for product version resolution.

Rule precondition data missing

Skip rule, flag as "unable_to_evaluate", reduce confidence, route to review if critical.

Tool calculation error

Log inputs and error. Route line item to manual calculation.

SI balance negative

Cap at zero. Mark as SI_EXHAUSTED. Partially approve up to available balance.

Conflicting rules

Use mutual_exclusivity_constraints. If still ambiguous, route to review with both interpretations.

PAS revalidation mismatch

Log mismatch with root cause category. Do NOT auto-correct AI decision. Feed into improvement loop.

11. PAS Reconciliation Design

11.1 Mismatch Categories

Category

Description

Resolution Path

RULE_EXTRACTION

Product JSON missing or incorrectly capturing a policy
clause

Update product JSON, re-validate with SME

RULE_INTERPRETATION

AI applied the rule differently than PAS logic

Refine rule preconditions or add disambiguation
notes

API_DATA

CALCULATION

Different data seen by AI vs PAS (timing, staleness)

Align API refresh timing, add data version checks

Arithmetic difference (rounding, sequence)

Fix tool logic or input mapping

PRODUCT_VERSION

AI used different product version than PAS

Fix version binding logic

MANUAL_OVERRIDE

PAS has a manual override not reflected in rules

Document override as exception rule or position
statement

PAS_EXCEPTION

PAS has hardcoded logic not in policy wording

Evaluate if PAS logic is correct; if so, add to JSON

Running balance (Booster+, Forever, wallet) diverges

Reconcile lifetime state store with PAS ledger

STATEFUL_ACCUMULATIO
N

11.2 Reconciliation Flow

•
•
•
•
•
•
•
•
•

AI produces decision → sent to PAS

PAS produces its decision independently

Comparator service checks:

Line-item level: payable amount match (within tolerance ±₹1)

Claim level: total payable match

Decision state match (approved/rejected/partial)

IF match → log as CONCORDANT

IF mismatch → classify into category, log with full trace from both sides

Weekly: aggregate mismatches, identify patterns, prioritize fixes

12. Observability & Audit

12.1 Decision Trace (per line item)

Every line item produces a trace array:

[

{

"step": 1,

"rule_id": "R3_EXCL_002",

"rule_name": "Specified disease waiting period",

"gate": "waiting_period",

"inputs": {"condition": "Cataract", "continuous_coverage_months": 30, "accident_related": false},

"evaluation": "EXCLUSION_ACTIVE",

"reason": "Cataract is in specified disease list. 24 months required, only 30 months covered. Wait cleared.",

"confidence": 0.98,

"source_section": "5.1.2",

New Section 3 Page 12

"source_page": 30,

"timestamp": "2025-05-05T10:30:01.234Z"

}

]

12.2 Metrics to Track

Metric

Purpose

Auto-approval rate

% of claims fully auto-adjudicated without manual touch

PAS concordance rate

% of claims matching PAS decision

Average confidence score

Trend of AI certainty over time

Gate failure distribution

Which gates reject most claims (identifies product complexity)

Tool invocation latency

Performance of calculation tools

Exception queue depth

Backlog of claims needing manual review

Mismatch category distribution Where to focus rule improvement

13. Rollout Phases

Phase 1: Shadow Mode (Weeks 1-8)

•
•
•
•

AI runs in parallel with PAS on all claims

No production impact — PAS remains authoritative

Measure concordance rate, identify top mismatch categories

Target: >85% concordance before proceeding

Phase 2: Assisted Mode (Weeks 9-16)

•
•
•
•

AI pre-populates adjudication decisions

Operations team reviews and approves/overrides

Track override rate and reasons

Target: <15% override rate before proceeding

Phase 3: Controlled Automation (Weeks 17-24)

•
•
•
•
•
•
•
•

Auto-adjudicate claims where:

Confidence >= 0.92

Claim amount <= ₹2,00,000

No PED involvement

No exclusion triggered

Single hospitalization (not multi-episode)

All others remain in assisted mode

Target: 40-50% auto-adjudication rate

Phase 4: Scale-Out (Week 25+)

•
•
•
•
•

Gradually increase auto-adjudication thresholds

Add more products beyond ReAssure 3.0

Reduce amount ceiling

Add complex scenarios (multi-claim SI consumption, endorsement-heavy policies)

Target: 70%+ auto-adjudication rate at steady state

14. Non-Functional Requirements

New Section 3 Page 13

14. Non-Functional Requirements

Requirement

Target

Latency

< 5 seconds for cashless pre-auth; < 30 seconds for reimbursement

Throughput

500 claims/hour during peak

Availability

99.5% uptime during business hours

Data retention

Decision traces retained for 8 years (regulatory)

Security

All PII encrypted at rest and in transit; role-based access to claim data

Scalability

Horizontal scaling of execution agents; line items processed in parallel

Recoverability

Any claim can be re-adjudicated from stored inputs + product JSON version

15. Key Risks & Mitigations

Risk

Impact

Mitigation

Product JSON has gaps vs actual policy
wording

Incorrect adjudication

SME validation loop; PAS reconciliation catches gaps

AI hallucinates a rule that doesn't exist

Incorrect approval/rejection

Structured memory constraint — AI can only reference rules
in the JSON

Lifetime state diverges from PAS ledger

Incorrect SI/balance calculations Nightly reconciliation job; state store syncs from PAS as

API data staleness

Decision based on outdated info

source of truth

Timestamp all API responses; reject if data older than
threshold

Tool calculation edge cases

Rounding errors, boundary
conditions

Comprehensive unit test suite for all tools; property-based
testing

Endorsement not reflected in context

Wrong rules applied

Endorsement API must return ALL changes with effective
dates

16. Deliverables Checklist

#

Deliverable

Status

1

2

3

4

5

6

7

8

9

10

11

12

Product JSON (ReAssure 3.0) with dependency fields ✅ Created (reassure3-policy-rules-v2.json)

Claim Context API specification

Lifetime State schema and API

Calculation Tool contracts (8 tools)

Gate execution sequence specification

Confidence scoring model

PAS reconciliation service design

Decision output schema

Endorsement event model

Observability and metrics specification

Rollout plan with success criteria

  To be designed

  To be designed

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

✅ Defined in this document

Manual review exception workflow

  To be designed

17. Appendix: Product JSON Enhancement Recommendations

To make the existing reassure3-policy-rules-v2.json fully compatible with this architecture, add the following fields to each rule
blueprint:

{

"rule_id": "R3_FIN_002",

New Section 3 Page 14

"depends_on": ["R3_BEN_004", "R3_GEN_002", "R3_BEN_016", "R3_BEN_017"],

"gate": "financial",

"execution_priority": 80,

"tool_required": "copay_calculator",

"auto_adjudicable": true,

"confidence_weight": 0.95,

"test_scenarios": [

{"input": {"admissible": 100000, "copay_percent": 0.20}, "expected_output": {"payable": 80000}}

]

}

New fields:

•

•

•

•

•

•

•

depends_on — array of rule_ids that must execute before this rule

gate — which gate this rule belongs to (policy/member/coverage/waiting/exclusion/financial)

execution_priority — numeric priority within the gate (lower = earlier)

tool_required — which calculation tool to invoke (null if AI-only evaluation)

auto_adjudicable — whether this rule can be auto-decided or always needs review

confidence_weight — how much this rule's confidence affects overall claim confidence

test_scenarios` — embedded test cases for validation

New Section 3 Page 15

