/**
 * Generic TTL cache with in-flight request deduplication.
 *
 * Used by rate-limit endpoints (`/api/codex-limits`, `/api/claude-code-limits`)
 * to avoid redundant expensive fetches when multiple clients hit the same
 * endpoint concurrently. Failed fetches use a shorter TTL (30 s) so retries
 * happen sooner.
 * @module
 */

const DEFAULT_TTL_MS = 5 * 60 * 1000; // 5 minutes

interface CacheSlot<T> {
  data: T | null;
  ts: number;
  ttl: number;
  inFlight: Promise<T> | null;
}

/**
 * Create a cached async fetcher.
 *
 * @param fetcher  — the expensive function to cache
 * @param ttlMs   — cache lifetime in ms (default 5 min)
 * @returns a function that returns cached or freshly-fetched data
 */
export function createCachedFetch<T>(
  fetcher: () => Promise<T>,
  ttlMs: number = DEFAULT_TTL_MS,
  opts?: { isValid?: (result: T) => boolean },
): () => Promise<T> {
  const slot: CacheSlot<T> = { data: null, ts: 0, ttl: ttlMs, inFlight: null };
  const FAILURE_TTL_MS = 30_000; // retry failures after 30s, not 5min

  return async () => {
    const now = Date.now();
    if (slot.data !== null && now - slot.ts < slot.ttl) return slot.data;

    if (!slot.inFlight) {
      slot.inFlight = fetcher().then(
        (result) => {
          const valid = opts?.isValid ? opts.isValid(result) : true;
          slot.data = result;
          slot.ts = Date.now();
          slot.ttl = valid ? ttlMs : FAILURE_TTL_MS;
          slot.inFlight = null;
          return result;
        },
        (err) => {
          slot.inFlight = null;
          throw err;
        },
      );
    }

    return slot.inFlight;
  };
}
