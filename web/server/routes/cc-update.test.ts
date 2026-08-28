/** Tests for the one-click-apply trigger/status routes (cc-update). */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';
import { mkdtempSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

let dir: string;

async function buildApp() {
  vi.doMock('../middleware/rate-limit.js', () => ({
    rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
  }));
  const mod = await import('./cc-update.js');
  const app = new Hono();
  app.route('/', mod.default);
  return app;
}

describe('cc-update routes', () => {
  beforeEach(() => {
    vi.resetModules();
    dir = mkdtempSync(join(tmpdir(), 'cc-update-'));
    process.env.CC_UPDATE_TRIGGER = join(dir, 'trigger');
    process.env.CC_UPDATE_STATUS = join(dir, 'status.json');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.CC_UPDATE_TRIGGER;
    delete process.env.CC_UPDATE_STATUS;
  });

  it('apply writes the trigger file and reports 202', async () => {
    const app = await buildApp();
    const res = await app.request('/api/update/apply', {
      method: 'POST',
      body: JSON.stringify({ target: '1.2.3' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(202);
    expect(existsSync(join(dir, 'trigger'))).toBe(true);
    const trigger = JSON.parse(readFileSync(join(dir, 'trigger'), 'utf-8'));
    expect(trigger.target).toBe('1.2.3');
  });

  it('apply refuses while a trigger is pending (server-side double-click guard)', async () => {
    const app = await buildApp();
    writeFileSync(join(dir, 'trigger'), '{}');
    const res = await app.request('/api/update/apply', { method: 'POST' });
    expect(res.status).toBe(409);
  });

  it('apply refuses while a fresh run is in flight, but not a stale one', async () => {
    const app = await buildApp();
    writeFileSync(join(dir, 'status.json'), JSON.stringify({
      state: 'running', phase: 'rebuild', updated_at: new Date().toISOString(),
    }));
    expect((await app.request('/api/update/apply', { method: 'POST' })).status).toBe(409);

    // A running record older than the unit's own timeout is a crashed updater
    // — it must NOT brick the button forever.
    writeFileSync(join(dir, 'status.json'), JSON.stringify({
      state: 'running', phase: 'rebuild',
      updated_at: new Date(Date.now() - 3000 * 1000).toISOString(),
    }));
    expect((await app.request('/api/update/apply', { method: 'POST' })).status).toBe(202);
  });

  it('status reports the durable record and the pending flag', async () => {
    const app = await buildApp();
    writeFileSync(join(dir, 'status.json'), JSON.stringify({
      state: 'rolled_back', phase: 'rollback', error: 'health check failed',
    }));
    writeFileSync(join(dir, 'trigger'), '{}');
    const res = await app.request('/api/update/status');
    const json = await res.json() as { pending: boolean; inFlight: boolean; status: { state: string } };
    expect(json.pending).toBe(true);
    expect(json.inFlight).toBe(false);
    expect(json.status.state).toBe('rolled_back');
  });
});
