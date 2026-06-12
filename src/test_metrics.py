"""
Unit tests for the Claims Analytics & Metrics Engine (src/metrics.py)
"""

import os
import json
import pytest
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any

from metrics import TelemetrySession, PipelineMetricsEngine, telemetry_context
from schemas import (
    ClaimDecision, DeductionBreakdown, SIWaterfallBreakdown,
    LineItemDecision, DecisionTrace
)

@pytest.fixture
def temp_telemetry_file(tmp_path):
    """Fixture providing a temporary telemetry file path"""
    file_path = tmp_path / "test_telemetry.jsonl"
    return str(file_path)

def test_telemetry_session_recording():
    """Verify TelemetrySession records latencies correctly"""
    session = TelemetrySession()
    session.record_tool_latency("tool_1", 10.5)
    session.record_tool_latency("tool_1", 12.0)
    session.record_tool_latency("tool_2", 5.0)

    session.record_gate_latency("gate_1", 100.0)
    session.record_gate_latency("gate_1", 105.0)

    assert session.tool_latencies["tool_1"] == [10.5, 12.0]
    assert session.tool_latencies["tool_2"] == [5.0]
    assert session.gate_latencies["gate_1"] == [100.0, 105.0]

def test_pipeline_metrics_engine_recording(temp_telemetry_file):
    """Verify that records are parsed, simulated, and saved to the telemetry file"""
    engine = PipelineMetricsEngine(temp_telemetry_file)

    # Mock DecisionTrace list
    traces = [
        DecisionTrace(
            step=1,
            rule_id="R3_EXCL_002",
            rule_name="Waiting Period Exclusion",
            gate="waiting_period_validation",
            inputs={},
            evaluation="EXCLUSION_ACTIVE",
            reason="Exclusion active"
        ),
        DecisionTrace(
            step=2,
            rule_id="R3_BEN_003",
            rule_name="Hospitalization",
            gate="coverage_validation",
            inputs={},
            evaluation="PASSED",
            reason="Passed"
        )
    ]

    # Mock ClaimDecision
    decision = ClaimDecision(
        claim_id="CLM-TEST-001",
        claim_decision="PARTIALLY_APPROVED",
        total_claimed=10000.0,
        total_admissible=9000.0,
        total_payable=8000.0,
        total_deductions=2000.0,
        deduction_breakdown=DeductionBreakdown(
            room_pro_rata=1000.0,
            co_payment=500.0,
            deductible=500.0
        ),
        si_waterfall_breakdown=SIWaterfallBreakdown(
            amount_from_base_si=8000.0,
            total_paid=8000.0,
            shortfall=2000.0,
            updated_base_si=92000.0,
            updated_booster=0.0,
            updated_forever_pool=0.0
        ),
        line_items=[],
        decision_trace=traces,
        confidence_score=0.95,
        manual_review_required=False,
        processing_duration_ms=45.2
    )

    session = TelemetrySession()
    session.record_tool_latency("calculate_copayment", 5.2)
    session.record_gate_latency("financial_computation", 12.4)

    # Record execution
    entry = engine.record_execution(decision, session, pas_override_decision="PARTIALLY_APPROVED")

    assert entry["claim_id"] == "CLM-TEST-001"
    assert entry["claim_decision"] == "PARTIALLY_APPROVED"
    assert entry["pas_decision"] == "PARTIALLY_APPROVED"
    assert entry["confidence_score"] == 0.95
    assert entry["processing_duration_ms"] == 45.2
    assert entry["deduction_breakdown"]["room_pro_rata"] == 1000.0
    assert entry["deduction_breakdown"]["co_payment"] == 500.0
    assert entry["deduction_breakdown"]["deductible"] == 500.0
    assert entry["waterfall_shortfall"] == 2000.0
    assert len(entry["failed_rules"]) == 1
    assert entry["failed_rules"][0]["rule_id"] == "R3_EXCL_002"
    assert entry["tool_latencies"]["calculate_copayment"] == [5.2]
    assert entry["gate_latencies"]["financial_computation"] == [12.4]

    # Verify file content
    records = engine.load_telemetry_records()
    assert len(records) == 1
    assert records[0]["claim_id"] == "CLM-TEST-001"

def test_metrics_calculation(temp_telemetry_file):
    """Verify core metrics calculation calculations"""
    engine = PipelineMetricsEngine(temp_telemetry_file)

    # Populate temporary file with mock data
    runs = [
        # Auto-Approved: claim_decision in APPROVED/PARTIALLY_APPROVED and confidence >= 0.90
        # Concordant: claim_decision == pas_decision
        {
            "claim_id": "CLM-01",
            "claim_decision": "APPROVED",
            "confidence_score": 0.98,
            "pas_decision": "APPROVED",
            "deduction_breakdown": {"room_pro_rata": 0.0, "co_payment": 0.0, "deductible": 0.0},
            "waterfall_shortfall": 0.0,
            "tool_latencies": {"calculate_copayment": [1.5]},
            "gate_latencies": {"financial_computation": [5.0]},
            "failed_rules": []
        },
        {
            "claim_id": "CLM-02",
            "claim_decision": "PARTIALLY_APPROVED",
            "confidence_score": 0.92,
            "pas_decision": "PARTIALLY_APPROVED",
            "deduction_breakdown": {"room_pro_rata": 1000.0, "co_payment": 500.0, "deductible": 200.0},
            "waterfall_shortfall": 300.0,
            "tool_latencies": {"calculate_copayment": [1.2]},
            "gate_latencies": {"financial_computation": [4.8]},
            "failed_rules": []
        },
        # Assisted Review (not auto-approved because of decision state and/or confidence)
        {
            "claim_id": "CLM-03",
            "claim_decision": "ASSISTED_REVIEW",
            "confidence_score": 0.82,
            "pas_decision": "APPROVED",  # Mismatch / non-concordant
            "deduction_breakdown": {"room_pro_rata": 0.0, "co_payment": 0.0, "deductible": 0.0},
            "waterfall_shortfall": 0.0,
            "tool_latencies": {"calculate_copayment": [2.0]},
            "gate_latencies": {"financial_computation": [6.0]},
            "failed_rules": [{"rule_id": "R3_EXCL_007", "gate": "exclusion_validation"}]
        },
        # Pending Review
        {
            "claim_id": "CLM-04",
            "claim_decision": "PENDING_REVIEW",
            "confidence_score": 0.65,
            "pas_decision": "PENDING_REVIEW",
            "deduction_breakdown": {"room_pro_rata": 0.0, "co_payment": 0.0, "deductible": 0.0},
            "waterfall_shortfall": 0.0,
            "tool_latencies": {"calculate_copayment": [1.0, 1.8]},
            "gate_latencies": {"financial_computation": [4.0, 5.2]},
            "failed_rules": [{"rule_id": "R3_EXCL_002", "gate": "waiting_period_validation"}]
        }
    ]

    with open(temp_telemetry_file, "w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r) + "\n")

    metrics = engine.calculate_metrics()

    # Total runs = 4
    # Auto-approved: CLM-01 (APPROVED, 0.98), CLM-02 (PARTIALLY_APPROVED, 0.92) -> 2/4 = 50%
    assert metrics["auto_approval_rate"] == 0.50

    # PAS Concordance: CLM-01, CLM-02, CLM-04 matched -> 3/4 = 75%
    assert metrics["pas_concordance_rate"] == 0.75

    # Exception Queue Depth: 1 ASSISTED, 1 PENDING -> ratio 50% / 50%
    assert metrics["exception_queue"]["assisted_review_count"] == 1
    assert metrics["exception_queue"]["pending_review_count"] == 1
    assert metrics["exception_queue"]["assisted_ratio"] == 0.50
    assert metrics["exception_queue"]["pending_ratio"] == 0.50

    # Average Confidence Score: mean of 0.98, 0.92, 0.82, 0.65
    expected_mean = (0.98 + 0.92 + 0.82 + 0.65) / 4
    assert pytest.approx(metrics["average_confidence_score"]["mean"]) == expected_mean

    # Gate failure distribution
    assert metrics["gate_failure_distribution"]["exclusion_validation"] == 1
    assert metrics["gate_failure_distribution"]["waiting_period_validation"] == 1
    assert metrics["rule_failure_distribution"]["R3_EXCL_007"] == 1
    assert metrics["rule_failure_distribution"]["R3_EXCL_002"] == 1

    # Financial delta distribution
    fd = metrics["deduction_mismatch_category_distribution"]
    assert fd["pro_rata_delta"] == 1000.0
    assert fd["stacking_copay_delta"] == 500.0
    assert fd["deductible_delta"] == 200.0
    assert fd["si_waterfall_depletion"] == 300.0
    assert fd["total_delta"] == 2000.0

    # Tool and Gate latencies
    copay_lats = metrics["tool_invocation_latency"]["calculate_copayment"]
    # calculate_copayment: 1.5, 1.2, 2.0, 1.0, 1.8 -> sorted: 1.0, 1.2, 1.5, 1.8, 2.0. len = 5. P95 index = int(5 * 0.95) = 4 -> 2.0
    assert pytest.approx(copay_lats["avg"]) == (1.5 + 1.2 + 2.0 + 1.0 + 1.8) / 5
    assert copay_lats["p95"] == 2.0

def test_telemetry_thread_safety(temp_telemetry_file):
    """Verify that writing telemetry records concurrently is thread-safe"""
    engine = PipelineMetricsEngine(temp_telemetry_file)

    # Empty decision trace / ClaimDecision helper
    decision = ClaimDecision(
        claim_id="CLM-THREAD-TEST",
        claim_decision="APPROVED",
        total_claimed=100.0,
        total_admissible=100.0,
        total_payable=100.0,
        total_deductions=0.0,
        deduction_breakdown=DeductionBreakdown(),
        si_waterfall_breakdown=SIWaterfallBreakdown(
            updated_base_si=0.0, updated_booster=0.0, updated_forever_pool=0.0
        ),
        line_items=[],
        confidence_score=0.95
    )

    num_threads = 10
    runs_per_thread = 5

    def worker():
        session = TelemetrySession()
        session.record_tool_latency("tool_t", 1.0)
        for _ in range(runs_per_thread):
            engine.record_execution(decision, session, pas_override_decision="APPROVED")

    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Read back and verify all records were written successfully without data corruption
    records = engine.load_telemetry_records()
    assert len(records) == num_threads * runs_per_thread
    for r in records:
        assert r["claim_id"] == "CLM-THREAD-TEST"
