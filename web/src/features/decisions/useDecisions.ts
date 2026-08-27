import { useState, useEffect, useCallback, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import type { DecisionsData, ProposalDetail, ProposalSummary } from './types';

const POLL_MS = 30_000;
const HISTORY_PAGE = 50;

/**
 * Data + actions for the Decisions Inbox.
 *
 * Live-first: the gateway forwards every durable-log event as a cc.* push,
 * so proposal/work events trigger an immediate (debounced) silent refresh
 * via GatewayContext's subscribe bus. A quiet 30s poll remains as the
 * fallback — a missed push costs one poll interval, never data.
 *
 * Ordering contract: oldest-first review queue. New arrivals append at the
 * bottom — the list never reshuffles under the operator's eyes.
 */
export function useDecisions() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [data, setData] = useState<DecisionsData>({
    proposals: [], dismissals: [], operatorItems: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const refresh = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    try {
      const res = await rpcRef.current('decisions.list') as DecisionsData;
      // Backend returns newest-first; the review queue reads oldest-first.
      // (operatorItems already arrive oldest-first from the OPEN query.)
      setData({
        proposals: [...(res.proposals ?? [])].reverse(),
        dismissals: [...(res.dismissals ?? [])].reverse(),
        operatorItems: res.operatorItems ?? [],
      });
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh({ silent: true });
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [connectionState, refresh]);

  // Instant refresh when the gate's state changes server-side (debounced —
  // one drain step can emit several work.*/proposal.* events back to back).
  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const evt = msg.event || '';
      if (!evt.startsWith('cc.proposal.') && !evt.startsWith('cc.work.')
          && !evt.startsWith('cc.orchestration.')) return;
      if (t) return;
      t = setTimeout(() => {
        t = null;
        refresh({ silent: true });
      }, 400);
    });
    return () => {
      unsub();
      if (t) clearTimeout(t);
    };
  }, [connectionState, subscribe, refresh]);

  const getProposal = useCallback(
    (id: string) => rpcRef.current('proposals.get', { id }) as Promise<ProposalDetail>,
    [],
  );

  // An action is DONE when the gateway has recorded the decision. The list
  // catching up is bookkeeping — awaiting it held the button in "Sending…"
  // for the length of a whole decisions.list round trip after the decision was
  // already made, which is what pushed the operator to click elsewhere and
  // meet the stale-pane bug (2026-08-16). Fire it, don't wait on it; the push
  // subscription above refreshes anyway.
  const approve = useCallback(async (id: string) => {
    await rpcRef.current('proposals.approve', { id });
    void refresh({ silent: true });
  }, [refresh]);

  const reject = useCallback(async (id: string, feedback: string) => {
    await rpcRef.current('proposals.reject', { id, feedback });
    void refresh({ silent: true });
  }, [refresh]);

  const dismiss = useCallback(async (id: string, reason: string) => {
    await rpcRef.current('proposals.dismiss', { id, reason });
    void refresh({ silent: true });
  }, [refresh]);

  const confirmDismissal = useCallback(async (id: string) => {
    await rpcRef.current('dismissals.confirm', { id });
    void refresh({ silent: true });
  }, [refresh]);

  const confirmAllDismissals = useCallback(async () => {
    await rpcRef.current('dismissals.confirm_all');
    void refresh({ silent: true });
  }, [refresh]);

  const reopenDismissal = useCallback(async (id: string, note: string) => {
    await rpcRef.current('dismissals.reopen', { id, note });
    void refresh({ silent: true });
  }, [refresh]);

  // D7 phase 2: answer a plan review / question / stall. The parked
  // orchestration resumes in the background; cc.orchestration.* pushes
  // refresh the list as the loop moves.
  const answerOperatorItem = useCallback(
    async (id: string, answer: string, verdict?: string) => {
      await rpcRef.current('operator_items.answer', { id, answer, verdict });
      await refresh({ silent: true });
    }, [refresh]);

  // History: decided proposals (EXECUTED/REJECTED/FAILED/WITHDRAWN/…), opt-in
  // and paginated. Decided rows are effectively immutable, so unlike `data`
  // this never refetches on the 30s poll or push events — only on toggle-on
  // (fresh first page, simplest) and explicit load-more.
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<ProposalSummary[]>([]);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  const toggleHistory = useCallback(() => {
    setShowHistory((prev) => {
      const next = !prev;
      if (next) {
        setHistoryLoading(true);
        rpcRef.current('decisions.history', { limit: HISTORY_PAGE, offset: 0 })
          .then((res) => {
            const r = res as { proposals: ProposalSummary[]; hasMore: boolean };
            setHistory(r.proposals ?? []);
            setHistoryHasMore(!!r.hasMore);
          })
          .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
          .finally(() => setHistoryLoading(false));
      }
      return next;
    });
  }, []);

  const loadMoreHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await rpcRef.current('decisions.history', {
        limit: HISTORY_PAGE, offset: history.length,
      }) as { proposals: ProposalSummary[]; hasMore: boolean };
      setHistory((prev) => [...prev, ...(res.proposals ?? [])]);
      setHistoryHasMore(!!res.hasMore);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setHistoryLoading(false);
    }
  }, [history.length]);

  return {
    ...data, loading, error, refresh,
    getProposal, approve, reject, dismiss,
    confirmDismissal, confirmAllDismissals, reopenDismissal,
    answerOperatorItem,
    history, historyHasMore, historyLoading, showHistory,
    toggleHistory, loadMoreHistory,
  };
}
