import { useCallback, useEffect, useRef, useState } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { isLatestReview, notRefreshed, type LatestReview } from './bundledRefresh';

const POLL_MS = 60_000;
const NONE = /no bundled-skill review/;
/** Events that can change the answer: an apply posts docs (`skill.doc_added`,
 *  forwarded as `cc.skill.doc_added`), a verdict is `cc.skill.review_resolved`. */
const EVENT_PREFIX = 'cc.skill';

/**
 * The most recent bundled-skill review, for the Skills page's retry banner.
 *
 * Over the gateway RPC like `useSkills` (this page always has the socket; the
 * Update dialog, which has not, uses `useSkillReview` over the HTTP proxy).
 * "No review stored" is the normal state, not an error. A failed read keeps
 * the last answer — a banner that vanished because one poll failed would be a
 * silent failure — and the Retry's own failure is shown beside the Retry.
 */
export function useBundledRefresh() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [review, setReview] = useState<LatestReview | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState('');
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const load = useCallback(async () => {
    try {
      const res = await rpcRef.current('skills.update_review_latest');
      setReview(isLatestReview(res) ? res : null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (NONE.test(msg)) setReview(null);
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

  /** POST .../apply as the updater: only rows with no `applied_at` run. */
  const retry = useCallback(async () => {
    if (!review) return;
    setRetrying(true);
    setRetryError('');
    try {
      await rpcRef.current('skills.update_review_apply', { target: review.target, actor: 'updater' });
    } catch (e: unknown) {
      setRetryError(e instanceof Error ? e.message : String(e));
    } finally {
      setRetrying(false);
    }
    await load();
  }, [review, load]);

  return { review, pending: notRefreshed(review), retrying, retryError, retry, reload: load };
}
