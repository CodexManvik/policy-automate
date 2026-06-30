/**
 * DecisionBanner — top-level adjudication decision display.
 * Shows decision status, total payable, and styled color state.
 */

import { ShieldCheck, ShieldAlert, Shield } from 'lucide-react';
import type { ClaimDecision } from '../../types/claims';

interface Props {
  decision: ClaimDecision;
  formatCurrency: (val: number | undefined) => string;
}

export function DecisionBanner({ decision, formatCurrency }: Props) {
  const colorClass =
    decision.claim_decision === 'APPROVED'
      ? 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'
      : decision.claim_decision === 'PARTIALLY_APPROVED'
      ? 'bg-amber-950/20 border-amber-500/30 text-amber-300'
      : decision.claim_decision === 'REJECTED'
      ? 'bg-red-950/20 border-red-500/30 text-red-300'
      : decision.claim_decision === 'ASSISTED_REVIEW'
      ? 'bg-amber-950/20 border-amber-500/30 text-amber-300'
      : decision.claim_decision === 'MEDICAL_REVIEW'
      ? 'bg-purple-950/20 border-purple-500/30 text-purple-300'
      : 'bg-rose-950/20 border-rose-500/30 text-rose-300';

  const Icon =
    decision.claim_decision === 'APPROVED'
      ? ShieldCheck
      : decision.claim_decision === 'REJECTED'
      ? ShieldAlert
      : Shield;

  return (
    <div className={`p-4 rounded-2xl border flex items-center justify-between transition-smooth ${colorClass}`}>
      <div>
        <div className="flex items-center gap-1.5">
          <Icon className={`w-4 h-4 ${decision.claim_decision === 'APPROVED' || decision.claim_decision === 'REJECTED' ? '' : 'animate-pulse'}`} />
          <span className="text-[10px] uppercase tracking-wider font-bold">Adjudication Decision</span>
        </div>
        <h3 className="text-xl font-bold tracking-tight mt-0.5 font-display">{decision.claim_decision}</h3>
      </div>
      <div className="text-right">
        <span className="text-[10px] uppercase tracking-wider text-slate-400 block font-semibold">Total Payable</span>
        <p className="text-lg font-bold font-mono text-white">{formatCurrency(decision.total_payable)}</p>
      </div>
    </div>
  );
}
