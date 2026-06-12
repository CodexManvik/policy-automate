import logging
import time
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime, timezone
from schemas import ClaimDecision, PASMismatch, PASReconciliationResult

_logger = logging.getLogger("pas_comparator")

class PASComparator:
    TOLERANCE_INR: float = 1.0

    def compare(self, engine_decision: ClaimDecision, pas_decision: Dict[str, Any]) -> PASReconciliationResult:
        """
        Compare the engine's decision and PAS submission payload with the actual PAS decision.
        """
        t_start = time.perf_counter()
        claim_id = engine_decision.claim_id
        mismatches: List[PASMismatch] = []
        payload = engine_decision.pas_submission_payload or {}

        # 1. Compare top-level fields
        fields_to_compare = [
            ("adjudication_decision", "ROUTING"),
            ("total_claimed", "CALCULATION"),
            ("total_admissible", "CALCULATION"),
            ("total_payable", "CALCULATION"),
            ("total_deductions", "CALCULATION"),
        ]

        for field_name, category in fields_to_compare:
            engine_val = payload.get(field_name)
            pas_val = pas_decision.get(field_name)
            self._check_value(field_name, engine_val, pas_val, category, mismatches)

        # 2. Compare deduction breakdown
        engine_db = payload.get("deduction_breakdown", {})
        pas_db = pas_decision.get("deduction_breakdown", {})
        if engine_db or pas_db:
            for key in ["room_pro_rata", "co_payment", "non_payable_items", "deductible", "si_cap", "sublimits", "penalties"]:
                self._check_value(
                    f"deduction_breakdown.{key}",
                    engine_db.get(key) if engine_db else None,
                    pas_db.get(key) if pas_db else None,
                    "CALCULATION",
                    mismatches
                )

        # 3. Compare SI sourcing / stateful accumulations
        engine_si = payload.get("si_sourcing", {})
        pas_si = pas_decision.get("si_sourcing", {})
        if engine_si or pas_si:
            for key in ["from_base_si", "from_booster", "from_forever"]:
                self._check_value(
                    f"si_sourcing.{key}",
                    engine_si.get(key) if engine_si else None,
                    pas_si.get(key) if pas_si else None,
                    "STATEFUL_ACCUMULATION",
                    mismatches
                )

        # Log concordant vs discordant status
        concordant = len(mismatches) == 0
        status_str = "CONCORDANT" if concordant else "DISCORDANT"
        mismatch_categories = sorted(list(set(m.category for m in mismatches)))

        _logger.info(
            f"PAS Reconciliation for Claim {claim_id}: {status_str}. "
            f"Mismatches: {len(mismatches)}, Categories: {mismatch_categories}"
        )

        duration_ms = (time.perf_counter() - t_start) * 1000.0

        return PASReconciliationResult(
            claim_id=claim_id,
            concordant=concordant,
            mismatches=mismatches,
            mismatch_categories=mismatch_categories,
            engine_total_payable=engine_decision.total_payable,
            pas_total_payable=pas_decision.get("total_payable"),
            checked_at=datetime.now(timezone.utc),
            check_duration_ms=duration_ms
        )

    def _check_value(
        self,
        field_name: str,
        engine_val: Any,
        pas_val: Any,
        category: Literal["RULE_EXTRACTION", "CALCULATION", "STATEFUL_ACCUMULATION", "ROUTING"],
        mismatches: List[PASMismatch]
    ):
        if engine_val is None and pas_val is None:
            return

        # Check for numeric types to apply tolerance
        is_numeric = isinstance(engine_val, (int, float)) and isinstance(pas_val, (int, float))
        if is_numeric:
            delta = float(abs(engine_val - pas_val))
            if delta > self.TOLERANCE_INR:
                mismatches.append(PASMismatch(
                    field=field_name,
                    engine_value=engine_val,
                    pas_value=pas_val,
                    delta=delta,
                    category=category,
                    tolerance_applied=True
                ))
        else:
            if engine_val != pas_val:
                mismatches.append(PASMismatch(
                    field=field_name,
                    engine_value=engine_val,
                    pas_value=pas_val,
                    delta=None,
                    category=category,
                    tolerance_applied=False
                ))

    def record_weekly_report(self, results: List[PASReconciliationResult]) -> Dict[str, Any]:
        """
        Aggregate reconciliation results to produce a weekly mismatch report.
        """
        total = len(results)
        concordant_count = sum(1 for r in results if r.concordant)
        discordant_count = total - concordant_count

        category_counts = {"RULE_EXTRACTION": 0, "CALCULATION": 0, "STATEFUL_ACCUMULATION": 0, "ROUTING": 0}
        field_mismatches = {}

        for r in results:
            for m in r.mismatches:
                category_counts[m.category] = category_counts.get(m.category, 0) + 1
                field_mismatches[m.field] = field_mismatches.get(m.field, 0) + 1

        accuracy_rate = (concordant_count / total) * 100.0 if total > 0 else 100.0

        return {
            "total_reconciled": total,
            "concordant_claims": concordant_count,
            "discordant_claims": discordant_count,
            "accuracy_rate_percent": accuracy_rate,
            "mismatch_by_category": category_counts,
            "mismatch_by_field": field_mismatches,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
