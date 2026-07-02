/**
 * Adjudication API service layer.
 *
 * Single-responsibility module for all HTTP communication with the
 * Claims Auto-Adjudication Engine backend. Base URL is read from the
 * Vite environment variable VITE_API_URL (set in .env).
 *
 * All callers receive typed results or a typed ApiError — never raw fetch responses.
 */

import type { ClaimContext, ClaimDecision, DocumentExtractionResult } from '../types/claims';
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

/**
 * Fetch policy details from the backend's external ledger endpoint.
 */
export async function fetchPolicyFromDb(policyId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/policy/${policyId}`);
  if (!response.ok) {
    throw new Error(`Policy not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch member details from the backend's external ledger endpoint.
 */
export async function fetchMemberFromDb(memberId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/member/${memberId}`);
  if (!response.ok) {
    throw new Error(`Member not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch claims history details from the backend's external ledger endpoint.
 */
export async function fetchClaimsHistoryFromDb(policyId: string, memberId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/claims-history/${policyId}/${memberId}`);
  if (!response.ok) {
    throw new Error(`Claims history not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch benefit balances details from the backend's external ledger endpoint.
 */
export async function fetchBalancesFromDb(policyId: string, memberId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/balances/${policyId}/${memberId}`);
  if (!response.ok) {
    throw new Error(`Balances not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch lifetime state details from the backend's external ledger endpoint.
 */
export async function fetchLifetimeStateFromDb(policyId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/lifetime-state/${policyId}`);
  if (!response.ok) {
    throw new Error(`Lifetime state not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch endorsements details from the backend's external ledger endpoint.
 */
export async function fetchEndorsementsFromDb(policyId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/endorsements/${policyId}`);
  if (!response.ok) {
    throw new Error(`Endorsements not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch porting details from the backend's external ledger endpoint.
 */
export async function fetchPortingFromDb(policyId: string): Promise<any> {
  const response = await fetch(`${BASE_URL}/api/external/porting/${policyId}`);
  if (!response.ok) {
    throw new Error(`Porting credits not found: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Generate a detailed AI summary explaining a claim rejection or partial approval.
 */
export async function getClaimSummary(
  decision: ClaimDecision,
  signal?: AbortSignal
): Promise<{ summary: string }> {
  const response = await fetch(`${BASE_URL}/api/v2/adjudicate/summary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(decision),
    signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, 'Failed to generate AI adjudication summary.');
  }

  return response.json();
}

/**
 * Submit a ClaimContext to the streaming adjudication engine.
 * Streams intermediate decision traces in real-time as they run on the backend.
 */
export async function adjudicateClaimStream(
  context: ClaimContext,
  onTrace: (trace: any) => void,
  onDecision: (decision: ClaimDecision) => void,
  onError: (error: string) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/v2/adjudicate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(context),
    signal,
  });

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

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Response body reader is not available.");
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // Keep the last partial line in the buffer
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;

        try {
          const rawJson = trimmed.substring(5).trim();
          const event = JSON.parse(rawJson);

          if (event.type === "trace") {
            onTrace(event.data);
          } else if (event.type === "decision") {
            onDecision(event.data);
          } else if (event.type === "error") {
            onError(event.data);
          }
        } catch (e) {
          console.error("Failed to parse event chunk", e);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Upload a clinical document (PDF or image) and extract structured claim fields.
 *
 * The backend extracts raw text via pypdf/pytesseract, then runs it through
 * the LLM to parse structured fields. Returns a DocumentExtractionResult.
 *
 * @throws {ApiError} on non-2xx HTTP status.
 */
export async function extractDocument(
  file: File,
  signal?: AbortSignal,
): Promise<DocumentExtractionResult> {
  const form = new FormData();
  form.append('file', file, file.name);

  const response = await fetch(`${BASE_URL}/api/v2/extract-document`, {
    method: 'POST',
    body: form,
    signal,
  });

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

  return response.json() as Promise<DocumentExtractionResult>;
}
