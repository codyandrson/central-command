/** Tests for the LRU TTS cache (TTL expiry, eviction, memory budget). */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock config before importing the module
vi.mock('../lib/config.js', () => ({
  config: {
    ttsCacheTtlMs: 5000, // 5 seconds for testing
    ttsCacheMax: 3,
  },
}));

// We need to reset module state between tests since tts-cache uses module-level state
let getTtsCache: typeof import('./tts-cache.js').getTtsCache;
let setTtsCache: typeof import('./tts-cache.js').setTtsCache;

describe('tts-cache', () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    // Re-import module to reset state (cache Map and totalBytes)
    vi.resetModules();
    const mod = await import('./tts-cache.js');
    getTtsCache = mod.getTtsCache;
    setTtsCache = mod.setTtsCache;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should store and retrieve a cached buffer', () => {
    const buf = Buffer.from('audio-data');
    setTtsCache('hash1', buf);

    const result = getTtsCache('hash1');
    expect(result).not.toBeNull();
    expect(result!.toString()).toBe('audio-data');
  });

  it('should return null for cache miss', () => {
    expect(getTtsCache('nonexistent')).toBeNull();
  });

  it('should expire entries after TTL', () => {
    const buf = Buffer.from('audio-data');
    setTtsCache('hash1', buf);

    // Still valid
    vi.advanceTimersByTime(4999);
    expect(getTtsCache('hash1')).not.toBeNull();

    // Expired
    vi.advanceTimersByTime(2);
    expect(getTtsCache('hash1')).toBeNull();
  });

  it('should enforce max entry count (LRU eviction)', () => {
    // Config max is 3
    setTtsCache('a', Buffer.from('1'));
    setTtsCache('b', Buffer.from('2'));
    setTtsCache('c', Buffer.from('3'));

    // All three should exist
    expect(getTtsCache('a')).not.toBeNull();
    expect(getTtsCache('b')).not.toBeNull();
    expect(getTtsCache('c')).not.toBeNull();

    // Adding a 4th should evict the oldest (a)
    setTtsCache('d', Buffer.from('4'));
    expect(getTtsCache('a')).toBeNull();
    expect(getTtsCache('d')).not.toBeNull();
  });

  it('should promote entries on access (LRU)', () => {
    setTtsCache('a', Buffer.from('1'));
    setTtsCache('b', Buffer.from('2'));
    setTtsCache('c', Buffer.from('3'));

    // Access 'a' to promote it (moves to end of Map iteration order)
    getTtsCache('a');

    // Add a 4th — should evict 'b' (now the oldest unreferenced) instead of 'a'
    setTtsCache('d', Buffer.from('4'));
    expect(getTtsCache('a')).not.toBeNull();
    expect(getTtsCache('b')).toBeNull();
  });

  it('should enforce memory budget (100 MB)', () => {
    // Create buffers that total over 100MB
    const big = Buffer.alloc(40 * 1024 * 1024); // 40 MB each

    setTtsCache('a', big);
    setTtsCache('b', big);
    // a + b = 80MB, still under 100MB
    expect(getTtsCache('a')).not.toBeNull();
    expect(getTtsCache('b')).not.toBeNull();

    // Adding a third 40MB buffer → 120MB total, should evict oldest to get under budget
    setTtsCache('c', big);
    expect(getTtsCache('a')).toBeNull(); // evicted
    expect(getTtsCache('c')).not.toBeNull();
  });

  it('should prune expired entries on set', () => {
    setTtsCache('old', Buffer.from('old-data'));

    // Advance past TTL
    vi.advanceTimersByTime(6000);

    // Setting a new entry should prune the expired one
    setTtsCache('new', Buffer.from('new-data'));
    expect(getTtsCache('old')).toBeNull();
    expect(getTtsCache('new')).not.toBeNull();
  });
});
