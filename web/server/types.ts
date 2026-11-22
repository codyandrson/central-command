/**
 * Shared types for the Nerve server.
 *
 * Centralised type definitions used across routes, services, and lib modules.
 * @module
 */

/** A single entry in the in-memory TTS audio cache. */
export interface TtsCacheEntry {
  /** Raw audio buffer (typically MP3). */
  buf: Buffer;
  /** Epoch ms when the entry was first cached. */
  createdAt: number;
  /** Epoch ms of the most recent cache hit (used for LRU eviction). */
  lastAccess: number;
}

