import { useCallback, useEffect, useRef, useState } from 'react';
import { isReview, itemKey, type Review, type ReviewCounts, type ReviewItem, type Verdict } from './skillReview';

const POLL_MS = 3000;
const BASE = '/api/skills/update-review';

// Plain `fetch` over the HTTP proxy, not the gateway RPC: the Update dialog also
// opens from Settings › Updates, where no gateway socket is mounted. The Skills
// page's banner (`useBundledRefresh`) always has the socket and reads over RPC.

/** What the update dialog needs of the review of one staged release. */
export interface SkillReviewState {
  /** The stored review, once one has been read. */
  review: Review | null;
  /** A compute or a first read is under way: Apply stays disabled. */
  loading: boolean;
  /** Why the review could not be computed or read; Apply stays disabled. */
  error: string | null;
  /** Read the stored review now (after the apply route reports a change). */
  reload: () => Promise<void>;
  /** Compute the review again (the Retry button, and after a 409). */
  recompute: () => Promise<void>;
  /** Post one verdict. `ok: false` carries what to show on the card. */
  resolve: (item: ReviewItem, verdict: Verdict, content?: string) => Promise<ResolveResult>;
}

export type ResolveResult = { ok: true } | { ok: false; status: number; message: string };

async function detail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null) as { detail?: unknown; error?: unknown } | null;
  const d = body?.detail ?? body?.error;
  return typeof d === 'string' && d ? d : `HTTP ${res.status}`;
}

/**
 * The bundled-skill review of the staged release, for the Update dialog.
 *
 * While `enabled` it reads `GET /api/skills/update-review/{target}` straight
 * away and then on the dialog's 3 s cadence. A 404 (no review: the stage ran
 * under an updater that predates this, or its review failed) and a 409 (the
 * library or the release moved since the review was computed) both mean
 * "compute it": the hook POSTs `{target, ref}` once per such episode and keeps
 * the dialog's Apply disabled until a read succeeds. A POST that fails is
 * shown and is NOT retried on the tick; only `recompute` (the Retry button)
 * posts again, so a broken review cannot become a request loop.
 *
 * It goes over the HTTP proxy (`web/server/routes/cc-skills.ts`) rather than
 * the gateway RPC because the dialog also opens where no gateway socket is
 * mounted (Settings › Updates), as the status and hold polls already do.
 */
export function useSkillReview(opts: {
  enabled: boolean;
  target: string;
  /** `v<target>` on the k3s profile, `upstream` on single-node. */
  ref: string;
}): SkillReviewState {
  const { enabled, target, ref } = opts;
  const [review, setReview] = useState<Review | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const posted = useRef(false);        // a compute was tried since the last good read
  const busy = useRef(false);          // a read or compute is in flight
  const refRef = useRef(ref);
  refRef.current = ref;

  useEffect(() => {
    setReview(null); setError(null); setLoading(false); posted.current = false; busy.current = false;
  }, [target]);

  const compute = useCallback(async (): Promise<boolean> => {
    posted.current = true;
    setLoading(true);
    try {
      const res = await fetch(BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, ref: refRef.current }),
      });
      if (!res.ok) { setError(await detail(res)); return false; }
      const body: unknown = await res.json().catch(() => null);
      if (!isReview(body)) { setError('The review answered in an unexpected shape.'); return false; }
      setReview(body); setError(null); posted.current = false;
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setLoading(false);
    }
  }, [target]);

  const read = useCallback(async () => {
    if (busy.current || !target) return;
    busy.current = true;
    try {
      const res = await fetch(`${BASE}/${encodeURIComponent(target)}`);
      if (res.status === 404 || res.status === 409) {
        if (!posted.current) { setLoading(true); await compute(); }
        return;
      }
      if (!res.ok) { setError(await detail(res)); return; }
      const body: unknown = await res.json().catch(() => null);
      if (!isReview(body)) { setError('The review answered in an unexpected shape.'); return; }
      setReview(body); setError(null); posted.current = false;
    } catch {
      // The API is restarting under us, as the status poll expects: keep polling.
    } finally {
      busy.current = false;
    }
  }, [target, compute]);

  useEffect(() => {
    if (!enabled || !target) return;
    void read();
    const iv = setInterval(() => { void read(); }, POLL_MS);
    return () => clearInterval(iv);
  }, [enabled, target, read]);

  const recompute = useCallback(async () => {
    setError(null);
    await compute();
  }, [compute]);

  const resolve = useCallback(async (item: ReviewItem, verdict: Verdict, content?: string): Promise<ResolveResult> => {
    const path = `${BASE}/${encodeURIComponent(target)}/items/${encodeURIComponent(item.skill_id)}/${encodeURIComponent(item.doc_key)}/resolve`;
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The hashes of the item as it was shown, nulls included: the server
        // refuses the verdict (409) when the stored row has moved.
        body: JSON.stringify({
          verdict, content, ours_hash: item.ours_hash, theirs_hash: item.theirs_hash,
        }),
      });
      if (!res.ok) {
        const message = await detail(res);
        if (res.status === 409) {
          // The doc changed underneath: compute again and show the new review.
          await recompute();
        }
        return { ok: false, status: res.status, message };
      }
      // The answer carries the new counts: apply it now, so Apply follows the
      // click at once rather than at the next tick, then read the stored review.
      const done = await res.json().catch(() => null) as
        { verdict?: Verdict; resolved_at?: string; counts?: ReviewCounts } | null;
      if (done?.counts && done.verdict) {
        const key = itemKey(item);
        setReview((prev) => prev && {
          ...prev,
          counts: done.counts as ReviewCounts,
          items: prev.items.map((it) => itemKey(it) !== key ? it : {
            ...it, verdict: done.verdict as Verdict, resolved_at: done.resolved_at ?? it.resolved_at,
            resolved_content: done.verdict === 'edit' ? content ?? null : null,
          }),
        });
      }
      void read();
      return { ok: true };
    } catch (e) {
      return { ok: false, status: 0, message: e instanceof Error ? e.message : String(e) };
    }
  }, [target, recompute, read]);

  return { review, loading: loading || (enabled && review === null && error === null), error, reload: read, recompute, resolve };
}

