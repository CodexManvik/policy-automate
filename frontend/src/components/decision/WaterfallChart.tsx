/**
 * WaterfallChart — Gate 6 financial computation waterfall.
 * Shows the sequential deduction pipeline from claimed amount to final payout.
 */

import type { ClaimDecision } from '../../types/claims';

interface Props {
  decision: ClaimDecision;
  formatCurrency: (val: number | undefined) => string;
}

interface WaterfallStep {
  label: string;
  value: number;
  isDeduction: boolean;
}

export function WaterfallChart({ decision, formatCurrency }: Props) {
  const { deduction_breakdown: db } = decision;

  const steps: WaterfallStep[] = [
    { label: 'Step 0: Non-Payables Excluded', value: db.non_payable_items, isDeduction: true },
    { label: 'Step 1: Room Rent Pro-Rata Deduction (Tool 2)', value: db.room_pro_rata, isDeduction: true },
    { label: 'Step 4: Annual Deductible Applied (Tool 4)', value: db.deductible, isDeduction: true },
    { label: 'Step 5: Stacked Co-Payment Applied (Tool 3)', value: db.co_payment, isDeduction: true },
  ];

  return (
    <div className="outer-shell bg-white/5 border border-white/10 rounded-[2.5rem] p-1.5 overflow-hidden">
      <div className="p-6 bg-vanta-black rounded-[calc(2.5rem-0.375rem)] flex flex-col gap-4">
        <div className="flex items-center justify-between border-b border-white/5 pb-3">
          <h4 className="text-xs font-bold text-white uppercase tracking-wider font-display">
            Gate 6: Financial Computation Waterfall
          </h4>
          <span className="text-[10px] text-slate-500 font-mono">Deduction breakdown</span>
        </div>

        <div className="flex flex-col gap-3 relative before:absolute before:left-3.5 before:top-2 before:bottom-2 before:w-[1px] before:bg-white/5">

          {/* Item 0: Claimed Amount */}
          <div className="flex items-center justify-between pl-8 relative">
            <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
              0
            </div>
            <span className="text-xs text-slate-300">Initial Claimed Amount</span>
            <span className="text-xs font-bold font-mono text-white">{formatCurrency(decision.total_claimed)}</span>
          </div>

          {steps.map((step, idx) => (
            <div key={idx} className="flex items-center justify-between pl-8 relative">
              <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                {idx + 1}
              </div>
              <span className="text-xs text-slate-400">{step.label}</span>
              <span className="text-xs font-medium font-mono text-red-400">
                -{formatCurrency(step.value)}
              </span>
            </div>
          ))}

          {/* Final payable */}
          <div className="flex items-center justify-between pl-8 relative border-t border-white/5 pt-3 mt-1">
            <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-indigo-500 flex items-center justify-center text-[8px] font-bold text-white">
              ✓
            </div>
            <span className="text-xs font-bold text-indigo-300">Payable Payout (Gate 6 Waterfall)</span>
            <span className="text-sm font-bold font-mono text-indigo-400">{formatCurrency(decision.total_payable)}</span>
          </div>

        </div>
      </div>
    </div>
  );
}
