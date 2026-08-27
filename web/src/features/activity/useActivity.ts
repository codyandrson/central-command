/**
 * useActivity — data hook for the Activity screen (2026-08-11 activity-coverage
 * spec: docs/superpowers/specs/2026-08-11-activity-coverage-design.md).
 *
 * Three reads behind one hook, matching the screen's three tabs:
 *  - `overview`  — CURRENT STATE, one composed payload (never history)
 *  - `runs`      — every session, faceted, keyset-paged
 *  - `events`    — the durable log, kind-faceted, cursor-paged backwards
 *
 * Paging is Load more, never infinite scroll: the research behind the spec is
 * unanimous that infinite scroll serves discovery feeds and hurts refinding,
 * and refinding is the entire purpose of this screen.
 *
 * Runs paging is KEYSET on `(created_at, id)`, not offset — sessions are
 * created while the operator pages, and an offset cursor would silently skip
 * or repeat a row every time one lands.
 *
 * Like useGraph, every method degrades to an `error` string rather than
 * throwing, so a tab renders a fallback instead of taking the screen down.
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { apiFetch } from '@/lib/apiFetch';

/* ── Wire shapes ──────────────────────────────────────────────────────
 * Hand-declared over untyped RPC/REST payloads, which CLAUDE.md warns is a
 * place fields go missing silently. The backend guards these: the Runs facets
 * and the coverage property are pinned by tests/test_activity_coverage.py.
 */

export interface DispatchStatus {
  running: boolean;
  may_dispatch: boolean;
  reason: string | null;
  in_flight: number;
  awaiting_human: number;
  tokens_spent: number;
  valves: { concurrency: number; approval_limit: number; token_budget: number };
  ledger: Record<string, number>;
}

export interface WorkFailure {
  id: string;
  subject: string | null;
  kind: string;
  source: string;
  state: string;
  session_id: string | null;
  attempts: number;
  last_error: string | null;
  enrolled_at: string;
  terminal_at: string | null;
}

export interface ScheduleLiveness {
  id: string;
  name: string;
  enabled: boolean;
  action_kind: string;
  last_fired_at: string | null;
}

export interface ActivityOverview {
  dispatch: DispatchStatus;
  oldest_unprocessed_at: string | null;
  failures: WorkFailure[];
  dismiss_pending: number;
  heartbeat: { running: boolean; reason: string | null; tick_seconds: number };
  schedules: ScheduleLiveness[];
}

export interface RunRow {
  id: string;
  agent_id: string;
  status: string;
  mode: string;
  created_at: string;
  updated_at: string | null;
  closed_reason: string | null;
  parent_session_id: string | null;
  task_title: string | null;
  /** The other half of "what is this run FOR" — 70% of runs are ledger drains
   *  with no task, and without this the row would print a uuid. */
  work_subject: string | null;
  proposal_count: number;
  token_estimate: number;
}

export interface EventRow {
  id: string;
  kind: string;
  ref_id: string | null;
  actor: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface EventKind { kind: string; count: number }

export interface RunFacets {
  agent_id?: string;
  status?: string;
  mode?: string;
}

const RUNS_PAGE = 50;
const EVENTS_PAGE = 100;

/** Every `/api/activity/*` response is `{ ok, result }` — fetch and unwrap it. */
async function fetchResult<T>(url: string, init?: RequestInit): Promise<T> {
  const { result } = await apiFetch<{ result: T }>(url, init);
  return result;
}

/** One drain step emits a burst of events — collapse them into one refetch. */
const LIVE_DEBOUNCE_MS = 2000;

/**
 * Live refresh: any gateway push means the projections behind this screen MAY
 * have moved, so refetch — the push itself carries no replay and is not a
 * substitute for the query (same reasoning as the notifications diff).
 *
 * The CALLER decides whether it is safe to refetch right now: the paged tabs
 * refuse while the operator has scrolled, because a refetch that prepends new
 * rows would move what he is reading (the no-displacement rule). He sees them
 * on the next scroll to top, or via the Refresh button.
 */
export function useLiveRefresh(refresh: () => void) {
  const { connectionState, subscribe } = useGateway();
  const ref = useRef(refresh);
  useEffect(() => { ref.current = refresh; }, [refresh]);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe(() => {
      if (t) return;
      t = setTimeout(() => { t = null; ref.current(); }, LIVE_DEBOUNCE_MS);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, subscribe]);
}

export function useActivity() {
  const [error, setError] = useState<string | null>(null);

  const [overview, setOverview] = useState<ActivityOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);

  const [runs, setRuns] = useState<RunRow[]>([]);
  const [runAgents, setRunAgents] = useState<string[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsExhausted, setRunsExhausted] = useState(false);

  const [events, setEvents] = useState<EventRow[]>([]);
  const [eventKinds, setEventKinds] = useState<EventKind[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsExhausted, setEventsExhausted] = useState(false);

  /** Guards against a slow first page landing after a facet change and
   *  overwriting the newer result — the list would then disagree with the
   *  facet bar above it, which is the one thing a filter must never do. */
  const runsGeneration = useRef(0);
  const eventsGeneration = useRef(0);

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const result = await fetchResult<ActivityOverview>('/api/activity/overview');
      setOverview(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async (facets: RunFacets, cursor?: RunRow) => {
    const generation = cursor ? runsGeneration.current : ++runsGeneration.current;
    setRunsLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(RUNS_PAGE) });
      if (facets.agent_id) params.set('agent_id', facets.agent_id);
      if (facets.status) params.set('status', facets.status);
      if (facets.mode) params.set('mode', facets.mode);
      if (cursor) {
        params.set('before', cursor.created_at);
        params.set('before_id', cursor.id);
      }
      const result = await fetchResult<{ sessions: RunRow[]; agents: string[] }>(
        `/api/activity/runs?${params.toString()}`,
      );
      if (generation !== runsGeneration.current) return;
      setRuns(prev => (cursor ? [...prev, ...result.sessions] : result.sessions));
      setRunAgents(result.agents);
      setRunsExhausted(result.sessions.length < RUNS_PAGE);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadEvents = useCallback(async (kind: string | undefined, cursor?: EventRow) => {
    const generation = cursor ? eventsGeneration.current : ++eventsGeneration.current;
    setEventsLoading(true);
    try {
      // order=desc + before is the backwards page the API already documents.
      const params = new URLSearchParams({ limit: String(EVENTS_PAGE), order: 'desc' });
      if (kind) params.set('kind', kind);
      if (cursor) params.set('before', cursor.id);
      const result = await fetchResult<{ events: EventRow[] }>(
        `/api/activity/events?${params.toString()}`,
      );
      if (generation !== eventsGeneration.current) return;
      setEvents(prev => (cursor ? [...prev, ...result.events] : result.events));
      setEventsExhausted(result.events.length < EVENTS_PAGE);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEventsLoading(false);
    }
  }, []);

  const loadEventKinds = useCallback(async () => {
    try {
      const result = await fetchResult<{ kinds: EventKind[] }>('/api/activity/event-kinds');
      setEventKinds(result.kinds);
    } catch {
      // The facet vocabulary is an affordance; losing it must not blank the
      // list beside it. The events themselves carry their own kind.
    }
  }, []);

  /** Requeue or acknowledge a failed work item, then refresh the overview so
   *  the strip reflects what actually happened rather than what we assumed. */
  const actOnFailure = useCallback(async (id: string, action: 'requeue' | 'ack') => {
    try {
      await apiFetch(`/api/activity/work/${encodeURIComponent(id)}/${action}`, {
        method: 'POST',
      });
      setError(null);
      await loadOverview();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    }
  }, [loadOverview]);

  return {
    error,
    overview, overviewLoading, loadOverview,
    runs, runAgents, runsLoading, runsExhausted, loadRuns,
    events, eventKinds, eventsLoading, eventsExhausted, loadEvents, loadEventKinds,
    actOnFailure,
  };
}
