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
    process.env.CC_UPDATE_STAGE_TRIGGER = join(dir, 'stage-trigger');
    process.env.CC_UPDATE_STAGE_STATUS = join(dir, 'stage.json');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.CC_UPDATE_TRIGGER;
    delete process.env.CC_UPDATE_STATUS;
    delete process.env.CC_UPDATE_STAGE_TRIGGER;
    delete process.env.CC_UPDATE_STAGE_STATUS;
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

    // A running record older than the unit's own timeout (5400s) is a crashed
    // updater — it must NOT brick the button forever.
    writeFileSync(join(dir, 'status.json'), JSON.stringify({
      state: 'running', phase: 'rebuild',
      updated_at: new Date(Date.now() - 6000 * 1000).toISOString(),
    }));
    expect((await app.request('/api/update/apply', { method: 'POST' })).status).toBe(202);
  });

  it('apply refuses a target that is already installed (the stale-button trap)', async () => {
    const app = await buildApp();
    // Ask the same reader the route uses for the installed version.
    const { readProductVersion } = await import('../lib/release-source.js');
    const current = readProductVersion();
    const res = await app.request('/api/update/apply', {
      method: 'POST',
      body: JSON.stringify({ target: current }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(409);
    expect(existsSync(join(dir, 'trigger'))).toBe(false);
  });

  it('stage writes the stage trigger and reports 202', async () => {
    const app = await buildApp();
    const res = await app.request('/api/update/stage', {
      method: 'POST',
      body: JSON.stringify({ target: '9.9.9' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(202);
    const trigger = JSON.parse(readFileSync(join(dir, 'stage-trigger'), 'utf-8'));
    expect(trigger.target).toBe('9.9.9');
  });

  it('stage no-ops when the target is already staged or a request is pending', async () => {
    const app = await buildApp();
    writeFileSync(join(dir, 'stage.json'), JSON.stringify({
      state: 'success', phase: 'staged', target: '9.9.9',
    }));
    let res = await app.request('/api/update/stage', {
      method: 'POST',
      body: JSON.stringify({ target: '9.9.9' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(200);
    expect(existsSync(join(dir, 'stage-trigger'))).toBe(false);

    // A NEWER target than the staged one must re-stage.
    res = await app.request('/api/update/stage', {
      method: 'POST',
      body: JSON.stringify({ target: '9.9.10' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(202);
    expect(existsSync(join(dir, 'stage-trigger'))).toBe(true);

    // And with that trigger now pending, another request no-ops.
    res = await app.request('/api/update/stage', {
      method: 'POST',
      body: JSON.stringify({ target: '9.9.10' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(200);
  });

  it('status reports the stage record wire shape the dialog gates on', async () => {
    const app = await buildApp();
    writeFileSync(join(dir, 'stage.json'), JSON.stringify({
      state: 'success', phase: 'staged', target: '9.9.9',
    }));
    const res = await app.request('/api/update/status');
    const json = await res.json() as {
      stage: { pending: boolean; inFlight: boolean; status: { state: string; target: string } };
    };
    expect(json.stage.pending).toBe(false);
    expect(json.stage.inFlight).toBe(false);
    expect(json.stage.status.state).toBe('success');
    expect(json.stage.status.target).toBe('9.9.9');
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
