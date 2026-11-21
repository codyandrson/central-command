import { describe, it, expect } from 'vitest';
import { FifoGate, FifoGateAborted } from './fifo-gate.js';

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('FifoGate', () => {
  it('serves callers one at a time in arrival order', async () => {
    const gate = new FifoGate(1);
    const entered: number[] = [];
    let inside = 0;
    let peak = 0;
    const job = (n: number) =>
      gate.run(async () => {
        inside += 1;
        peak = Math.max(peak, inside);
        entered.push(n);
        await new Promise((r) => setTimeout(r, 2));
        inside -= 1;
      });
    await Promise.all([0, 1, 2, 3, 4].map(job));
    expect(entered).toEqual([0, 1, 2, 3, 4]);
    expect(peak).toBe(1);
    expect(gate.available).toBe(1);
    expect(gate.waiting).toBe(0);
  });

  it('a newcomer cannot overtake a waiter when the slot is freed', async () => {
    const gate = new FifoGate(1);
    const order: string[] = [];
    const releaseA = await gate.acquire();
    const b = gate.run(async () => void order.push('b'));
    await tick();
    releaseA();
    const c = gate.run(async () => void order.push('c')); // arrives as the slot is handed to b
    await Promise.all([b, c]);
    expect(order).toEqual(['b', 'c']);
  });

  it('a waiter that aborts leaves the line, holds nothing, and the next one proceeds', async () => {
    const gate = new FifoGate(1);
    const order: string[] = [];
    const releaseA = await gate.acquire();
    const gone = new AbortController();
    const b = gate.run(async () => void order.push('b'), gone.signal);
    const c = gate.run(async () => void order.push('c'));
    await tick();
    expect(gate.waiting).toBe(2);
    gone.abort();
    await expect(b).rejects.toBeInstanceOf(FifoGateAborted);
    expect(gate.waiting).toBe(1);
    expect(gate.available).toBe(0); // the aborted waiter freed nothing it did not hold
    releaseA();
    await c;
    expect(order).toEqual(['c']);
    expect(gate.available).toBe(1);
  });

  it('a waiter aborted in the instant the slot is handed to it passes the slot on', async () => {
    const gate = new FifoGate(1);
    const order: string[] = [];
    const releaseA = await gate.acquire();
    const gone = new AbortController();
    const b = gate.run(async () => void order.push('b'), gone.signal);
    const c = gate.run(async () => void order.push('c'));
    await tick();
    releaseA(); // the slot is handed to b ...
    gone.abort(); // ... which is aborted before it runs
    await expect(b).rejects.toBeInstanceOf(FifoGateAborted);
    await c;
    expect(order).toEqual(['c']);
    expect(gate.available).toBe(1);
    expect(gate.waiting).toBe(0);
  });

  it('an already-aborted signal never enters the line', async () => {
    const gate = new FifoGate(1);
    const gone = new AbortController();
    gone.abort();
    await expect(gate.run(async () => 1, gone.signal)).rejects.toBeInstanceOf(FifoGateAborted);
    expect(gate.available).toBe(1);
  });

  it('frees the slot when the body throws, and releasing twice frees it once', async () => {
    const gate = new FifoGate(1);
    await expect(
      gate.run(async () => {
        throw new Error('proxy down');
      }),
    ).rejects.toThrow('proxy down');
    expect(gate.available).toBe(1);
    const release = await gate.acquire();
    release();
    release();
    expect(gate.available).toBe(1);
  });
});
