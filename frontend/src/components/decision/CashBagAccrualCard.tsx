/**
 * CashBagAccrualCard — displays Cash-Bag+ accrual details sourced from
 * the decision trace. Previously displayed a hardcoded "2,800 points → ₹700"
 * string regardless of the actual adjudication result.
 *
 * Now reads the accrual amount directly from the CASH_BAG_PLUS_ACCRUAL
 * trace entry so the displayed values are always accurate.
 */

import { Wallet } from 'lucide-react';
import type { ClaimDecision } from '../../types/claims';

interface Props {
  decision: ClaimDecision;
  formatCurrency: (val: number | undefined) => string;
}

export function CashBagAccrualCard({ decision, formatCurrency }: Props) {
  // Source accrual data from the decision trace — never from a hardcoded string.
  const accrualTrace = decision.decision_trace.find(
    (t) => t.rule_id === 'CASH_BAG_PLUS_ACCRUAL',
  );

  if (!accrualTrace) return null;

  // The trace inputs carry the points consumed and credit amount computed by the engine.
  const pointsConverted =
    typeof accrualTrace.inputs?.points_converted === 'number'
      ? (accrualTrace.inputs.points_converted as number)
      : null;

  const creditAmount =
    typeof accrualTrace.inputs?.credit_amount === 'number'
      ? (accrualTrace.inputs.credit_amount as number)
      : null;

  // Fallback: if the trace inputs do not carry these keys, show generic confirmation.
  const label =
    pointsConverted !== null && creditAmount !== null
      ? `${pointsConverted.toLocaleString('en-IN')} points converted to ${formatCurrency(creditAmount)} Cash-Bag+ wallet credit.`
      : accrualTrace.reason || 'Cash-Bag+ wallet credited.';

  const displayAmount = creditAmount !== null ? creditAmount : undefined;

  return (
    <div className="p-4 bg-emerald-950/10 border border-emerald-500/20 rounded-2xl flex items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
          <Wallet className="w-4 h-4" />
        </div>
        <div>
          <p className="text-xs font-semibold text-white">Cash-Bag+ Wallet Accrual</p>
          <p className="text-[10px] text-slate-400 mt-0.5">
            Wellness Points Converted!{' '}
            <span className="text-emerald-300 font-semibold font-mono">{label}</span>
          </p>
        </div>
      </div>
      {displayAmount !== undefined && (
        <span className="text-xs font-mono font-bold text-emerald-300 bg-emerald-500/10 px-2 py-1 rounded-lg">
          +{formatCurrency(displayAmount)}
        </span>
      )}
    </div>
  );
}
