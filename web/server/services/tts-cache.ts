/**
 * LRU TTS cache with TTL expiry.
 *
 * In-memory cache for synthesised audio buffers keyed by content hash.
 * Entries are evicted by TTL, max count, or a 100 MB memory budget —
 * whichever limit is hit first. Access promotes entries (LRU).
 * @module
 */

import type { TtsCacheEntry } from '../types.js';
import { config } from '../lib/config.js';

const cache = new Map<string, TtsCacheEntry>();
let totalBytes = 0;
const MAX_CACHE_BYTES = 100 * 1024 * 1024; // 100 MB memory budget

/**
 * Retrieve a cached TTS buffer by hash. Returns null on miss or expiry.
 * Promotes the entry (LRU) on hit.
 */
export function getTtsCache(hash: string): Buffer | null {
  const entry = cache.get(hash);
  if (!entry) return null;

  if (Date.now() - entry.createdAt > config.ttsCacheTtlMs) {
    totalBytes -= entry.buf.length;
    cache.delete(hash);
    return null;
  }

  // LRU promotion: delete and re-insert
  cache.delete(hash);
  entry.lastAccess = Date.now();
  cache.set(hash, entry);
  return entry.buf;
}

/**
 * Store a TTS buffer in the cache and prune if necessary.
 */
export function setTtsCache(hash: string, buf: Buffer): void {
  const now = Date.now();
  const existing = cache.get(hash);
  if (existing) {
    totalBytes -= existing.buf.length;
  }
  cache.set(hash, { buf, createdAt: now, lastAccess: now });
  totalBytes += buf.length;

  // Prune expired entries
  for (const [key, e] of cache) {
    if (now - e.createdAt > config.ttsCacheTtlMs) {
      totalBytes -= e.buf.length;
      cache.delete(key);
    }
  }

  // Enforce max count
  while (cache.size > config.ttsCacheMax) {
    const oldest = cache.keys().next().value;
    if (!oldest) break;
    const entry = cache.get(oldest);
    if (entry) totalBytes -= entry.buf.length;
    cache.delete(oldest);
  }

  // Enforce memory budget
  while (totalBytes > MAX_CACHE_BYTES && cache.size > 0) {
    const oldest = cache.keys().next().value;
    if (!oldest) break;
    const entry = cache.get(oldest);
    if (entry) totalBytes -= entry.buf.length;
    cache.delete(oldest);
  }
}
