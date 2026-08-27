/** Tests for the async mutex (serialisation, error recovery, independence). */
import { describe, it, expect } from 'vitest';
import { createMutex } from './mutex.js';

describe('createMutex', () => {
  it('should serialize concurrent operations', async () => {
    const withLock = createMutex();
    const order: number[] = [];

    // Launch 3 concurrent tasks — they should execute in order
    const p1 = withLock(async () => {
      await delay(30);
      order.push(1);
      return 'first';
    });

    const p2 = withLock(async () => {
      await delay(10);
      order.push(2);
      return 'second';
    });

    const p3 = withLock(async () => {
      order.push(3);
      return 'third';
    });

    const results = await Promise.all([p1, p2, p3]);

    expect(results).toEqual(['first', 'second', 'third']);
    expect(order).toEqual([1, 2, 3]);
  });

  it('should return the value from the locked function', async () => {
    const withLock = createMutex();
    const result = await withLock(async () => 42);
    expect(result).toBe(42);
  });

  it('should propagate errors without breaking the lock', async () => {
    const withLock = createMutex();

    // First call throws
    await expect(
      withLock(async () => {
        throw new Error('boom');
      }),
    ).rejects.toThrow('boom');

    // Second call should still work (lock released after error)
    const result = await withLock(async () => 'recovered');
    expect(result).toBe('recovered');
  });

  it('should handle rapid sequential calls', async () => {
    const withLock = createMutex();
    let counter = 0;

    // Simulate read-modify-write race condition that the mutex prevents
    const promises = Array.from({ length: 10 }, () =>
      withLock(async () => {
        const current = counter;
        await delay(1);
        counter = current + 1;
      }),
    );

    await Promise.all(promises);
    expect(counter).toBe(10); // Without mutex this would likely be < 10
  });

  it('should create independent mutexes', async () => {
    const lock1 = createMutex();
    const lock2 = createMutex();
    const order: string[] = [];

    // lock1 holds for 50ms, lock2 should not wait for it
    const p1 = lock1(async () => {
      await delay(50);
      order.push('lock1');
    });

    const p2 = lock2(async () => {
      order.push('lock2');
    });

    await Promise.all([p1, p2]);

    // lock2 should finish before lock1 since they're independent
    expect(order[0]).toBe('lock2');
    expect(order[1]).toBe('lock1');
  });
});

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
