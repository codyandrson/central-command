/**
 * Simple async mutex for serializing file read-modify-write operations.
 * @module
 */

/**
 * Create an independent mutex instance.
 *
 * Returns a `withLock` function: call it with an async callback to guarantee
 * that only one callback executes at a time for this mutex.
 */
export function createMutex() {
  let lock: Promise<void> = Promise.resolve();

  return async function withLock<T>(fn: () => Promise<T>): Promise<T> {
    let release: () => void;
    const next = new Promise<void>((resolve) => { release = resolve; });
    const prev = lock;
    lock = next;

    await prev;
    try {
      return await fn();
    } finally {
      release!();
    }
  };
}

/**
 * Keyed mutex — one lock per key string.
 * Usage: await withMutex('memory-file', () => appendToMemoryFile(...));
 */
const mutexes = new Map<string, ReturnType<typeof createMutex>>();

export async function withMutex<T>(key: string, fn: () => Promise<T>): Promise<T> {
  let mutex = mutexes.get(key);
  if (!mutex) {
    mutex = createMutex();
    mutexes.set(key, mutex);
  }
  return mutex(fn);
}
