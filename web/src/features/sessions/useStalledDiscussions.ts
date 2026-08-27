import { useCallback, useEffect, useRef, useState } from 'react';
import { useGateway } from '@/contexts/GatewayContext';

/**
 * Which tier-3 clarification discussions the stall sweep has flagged, keyed by
 * discussion session id.
 *
 * Read from the DURABLE record — the open operator items in `decisions.list`,
 * which carry `stalled_at` and the blocked task's title — rather than from the
 * `cc.discussion.stalled` push alone. The sweep flags a lane ONCE per quiet
 * window, so a cockpit that only listened for the event would lose the amber
 * on the next reload and never be told again. The push is a refresh hint, the
 * table is the truth.
 */
export interface StalledDiscussion {
  /** The task this discussion blocks — its title when there is one, else its id. */
  taskTitle: string;
  /** When the sweep flagged it, epoch ms. */
  stalledAt: number;
}

interface OperatorItemShape {
  status?: string;
  task_id?: string | null;
  task_title?: string | null;
  discussion_session_id?: string | null;
  stalled_at?: string | null;
}

interface DecisionsListShape {
  operatorItems?: OperatorItemShape[];
}

const POLL_MS = 60_000;

function sameMap(
  a: Record<string, StalledDiscussion>,
  b: Record<string, StalledDiscussion>,
): boolean {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((k) =>
    b[k] !== undefined
    && b[k].taskTitle === a[k].taskTitle
    && b[k].stalledAt === a[k].stalledAt);
}

export function useStalledDiscussions(): Record<string, StalledDiscussion> {
  const { rpc, connectionState, subscribe } = useGateway();
  const [stalled, setStalled] = useState<Record<string, StalledDiscussion>>({});
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);
  // The 60s poll and the push-triggered refresh can be in flight together, and
  // nothing orders their replies. Without this, a slow earlier request can
  // resolve LAST and resurrect a flag the newer reply had already dropped —
  // the amber would go on accusing the operator for up to a minute after they
  // answered, which is the precise state the chip exists to deny.
  const seqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++seqRef.current;
    try {
      const res = await rpcRef.current('decisions.list') as DecisionsListShape;
      if (seq !== seqRef.current) return;   // a newer refresh already answered
      const next: Record<string, StalledDiscussion> = {};
      for (const item of res.operatorItems ?? []) {
        if (item.status && item.status !== 'OPEN') continue;
        if (!item.discussion_session_id || !item.stalled_at || !item.task_id) continue;
        const at = Date.parse(item.stalled_at);
        if (Number.isNaN(at)) continue;
        next[item.discussion_session_id] = {
          taskTitle: item.task_title || item.task_id,
          stalledAt: at,
        };
      }
      // Keep the previous object when nothing changed: this map feeds every
      // row of the sessions panel, and a fresh object each poll would
      // re-render the whole list for no new information.
      setStalled((prev) => (sameMap(prev, next) ? prev : next));
    } catch {
      /* transient — keep the last good map rather than flashing the amber away */
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

  // Live hints: a fresh flag, an answered ask, or a concluded discussion all
  // change what should be amber.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const evt = msg.event || '';
      if (
        !evt.startsWith('cc.discussion.')
        && !evt.startsWith('cc.orchestration.')
        && !evt.startsWith('cc.conversation.')
      ) return;
      if (t) return;
      t = setTimeout(() => { t = null; refresh(); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, subscribe, refresh]);

  return stalled;
}
