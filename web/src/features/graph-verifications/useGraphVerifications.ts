/**
 * useGraphVerifications — data hook for the cockpit Graph Verifications panel
 * (Task 6, docs/superpowers/plans/2026-08-19-graph-verification-auditor.md).
 *
 * Talks to the cc-graph Hono proxy (`/api/graph/verifications*`), which
 * forwards to `GET/POST /api/graph/verifications*` on the Central Command API —
 * pinned by `tests/test_graph_verification.py::test_verifications_wire_shape_for_the_cockpit`.
 * Fields below must match that wire shape exactly (hand-declared interfaces
 * over untyped RPC payloads is how a field silently stops rendering).
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/apiFetch';

const POLL_MS = 30_000;

export interface VerificationMechanical {
  missing: boolean;
  empty_delta?: boolean;
  unembedded?: string[];
  no_approved_text?: boolean;
}

export interface DeltaEntity {
  name: string;
  labels: string[];
  has_embedding: boolean;
  summary: string;
  /** true = created by THIS episode; false = pre-existing node the episode
   *  touched; absent on deltas audited before 2026-08-20 (not re-derived). */
  new?: boolean;
  created_at?: string | null;
}

export interface DeltaEdge {
  source: string;
  name: string;
  target: string;
  fact: string;
  valid_at: string | null;
  invalid_at: string | null;
  /** Same tri-state as DeltaEntity.new. */
  new?: boolean;
  created_at?: string | null;
}

export interface DeltaInvalidated {
  source: string;
  name: string;
  target: string;
  fact: string;
  attributed_by: string;
  expired_at: string | null;
}

export interface VerificationDelta {
  entities: DeltaEntity[];
  edges: DeltaEdge[];
  invalidated: DeltaInvalidated[];
}

export interface VerificationRow {
  id: string;
  proposal_id: string;
  episode_name: string;
  group_id: string;
  scope: string;
  status: 'AWAITING_OPERATOR' | 'VERIFIED' | 'PROBLEM';
  episode_uuid: string | null;
  mechanical: VerificationMechanical;
  delta: VerificationDelta | null;
  verdict: 'aligned' | 'flag' | null;
  verdict_rationale: string | null;
  has_invalidations: boolean;
  problem_note: string | null;
  /** Set on a re-check row: the PROBLEM row this one re-verifies (its own
   *  proposal_id is the curation proposal that applied the fix). */
  remediation_of: string | null;
  created_at: string;
  closed_at: string | null;
  closed_by: string | null;
  /** Only present on `awaiting` rows — the operator's decision is "does the
   *  delta match THIS claim", so it must ride along with the row it explains. */
  approved_text?: string | null;
}

export interface VerificationsData {
  enabled: boolean;
  mode: 'shadow' | 'active';
  awaiting: VerificationRow[];
  recent_closed: VerificationRow[];
}

const EMPTY: VerificationsData = { enabled: false, mode: 'shadow', awaiting: [], recent_closed: [] };

/** Every `/api/graph/verifications*` response is `{ ok, result }` — fetch and unwrap it. */
async function fetchResult<T>(url: string, init?: RequestInit): Promise<T> {
  const { result } = await apiFetch<{ result: T }>(url, init);
  return result;
}

export function useGraphVerifications() {
  const [data, setData] = useState<VerificationsData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    try {
      setData(await fetchResult<VerificationsData>('/api/graph/verifications'));
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  useEffect(() => {
    refreshRef.current();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refreshRef.current({ silent: true });
    }, POLL_MS);
    return () => clearInterval(iv);
  }, []);

  const confirm = useCallback(async (id: string) => {
    await apiFetch(`/api/graph/verifications/${id}/confirm`, { method: 'POST' });
    await refresh({ silent: true });
  }, [refresh]);

  const problem = useCallback(async (id: string, note: string, remediate: boolean) => {
    await apiFetch(`/api/graph/verifications/${id}/problem`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, remediate }),
    });
    await refresh({ silent: true });
  }, [refresh]);

  return { ...data, loading, error, refresh, confirm, problem };
}
