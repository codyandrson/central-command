/** Tests for the one-click-apply trigger/status routes (cc-update). */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';
import { mkdtempSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

let dir: string;

async function buildApp(config: Record<string, unknown> = {}) {
  vi.doMock('../lib/config.js', () => ({ config: { gatewayUrl: 'http://cc.test', ...config } }));
  vi.doMock('../middleware/rate-limit.js', () => ({
    rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
  }));
  const mod = await import('./cc-update.js');
  const app = new Hono();
  app.route('/', mod.default);
  return app;
}

// The API behind the route: the hold accepting.
function gateway() {
  return vi.fn(async () => new Response(JSON.stringify({ active: true }), { status: 200 }));
}

const apply = (app: Hono, body: object) => app.request('/api/update/apply', {
  method: 'POST',
  body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json' },
});

describe('cc-update routes', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', gateway());
    vi.resetModules();
    dir = mkdtempSync(join(tmpdir(), 'cc-update-'));
    process.env.CC_UPDATE_TRIGGER = join(dir, 'trigger');
    process.env.CC_UPDATE_STATUS = join(dir, 'status.json');
    process.env.CC_UPDATE_STAGE_TRIGGER = join(dir, 'stage-trigger');
    process.env.CC_UPDATE_STAGE_STATUS = join(dir, 'stage.json');
    process.env.CC_UPDATE_RESTART_STATUS = join(dir, 'restart.json');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.CC_UPDATE_TRIGGER;
    delete process.env.CC_UPDATE_STATUS;
    delete process.env.CC_UPDATE_STAGE_TRIGGER;
    delete process.env.CC_UPDATE_STAGE_STATUS;
    delete process.env.CC_UPDATE_RESTART_STATUS;
  });

  it('apply engages the API hold instead of writing the trigger (the API triggers when the agents land)', async () => {
    const fetchMock = gateway();
    vi.stubGlobal('fetch', fetchMock);
    const app = await buildApp();
    const res = await app.request('/api/update/apply', {
      method: 'POST',
      body: JSON.stringify({ target: '1.2.3' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ triggered: false, held: true });
    expect(existsSync(join(dir, 'trigger'))).toBe(false);
    // The hold is the only call: no bundled-skill review gates an apply (DL-232).
    expect(fetchMock.mock.calls.map((call) => String((call as unknown[])[0]))).toEqual([
      'http://cc.test/api/update/hold',
    ]);
    vi.unstubAllGlobals();
  });

  it('apply with force writes the trigger file itself and reports 202 (the "update anyway" path)', async () => {
    const app = await buildApp();
    const res = await app.request('/api/update/apply', {
      method: 'POST',
      body: JSON.stringify({ target: '1.2.3', force: true }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res.status).toBe(202);
    const trigger = JSON.parse(readFileSync(join(dir, 'trigger'), 'utf-8'));
    expect(trigger.target).toBe('1.2.3');
    expect(trigger.force).toBe(true);
  });

  it('the hold routes proxy to the API: GET reads, /now forces, /cancel releases', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ active: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const app = await buildApp();
    expect((await app.request('/api/update/hold')).status).toBe(200);
    expect((await app.request('/api/update/hold/now', { method: 'POST' })).status).toBe(200);
    expect((await app.request('/api/update/hold/cancel', { method: 'POST' })).status).toBe(200);
    const calls = fetchMock.mock.calls.map((c) => [String(c[0]), (c[1] as RequestInit).method]);
    expect(calls).toEqual([
      ['http://cc.test/api/update/hold', 'GET'],
      ['http://cc.test/api/update/hold/now', 'POST'],
      ['http://cc.test/api/update/hold', 'DELETE'],
    ]);
    vi.unstubAllGlobals();
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

  describe('restart of the API process', () => {
    const post = (app: Hono, path: string) => app.request(path, { method: 'POST' });

    it('status is restart.json as written, `none` when absent, plus what the card gates on', async () => {
      const app = await buildApp();
      let json = await (await app.request('/api/restart/status')).json() as Record<string, unknown>;
      expect(json).toEqual({
        state: 'none', available: true, inFlight: false, pending: false, updateBusy: false,
      });

      writeFileSync(join(dir, 'restart.json'), JSON.stringify({
        state: 'restarted', message: 'cc-uvicorn restarted and healthy',
        requested_at: '2026-11-16T10:00:00+00:00', finished_at: '2026-11-16T10:00:40+00:00', forced: false,
      }));
      json = await (await app.request('/api/restart/status')).json() as Record<string, unknown>;
      expect(json).toMatchObject({
        state: 'restarted', finished_at: '2026-11-16T10:00:40+00:00', available: true, inFlight: false,
      });
    });

    it('is unavailable, with a reason, on the single-node profile and where the updater is not installed', async () => {
      let json = await (await (await buildApp({ updateBackend: 'api' })).request('/api/restart/status')).json() as {
        available: boolean; reason: string;
      };
      expect(json.available).toBe(false);
      expect(json.reason).toMatch(/single-node/);

      vi.resetModules();
      process.env.CC_UPDATE_TRIGGER = join(dir, 'no-such-dir', 'trigger');
      const app = await buildApp();
      json = await (await app.request('/api/restart/status')).json() as typeof json;
      expect(json.available).toBe(false);
      expect(json.reason).toMatch(/not installed/);
      expect((await post(app, '/api/restart/hold')).status).toBe(501);
    });

    it('engages the API hold with kind restart and writes no trigger itself', async () => {
      const fetchMock = vi.fn(async () => new Response(JSON.stringify({ active: true, kind: 'restart' }), { status: 200 }));
      vi.stubGlobal('fetch', fetchMock);
      const app = await buildApp();
      const res = await post(app, '/api/restart/hold');
      expect(res.status).toBe(202);
      expect(await res.json()).toEqual({ held: true });
      const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
      expect(url).toBe('http://cc.test/api/update/hold');
      expect(init.method).toBe('POST');
      expect(JSON.parse(String(init.body))).toEqual({ kind: 'restart', target: '' });
      expect(existsSync(join(dir, 'trigger'))).toBe(false);
    });

    it('refuses while an update is running or pending, or a restart already is, and never reaches the API', async () => {
      const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
      vi.stubGlobal('fetch', fetchMock);
      const app = await buildApp();

      writeFileSync(join(dir, 'status.json'), JSON.stringify({
        state: 'running', phase: 'rebuild', updated_at: new Date().toISOString(),
      }));
      expect((await post(app, '/api/restart/hold')).status).toBe(409);
      expect((await (await app.request('/api/restart/status')).json() as { updateBusy: boolean }).updateBusy).toBe(true);
      rmSync(join(dir, 'status.json'));

      writeFileSync(join(dir, 'trigger'), JSON.stringify({ target: '1.2.3', force: false }));
      expect((await post(app, '/api/restart/hold')).status).toBe(409);
      writeFileSync(join(dir, 'trigger'), JSON.stringify({ target: '', force: false, action: 'restart' }));
      const pending = await (await app.request('/api/restart/status')).json() as { pending: boolean; updateBusy: boolean };
      expect(pending).toMatchObject({ pending: true, updateBusy: false });
      expect((await post(app, '/api/restart/hold')).status).toBe(409);
      rmSync(join(dir, 'trigger'));

      writeFileSync(join(dir, 'restart.json'), JSON.stringify({
        state: 'running', message: 'x', updated_at: new Date().toISOString(),
      }));
      expect((await post(app, '/api/restart/hold')).status).toBe(409);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('relays the API refusal when an update hold stands', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => new Response(
        JSON.stringify({ detail: 'an update hold is engaged — finish or cancel it before the restart' }),
        { status: 409 })));
      const res = await post(await buildApp(), '/api/restart/hold');
      expect(res.status).toBe(409);
      expect((await res.json() as { error: string }).error).toMatch(/update hold is engaged/);
    });

    it('an update apply is refused while a restart runs, and relays a restart hold\'s 409', async () => {
      const app = await buildApp();
      writeFileSync(join(dir, 'restart.json'), JSON.stringify({
        state: 'running', updated_at: new Date().toISOString(),
      }));
      expect((await apply(app, { target: '1.2.3' })).status).toBe(409);
      rmSync(join(dir, 'restart.json'));

      vi.stubGlobal('fetch', vi.fn(async () =>
        new Response(JSON.stringify({ detail: 'a restart hold is engaged' }), { status: 409 })));
      const res = await apply(app, { target: '1.2.3' });
      expect(res.status).toBe(409);
      expect((await res.json() as { error: string }).error).toBe('a restart hold is engaged');
    });

    it('proxies status, now and cancel to the API hold, always naming the restart kind', async () => {
      const fetchMock = vi.fn(async () => new Response(JSON.stringify({ active: true }), { status: 200 }));
      vi.stubGlobal('fetch', fetchMock);
      const app = await buildApp();
      expect((await app.request('/api/restart/hold')).status).toBe(200);
      expect((await post(app, '/api/restart/hold/now')).status).toBe(200);
      expect((await post(app, '/api/restart/hold/cancel')).status).toBe(200);
      const calls = fetchMock.mock.calls.map((c) => [String((c as unknown[])[0]), ((c as unknown[])[1] as RequestInit).method]);
      expect(calls).toEqual([
        ['http://cc.test/api/update/hold', 'GET'],
        ['http://cc.test/api/update/hold/now?kind=restart', 'POST'],
        ['http://cc.test/api/update/hold?kind=restart', 'DELETE'],
      ]);
    });
  });
});
