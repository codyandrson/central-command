/* eslint-disable react-refresh/only-export-components -- hook co-located with its provider, as elsewhere in contexts/ */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { classifyEvent, isErrorKind } from './tiers';
import { loadSeen, loadToastMode, saveSeen, type SeenSet } from './seen';

/**
 * The interrupt tier: what is BLOCKED ON THE OPERATOR right now, and what is new about
 * it since the last time they were told.
 *
 * ARCHITECTURE — a toast is a PROJECTION DIFF, not a rendered push.
 * `_forward_events` fans the durable log out over the WS with no replay, so a
 * push proves only that something changed; it cannot tell you what the
 * operator has and has not already seen, and it is gone forever if the socket
 * blinks. So a push is treated as a REFRESH HINT: refetch `decisions.list` +
 * `sessions.list`, build the set of things awaiting the operator, and toast the ids
 * that are not in the persisted seen-set. The same diff runs on every
 * (re)connect, which is how the reconnect gap survives.
 *
 * Cold start SEEDS the seen-set and toasts nothing: opening the cockpit to a
 * wall of toasts for a queue that has been sitting there all morning teaches
 * the operator to ignore toasts.
 *
 * Errors are the one exception — a failure has no row in either projection, so
 * it is toasted straight off the push and accepted as lossy (the durable log
 * still has it; the Decisions/Activity screens are where it is not lossy).
 *
 * NO SOUND. Deliberate: audio is a whole-body interrupt, and the doctrine's
 * interrupt tier is "look at this next", not "stop what you are doing".
 */

export interface ToastTarget {
  kind: 'decision' | 'session';
  /** Operator-item id to select on arrival (decision targets only). */
  itemId?: string;
  /** Session key to open (session targets only). */
  sessionKey?: string;
}

export interface Toast {
  /** Stable per ITEM, never per event — the same parked item toasts once. */
  id: string;
  title: string;
  body?: string;
  severity: 'attention' | 'error';
  target: ToastTarget | null;
  at: number;
}

interface NotificationsValue {
  toasts: Toast[];
  dismiss: (id: string) => void;
}

const NotificationsContext = createContext<NotificationsValue | null>(null);

/** One drain step emits several events back to back — collapse them. */
const DEBOUNCE_MS = 400;
/** Cap the stack so a burst cannot cover the app. */
const MAX_TOASTS = 4;

interface ProposalRow { id?: string; agent_id?: string; intent?: string }
interface OperatorItemRow {
  id?: string; agent_id?: string; kind?: string; body?: string;
  task_title?: string | null; discussion_session_id?: string | null;
}
interface DecisionsShape { proposals?: ProposalRow[]; operatorItems?: OperatorItemRow[] }
interface SessionRow {
  key?: string; sessionKey?: string; agentId?: string; status?: string;
  openedBy?: string; blockedTaskTitle?: string | null;
}
interface SessionsShape { sessions?: SessionRow[] }

/**
 * Everything awaiting the operator, as stable ids with the words to say about them.
 * PURE so the diff rule can be tested without a socket.
 */
export function buildInterruptItems(
  decisions: DecisionsShape, sessions: SessionsShape,
): Array<Omit<Toast, 'at'>> {
  const out: Array<Omit<Toast, 'at'>> = [];

  for (const p of decisions.proposals ?? []) {
    if (!p.id) continue;
    out.push({
      id: `proposal:${p.id}`,
      title: `${p.agent_id || 'An agent'} proposed a change`,
      body: p.intent || undefined,
      severity: 'attention',
      target: { kind: 'decision' },
    });
  }

  for (const i of decisions.operatorItems ?? []) {
    if (!i.id) continue;
    // An ask that opened its own lane is answered IN THE LANE — pointing the
    // operator at the inbox row would land them on a disabled answer box.
    const target: ToastTarget = i.discussion_session_id
      ? { kind: 'session', sessionKey: `agent:${i.agent_id}:${i.discussion_session_id}` }
      : { kind: 'decision', itemId: i.id };
    out.push({
      id: `item:${i.id}`,
      title: i.kind === 'stall'
        ? `${i.agent_id || 'An agent'} is stalled and needs you`
        : `${i.agent_id || 'An agent'} asked you a question`,
      body: i.task_title ? `${i.body || ''} · blocks ${i.task_title}` : i.body || undefined,
      severity: 'attention',
      target,
    });
  }

  for (const s of sessions.sessions ?? []) {
    const key = s.sessionKey || s.key;
    if (!key) continue;
    if (s.status !== 'AWAITING_OPERATOR') continue;
    // Only lanes the AGENT opened. A conversation the operator started and left resting
    // is not an interrupt — he knows it is there; he opened it.
    if (s.openedBy !== 'agent') continue;
    out.push({
      id: `session:${key}`,
      title: `${s.agentId || 'An agent'} opened a discussion`,
      body: s.blockedTaskTitle ? `blocks ${s.blockedTaskTitle}` : undefined,
      severity: 'attention',
      target: { kind: 'session', sessionKey: key },
    });
  }

  return out;
}

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { rpc, connectionState, subscribe } = useGateway();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  // The seen-set is a ref, not state: it must not re-render anything, and the
  // diff has to read the value written by the previous diff even when two land
  // inside one React batch.
  const seenRef = useRef<SeenSet | null>(null);
  const seededRef = useRef(false);

  const push = useCallback((t: Toast) => {
    // Mode is read here, not at subscribe time: the diff and the seen-set must
    // run identically whatever the operator has toasts set to.
    const mode = loadToastMode();
    if (mode === 'off' || (mode === 'errors' && t.severity !== 'error')) return;
    setToasts((prev) => {
      if (prev.some((x) => x.id === t.id)) return prev;
      return [...prev, t].slice(-MAX_TOASTS);
    });
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => (prev.some((t) => t.id === id) ? prev.filter((t) => t.id !== id) : prev));
  }, []);

  /** Refetch both projections, diff against the seen-set, toast what is new. */
  const refreshAndDiff = useCallback(async () => {
    let decisions: DecisionsShape;
    let sessions: SessionsShape;
    try {
      [decisions, sessions] = await Promise.all([
        rpcRef.current('decisions.list') as Promise<DecisionsShape>,
        rpcRef.current('sessions.list', { limit: 200 }) as Promise<SessionsShape>,
      ]);
    } catch {
      // Transient. Say nothing rather than guess — and crucially do NOT seed,
      // or a failed first fetch would silence the real first diff.
      return;
    }

    const items = buildInterruptItems(decisions, sessions);
    const ids = items.map((i) => i.id);
    const now = Date.now();
    const seen = seenRef.current ?? loadSeen();

    if (!seededRef.current) {
      // COLD START: everything already awaiting him is, by definition, not
      // news. Seed and stay quiet.
      seededRef.current = true;
      const seeded: SeenSet = { ...seen };
      for (const id of ids) if (seeded[id] === undefined) seeded[id] = now;
      seenRef.current = saveSeen(seeded, ids, now);
      return;
    }

    const next: SeenSet = { ...seen };
    for (const item of items) {
      if (next[item.id] !== undefined) continue;
      next[item.id] = now;
      push({ ...item, at: now });
    }
    seenRef.current = saveSeen(next, ids, now);
  }, [push]);

  // (Re)connect: the reconnect gap is exactly where a push-only design loses
  // items, so every transition into 'connected' runs the diff.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    void refreshAndDiff();
  }, [connectionState, refreshAndDiff]);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const kind = msg.event || '';
      if (classifyEvent(kind) !== 'interrupt') return;

      if (isErrorKind(kind)) {
        // No projection row exists for a failure — toast the push itself.
        const p = (msg.payload || {}) as {
          id?: string; ref_id?: string; payload?: Record<string, unknown>;
        };
        const detail = p.payload || {};
        const text = String(detail.error ?? detail.message ?? detail.reason ?? '');
        push({
          id: `event:${p.id ?? p.ref_id ?? `${kind}:${Date.now()}`}`,
          title: kind.replace(/^cc\./, '').replace(/[._]/g, ' '),
          body: text || undefined,
          severity: 'error',
          target: null,
          at: Date.now(),
        });
        return;
      }

      if (t) return;
      t = setTimeout(() => { t = null; void refreshAndDiff(); }, DEBOUNCE_MS);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, subscribe, refreshAndDiff, push]);

  const value = useMemo<NotificationsValue>(() => ({ toasts, dismiss }), [toasts, dismiss]);
  return (
    <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>
  );
}

export function useNotifications(): NotificationsValue {
  const ctx = useContext(NotificationsContext);
  // Unlike the gateway, a missing provider here must not crash the app: a
  // notification is an enhancement, and the durable inbox is the real record.
  return ctx ?? { toasts: [], dismiss: () => {} };
}
