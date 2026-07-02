/**
 * useAdjudication — Manages the loading/error/decision lifecycle for claim submission.
 *
 * Encapsulates all API interaction state so components only deal with
 * typed results, not raw fetch mechanics.
 */

import { useState, useCallback, useRef } from 'react';
import type { ClaimContext, ClaimDecision, DecisionTrace } from '../types/claims';
import { ApiError } from '../types/claims';
import { adjudicateClaimStream } from '../services/adjudicationApi';

export interface UseAdjudicationReturn {
  decision: ClaimDecision | null;
  liveTraces: DecisionTrace[];
  loading: boolean;
  error: string | null;
  submitStream: (
    context: ClaimContext,
    onTraceAdded?: (trace: DecisionTrace) => void
  ) => Promise<void>;
  reset: () => void;
}

export function useAdjudication(): UseAdjudicationReturn {
  const [decision, setDecision] = useState<ClaimDecision | null>(null);
  const [liveTraces, setLiveTraces] = useState<DecisionTrace[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Hold a ref to the AbortController so in-flight requests can be cancelled
  // when the component unmounts or when reset() is called.
  const abortRef = useRef<AbortController | null>(null);

  const submitStream = useCallback(async (
    context: ClaimContext,
    onTraceAdded?: (trace: DecisionTrace) => void
  ): Promise<void> => {
    // Cancel any previous in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);
    setDecision(null);
    setLiveTraces([]);

    try {
      await adjudicateClaimStream(
        context,
        (trace) => {
          setLiveTraces((prev) => {
            const next = [...prev, trace];
            if (onTraceAdded) {
              onTraceAdded(trace);
            }
            return next;
          });
        },
        (finalDecision) => {
          setDecision(finalDecision);
        },
        (errText) => {
          setError(errText);
        },
        controller.signal
      );
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // Request was intentionally cancelled — do not set error state
        return;
      }
      if (err instanceof ApiError) {
        setError(`Server error ${err.status}: ${err.message}`);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('An unknown error occurred. Ensure the backend server is running on the configured API URL.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setDecision(null);
    setLiveTraces([]);
    setError(null);
    setLoading(false);
  }, []);

  return { decision, liveTraces, loading, error, submitStream, reset };
}
