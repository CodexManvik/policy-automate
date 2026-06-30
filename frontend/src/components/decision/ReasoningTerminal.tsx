/**
 * ReasoningTerminal — semantic agent reasoning output display.
 * Renders decision traces from validation gates, including collapsible
 * thinking process excerpts for model reasoning transparency.
 */

import type { DecisionTrace } from '../../types/claims';

interface Props {
  traces: DecisionTrace[];
}

/** Splits <think>...</think> or <|thought|>...</|thought|> out of a reason string. */
function parseReasoningText(text: string): { thinking: string; cleanText: string } {
  const thinkRegex = /<(?:\|thought\||think)>([\s\S]*?)<\/(?:\|thought\||think)>/i;
  const match = text.match(thinkRegex);
  if (match) {
    return { thinking: match[1].trim(), cleanText: text.replace(thinkRegex, '').trim() };
  }
  return { thinking: '', cleanText: text };
}

export function ReasoningTerminal({ traces }: Props) {
  return (
    <div className="outer-shell bg-white/5 border border-white/10 rounded-2xl p-1 overflow-hidden">
      <div className="bg-black/90 font-mono text-xs p-4 rounded-[calc(2rem-0.75rem)] flex flex-col gap-2 min-h-[160px] border border-white/5 shadow-inner">
        <div className="flex items-center justify-between border-b border-white/5 pb-2 mb-1">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
            <span className="text-[10px] uppercase tracking-wider text-slate-400 font-bold">
              Semantic Agent Reasoning Terminal
            </span>
          </div>
          <span className="text-[9px] uppercase tracking-wider text-slate-600 font-medium">gemma4-e4b-qat</span>
        </div>

        <div className="flex-1 flex flex-col gap-2 overflow-y-auto max-h-[220px] pr-2">
          <p className="text-[10px] text-slate-500">{'[SYS] Loaded LLM template: <|turn>system <|think|>...'}</p>
          <p className="text-[10px] text-slate-500">{'[SYS] Executing semantic rules validation...'}</p>

          {traces.length > 0 ? (
            traces.map((trace, index) => {
              const { thinking, cleanText } = parseReasoningText(trace.reason);
              return (
                <div
                  key={index}
                  className="flex flex-col gap-1 border-t border-white/5 pt-2 mt-1 first:border-0 first:pt-0 first:mt-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] uppercase text-violet-400 font-bold">{trace.rule_id}</span>
                    <span
                      className={`text-[8px] px-1 py-0.5 rounded font-semibold ${
                        trace.evaluation === 'PASSED'
                          ? 'bg-emerald-950 text-emerald-400'
                          : 'bg-red-950 text-red-400'
                      }`}
                    >
                      {trace.evaluation}
                    </span>
                    {trace.confidence !== undefined && (
                      <span className="text-[8px] text-slate-500">
                        {(trace.confidence * 100).toFixed(0)}% conf
                      </span>
                    )}
                  </div>
                  {thinking && (
                    <details className="text-[10px] text-slate-500 pl-2 border-l border-white/10 mt-0.5 cursor-pointer select-none">
                      <summary className="hover:text-slate-400 transition-smooth">View Thinking Process...</summary>
                      <div className="mt-1 pl-2 border-l border-dashed border-white/5 whitespace-pre-wrap font-mono text-[9px] text-slate-600 bg-white/2 p-2 rounded">
                        {thinking}
                      </div>
                    </details>
                  )}
                  <p className="text-[11px] text-slate-300 leading-relaxed pl-2 border-l border-white/10 italic">
                    {cleanText}
                  </p>
                </div>
              );
            })
          ) : (
            <p className="text-[11px] text-slate-400 italic">
              No semantic agent reasoning traces generated for this claim.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
