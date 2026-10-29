import { useState, useEffect, useCallback, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import type {
  AuthorOverrideResult, CapabilityRow, GateOverride, OverrideScope,
} from '../agents/types';

/** The state badge both override views show: "in shadow k of n", live, suspended or revoked. */
export function overrideStateLabel(o: GateOverride) {
  return o.state === 'shadow' ? `in shadow ${o.shadow.k} of ${o.shadow.n}` : o.state;
}

/**
 * An agent's gate overrides (or, with `capability`, every override on one
 * capability — story 11's view). The operator's direct actions: `author` and
 * `revoke` are RPCs that return the refreshed list, which replaces the state.
 * Errors reach the caller as the RPC error message ("; "-joined problems).
 */
export function useOverrides(scope: { agentId?: string; capability?: string }) {
  const { rpc, subscribe } = useGateway();
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);
  const [overrides, setOverrides] = useState<GateOverride[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { agentId, capability } = scope;

  const refresh = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!agentId && !capability) { setOverrides([]); return; }
    if (!silent) setLoading(true);
    try {
      const res = await rpcRef.current(
        'overrides.list', capability ? { capability } : { agentId },
      ) as { overrides: GateOverride[] };
      setOverrides(res.overrides ?? []);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [agentId, capability]);

  useEffect(() => { refresh(); }, [refresh]);

  // Live-follow, debounced: a shadow run, a go-live or a revoke emits an
  // override event, and one silent refresh covers a burst (shadow progress and
  // live/suspended state change without a reload).
  useEffect(() => {
    if (!agentId && !capability) return;
    if (typeof subscribe !== 'function') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      if (!(msg.event || '').startsWith('cc.override')) return;
      if (t) return;  // one refresh per burst, not one per event
      t = setTimeout(() => { t = null; refresh({ silent: true }); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [agentId, capability, refresh, subscribe]);

  const author = useCallback(async (
    forAgent: string, cap: string, overrideScope: OverrideScope, acknowledgeIrreversible: boolean,
  ) => {
    const res = await rpcRef.current('overrides.author', {
      agentId: forAgent, capability: cap, scope: overrideScope, acknowledgeIrreversible,
    }) as AuthorOverrideResult;
    await refresh();
    return res;
  }, [refresh]);

  const revoke = useCallback(async (forAgent: string, overrideId: string, reason = '') => {
    await rpcRef.current('overrides.revoke', { agentId: forAgent, overrideId, reason });
    await refresh();
  }, [refresh]);

  return { overrides, loading, error, refresh, author, revoke };
}

/** The governed catalog, for the author dialog's capability picker. */
export function useCapabilityCatalog(enabled: boolean) {
  const { rpc } = useGateway();
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);
  const [capabilities, setCapabilities] = useState<CapabilityRow[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    (async () => {
      try {
        const res = await rpcRef.current('capabilities.list') as { capabilities: CapabilityRow[] };
        if (live) { setCapabilities(res.capabilities ?? []); setError(''); }
      } catch (e: unknown) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { live = false; };
  }, [enabled]);
  return { capabilities, error };
}
