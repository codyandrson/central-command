import { useCallback, useEffect, useRef, useState } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import type { OutageDependency, OutageRetryResult, OutagesPayload } from './types';

const POLL_MS = 30_000;
const RESULT_MS = 6_000;

/**
 * Every durable-log event that changes what the outages surface shows. The
 * relay (`nerve_gateway._forward_events`) pushes EVERY event as `cc.<kind>`,
 * exactly as `useDecisions` subscribes (`cc.proposal.`, `cc.work.`), so these
 * are the real names. The first group is the list the story named; the second
 * is the other half of each pair — the events that say a park was DRIVEN,
 * DISMISSED or REQUESTED, without which a banner's count would only fall at the
 * next 30 s poll.
 */
const REFRESH_EVENTS = new Set([
  'cc.dependency.down',
  'cc.dependency.up',
  'cc.session.resume_parked',
  'cc.proposal.execution_parked',
  'cc.proposal.execution_interrupted',
  'cc.task.run_parked',
  'cc.work.released',
  'cc.retry.failing_despite_probe',
  // …and the ones that take work back out of the count.
  'cc.session.resume_retried',
  'cc.proposal.execution_retried',
  'cc.proposal.execution_dismissed',
  'cc.task.run_retried',
  'cc.outage.retry_requested',
]);

/** "probe still down" / "drove 5" — the retry answer, in a few words. */
export function retryResultText(r: OutageRetryResult): string {
  if (r.probe === 'down') return 'probe still down';
  const n = Object.values(r.driven).reduce((a, b) => a + b, 0);
  return `drove ${n}`;
}

/**
 * Data + actions for the outage banners.
 *
 * Live-first, like `useDecisions`: the gateway forwards each durable event as a
 * `cc.*` push through GatewayContext.subscribe, which triggers a debounced
 * silent refresh; a quiet 30 s poll is the fallback — a missed push costs one
 * poll interval, never data. The server owns every number; this renders them.
 */
export function useOutages() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [dependencies, setDependencies] = useState<OutageDependency[]>([]);
  const [error, setError] = useState('');
  const [results, setResults] = useState<Record<string, string>>({});
  const [retrying, setRetrying] = useState<string>('');
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const refresh = useCallback(async () => {
    try {
      const res = await rpcRef.current('outages.list') as OutagesPayload;
      setDependencies(res.dependencies ?? []);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    void refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [connectionState, refresh]);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      if (!REFRESH_EVENTS.has(msg.event || '')) return;
      if (t) return;
      t = setTimeout(() => { t = null; void refresh(); }, 400);
    });
    return () => {
      unsub();
      if (t) clearTimeout(t);
    };
  }, [connectionState, subscribe, refresh]);

  useEffect(() => {
    const live = timers.current;
    return () => { Object.values(live).forEach(clearTimeout); };
  }, []);

  const retry = useCallback(async (dependency: string, includeInterrupted = false) => {
    setRetrying(dependency);
    try {
      const res = await rpcRef.current('outages.retry', { dependency, includeInterrupted }) as OutageRetryResult;
      setResults((r) => ({ ...r, [dependency]: retryResultText(res) }));
    } catch (e: unknown) {
      setResults((r) => ({ ...r, [dependency]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setRetrying('');
    }
    // The inline answer lasts a few seconds, then goes.
    clearTimeout(timers.current[dependency]);
    timers.current[dependency] = setTimeout(() => {
      setResults((r) => Object.fromEntries(
        Object.entries(r).filter(([name]) => name !== dependency)));
    }, RESULT_MS);
    void refresh();
  }, [refresh]);

  return { dependencies, error, results, retrying, refresh, retry };
}
