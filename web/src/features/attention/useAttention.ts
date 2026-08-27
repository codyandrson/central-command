import { useState, useEffect, useCallback, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';

/**
 * Attention counts for the top-bar tab badges — "there is something here that
 * awaits you", per view, so a new decision (or a stalled orchestration) shows
 * even while you are in another tab.
 *
 * Each count is an HONEST "awaits your action" number, and the three are
 * non-overlapping so the same item never double-signals:
 *   - decisions : proposals + dismissals + orchestrator asks (decisions.list)
 *   - tasks     : NEW tasks awaiting routing/assignment (REVIEW tasks are
 *                 represented by their proposal in `decisions`, not here)
 *   - chat      : conversations resting AWAITING_OPERATOR (your turn to reply)
 *   - verify    : graph verifications parked AWAITING_OPERATOR (post-execution
 *                 QC — added 2026-08-20 after twelve parked items accumulated
 *                 invisibly on a badge-less tab)
 *
 * Live-first: the gateway forwards every durable-log event as a cc.* push;
 * relevant kinds trigger a debounced refetch. A quiet poll is the fallback.
 */
export interface AttentionCounts {
  decisions: number;
  tasks: number;
  chat: number;
  verify: number;
}

const ZERO: AttentionCounts = { decisions: 0, tasks: 0, chat: 0, verify: 0 };
const POLL_MS = 30_000;

interface DecisionsListShape {
  proposals?: unknown[];
  dismissals?: unknown[];
  operatorItems?: unknown[];
}
interface SessionRow { status?: string }
interface SessionsListShape { sessions?: SessionRow[] }
interface KanbanTasksShape { items?: { status?: string }[] }

export function useAttention(): AttentionCounts {
  const { rpc, connectionState, subscribe } = useGateway();
  const [counts, setCounts] = useState<AttentionCounts>(ZERO);
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const refresh = useCallback(async () => {
    try {
      const [decisions, sessions] = await Promise.all([
        rpcRef.current('decisions.list') as Promise<DecisionsListShape>,
        rpcRef.current('sessions.list', { limit: 200 }) as Promise<SessionsListShape>,
      ]);
      // Tasks come over REST (the kanban adapter), not the WS gateway.
      let newTasks = 0;
      try {
        const res = await fetch('/api/kanban/tasks?limit=200');
        if (res.ok) {
          const data = (await res.json()) as KanbanTasksShape;
          newTasks = (data.items ?? []).filter((t) => t.status === 'NEW').length;
        }
      } catch { /* board optional — leave tasks at 0 */ }

      // Graph verifications are REST too — and the cc-graph proxy WRAPS its
      // responses as {ok, result} (unlike /api/kanban above, which is bare).
      // Reading `data.awaiting` here shipped a badge that was silently 0
      // forever (2026-08-20) — the exact untyped-payload class the wire-shape
      // rule warns about.
      let verify = 0;
      try {
        const res = await fetch('/api/graph/verifications');
        if (res.ok) {
          const data = (await res.json()) as { result?: { awaiting?: unknown[] } };
          verify = data.result?.awaiting?.length ?? 0;
        }
      } catch { /* panel optional — leave verify at 0 */ }

      setCounts({
        decisions:
          (decisions.proposals?.length ?? 0) +
          (decisions.dismissals?.length ?? 0) +
          (decisions.operatorItems?.length ?? 0),
        tasks: newTasks,
        chat: (sessions.sessions ?? []).filter((s) => s.status === 'AWAITING_OPERATOR').length,
        verify,
      });
    } catch {
      /* transient — keep the last good counts rather than flashing zero */
    }
  }, []);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [connectionState, refresh]);

  // Live refresh on the event families that change any of the three counts.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const evt = msg.event || '';
      if (
        !evt.startsWith('cc.proposal.') &&
        !evt.startsWith('cc.work.') &&
        !evt.startsWith('cc.orchestration.') &&
        !evt.startsWith('cc.task.') &&
        !evt.startsWith('cc.session.') &&
        !evt.startsWith('cc.conversation.') &&
        // An agent asking, or opening its own discussion lane, changes both
        // `decisions` (a new operator item) and `chat` (a new
        // AWAITING_OPERATOR row). Without this leg the badge was as late as
        // the 30s poll — for the one class of event where the agent is
        // standing still waiting.
        !evt.startsWith('cc.agent.') &&
        // graph.verification.parked / .confirmed / .problem move `verify`.
        !evt.startsWith('cc.graph.verification.')
      ) return;
      if (t) return;
      t = setTimeout(() => { t = null; refresh(); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, subscribe, refresh]);

  return counts;
}
