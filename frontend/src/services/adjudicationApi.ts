/**
 * Adjudication API service layer.
 *
 * Single-responsibility module for all HTTP communication with the
 * Claims Auto-Adjudication Engine backend. Base URL is read from the
 * Vite environment variable VITE_API_URL (set in .env).
 *
 * All callers receive typed results or a typed ApiError — never raw fetch responses.
 */

import type { ClaimContext, ClaimDecision } from '../types/claims';
import { ApiError } from '../types/claims';

const BASE_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

const DEFAULT_TIMEOUT_MS = 300_000; // 5 minutes: LLM calls can be slow

/**
 * Submit a ClaimContext to the adjudication engine and return the ClaimDecision.
 *
 * @param context - Fully-assembled claim context payload.
 * @param signal  - Optional AbortSignal for request cancellation.
 * @throws {ApiError} on non-2xx HTTP status.
 * @throws {Error}    on network failure or timeout.
 */
export async function adjudicateClaim(
  context: ClaimContext,
  signal?: AbortSignal,
): Promise<ClaimDecision> {
  const controller = new AbortController();
  // Combine caller-provided signal with an internal timeout signal.
  const timeoutId = setTimeout(() => controller.abort('timeout'), DEFAULT_TIMEOUT_MS);
  const combinedSignal = signal
    ? AbortSignal.any([signal, controller.signal])
    : controller.signal;

  try {
    const response = await fetch(`${BASE_URL}/api/v2/adjudicate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(context),
      signal: combinedSignal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      let detail: string;
      try {
        const err = (await response.json()) as { detail?: string };
        detail = err.detail ?? response.statusText;
      } catch {
        detail = response.statusText;
      }
      throw new ApiError(response.status, detail);
    }

    return (await response.json()) as ClaimDecision;
  } catch (err) {
    clearTimeout(timeoutId);
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('Request was cancelled or timed out.');
    }
    throw err;
  }
}

/**
 * Fetch the backend health status.
 * Returns the raw JSON object — callers decide how to display it.
 */
export async function fetchHealth(): Promise<Record<string, unknown>> {
  const response = await fetch(`${BASE_URL}/health`);
  return (await response.json()) as Record<string, unknown>;
}
