/**
 * A FIFO gate in front of a speech alias — one job at a time, in arrival order.
 *
 * The `cc-tts` / `cc-stt` LiteLLM aliases carry `max_parallel_requests: 1`
 * (DL-220). LiteLLM 1.98.0 made the second request WAIT at that limit; 1.110.2
 * REJECTS it (HTTP 429 "Deployment has all max_parallel_requests slots in
 * use", measured 2026-11-21), so the wait lives here, in the process that
 * calls the proxy. Same rules as `central_command/integrations/speech_gate.py`
 * (the API process's twin, which fronts the same two aliases):
 *
 *  - waiters are served in arrival order and the slot is handed straight to
 *    the next one, so a newcomer cannot overtake the line;
 *  - there is no timeout: a waiter waits as long as the jobs ahead take;
 *  - a waiter whose caller aborts while it waits leaves the line and holds
 *    nothing (a slot that was handed to it in that instant is passed on);
 *  - nothing here retries: a 429 that still comes back is the caller's error.
 *
 * One gate per alias per PROCESS. The cockpit server and the API process are
 * separate lines to the same proxy.
 */

export class FifoGateAborted extends Error {
  constructor() {
    super('client disconnected before its turn');
    this.name = 'FifoGateAborted';
  }
}

interface Waiter {
  grant: () => void;
  abort: () => void;
}

export class FifoGate {
  private free: number;
  private readonly waiters: Waiter[] = [];

  constructor(size = 1) {
    this.free = size;
  }

  /** Callers still in line (not holding a slot). */
  get waiting(): number {
    return this.waiters.length;
  }

  /** Slots nobody holds. */
  get available(): number {
    return this.free;
  }

  private release(): void {
    // Hand the slot to the oldest waiter without freeing it (no gap for a
    // newcomer); free it only when nobody is waiting.
    const next = this.waiters.shift();
    if (next) next.grant();
    else this.free += 1;
  }

  /** Resolve with a release function once it is this caller's turn. */
  acquire(signal?: AbortSignal): Promise<() => void> {
    if (signal?.aborted) return Promise.reject(new FifoGateAborted());
    const makeRelease = (): (() => void) => {
      let released = false;
      return () => {
        if (released) return;
        released = true;
        this.release();
      };
    };
    if (this.free > 0 && this.waiters.length === 0) {
      this.free -= 1;
      return Promise.resolve(makeRelease());
    }
    return new Promise<() => void>((resolve, reject) => {
      const waiter: Waiter = {
        grant: () => {
          signal?.removeEventListener('abort', onAbort);
          if (signal?.aborted) {
            // Aborted in the instant the slot arrived: it is held, so give it back.
            this.release();
            reject(new FifoGateAborted());
            return;
          }
          resolve(makeRelease());
        },
        abort: () => reject(new FifoGateAborted()),
      };
      const onAbort = () => {
        const i = this.waiters.indexOf(waiter);
        if (i >= 0) {
          this.waiters.splice(i, 1);
          waiter.abort();
        }
      };
      this.waiters.push(waiter);
      signal?.addEventListener('abort', onAbort, { once: true });
    });
  }

  /** Run `fn` holding the slot; the slot is freed however `fn` ends. */
  async run<T>(fn: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const release = await this.acquire(signal);
    if (signal?.aborted) {
      release();
      throw new FifoGateAborted();
    }
    try {
      return await fn();
    } finally {
      release();
    }
  }
}

/** One line per alias, each sized to the alias's max_parallel_requests (1). */
export const ttsGate = new FifoGate(1);
export const sttGate = new FifoGate(1);
