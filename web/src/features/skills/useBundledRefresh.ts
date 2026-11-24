import { useCallback, useEffect, useRef, useState } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { flagListSchema, type Decision, type ReleaseFlag } from './bundledRefresh';

const POLL_MS = 60_000;
/** Events that can change the answer: a decision is `cc.skill.review_resolved`,
 *  a post `cc.skill.doc_added`, the release comparison `cc.skill.review_computed`. */
const EVENT_PREFIX = 'cc.skill';

export type ResolveResult = { ok: true } | { ok: false; message: string };

/**
 * The open flags on bundled docs (DL-232), for the Skills page's banner.
 *
 * Over the gateway RPC like `useSkills`. A failed read keeps the last answer —
 * a banner that vanished because one poll failed would be a silent failure.
 */
export function useBundledRefresh() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [flags, setFlags] = useState<ReleaseFlag[]>([]);
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const load = useCallback(async () => {
    try {
      const res = flagListSchema.safeParse(await rpcRef.current('skills.release_flags'));
      if (res.success) setFlags(res.data.items);
    } catch {
      // keep the last answer
    }
  }, []);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    void load();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [connectionState, load]);

  useEffect(() => {
    if (connectionState !== 'connected' || typeof subscribe !== 'function') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      if (!(msg.event || '').startsWith(EVENT_PREFIX) || t) return;
      t = setTimeout(() => { t = null; void load(); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, load, subscribe]);

  /** One decision on one flag, over the text the operator was shown. */
  const resolve = useCallback(async (flag: ReleaseFlag, decision: Decision, content?: string): Promise<ResolveResult> => {
    try {
      await rpcRef.current('skills.release_flag_resolve', {
        skill_id: flag.skill_id, doc_key: flag.doc_key, decision,
        ours_hash: flag.ours_hash, ...(decision === 'edit' ? { content } : {}),
      });
    } catch (e: unknown) {
      await load();
      return { ok: false, message: e instanceof Error ? e.message : String(e) };
    }
    await load();
    return { ok: true };
  }, [load]);

  return { flags, resolve, reload: load };
}
