/** Tests for the gateway routes (models, restart). */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { Hono } from 'hono';

let execFileImpl: (...args: unknown[]) => void;

vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>();
  const mock = { ...actual, execFile: (...args: unknown[]) => execFileImpl(...args) };
  return { ...mock, default: mock };
});

vi.mock('../lib/config.js', () => ({
  config: {
    auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
    gatewayUrl: 'http://localhost:3100', gatewayToken: 'test-token',
  },
  SESSION_COOKIE_NAME: 'nerve_session_3000',
}));

vi.mock('../middleware/rate-limit.js', () => ({
  rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
  rateLimitRestart: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
}));

vi.mock('../lib/openclaw-bin.js', () => ({
  resolveOpenclawBin: () => '/usr/bin/openclaw',
}));

const socketMock = vi.hoisted(() => ({
  connectOk: false as boolean,
}));

vi.mock('node:net', () => {
  class MockSocket {
    private handlers: Record<string, (() => void)[]> = {};
    setTimeout() { return this; }
    connect(_port: number, _host: string, cb: () => void) {
      queueMicrotask(() => {
        if (socketMock.connectOk) { cb(); }
        else { this.emit('error'); }
      });
      return this;
    }
    on(event: string, handler: () => void) {
      (this.handlers[event] ??= []).push(handler);
      return this;
    }
    end() {}
    destroy() {}
    private emit(event: string) {
      for (const h of this.handlers[event] ?? []) h();
    }
  }
  return { Socket: MockSocket, default: { Socket: MockSocket } };
});

import gatewayRoutes from './gateway.js';

function buildApp() {
  const app = new Hono();
  app.route('/', gatewayRoutes);
  return app;
}

describe('gateway routes', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function setDefaults() {
    execFileImpl = (_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
      (cb as (err: null, stdout: string) => void)(null, '');
    };
  }

  describe('GET /api/gateway/models', () => {
    /**
     * The catalog is Central Command's LiteLLM proxy now, reached over HTTP — the
     * openclaw.json read this route used to do had no file to read on this
     * deployment, which is what produced the cockpit's permanent
     * "Could not read OpenClaw config" badge.
     */
    function stubCc(body: unknown, status = 200) {
      const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
        status, headers: { 'content-type': 'application/json' },
      }));
      vi.stubGlobal('fetch', fetchMock);
      return fetchMock;
    }

    it('maps the LiteLLM catalog, marking the default primary and the rest allowed', async () => {
      setDefaults();
      const fetchMock = stubCc({
        models: [
          { id: 'cc-default', thinking_levels: ['off', 'low', 'medium', 'xhigh'] },
          { id: 'anthropic/claude-sonnet-5', thinking_levels: [] },
        ],
        default: 'cc-default',
        override: null,
        error: null,
      });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(res.status).toBe(200);
      expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:3100/api/models');
      expect(await res.json()).toEqual({
        models: [
          // A slash-less LiteLLM alias has no provider segment to split off, so
          // the id IS the label and the provider is the proxy itself.
          { id: 'cc-default', label: 'cc-default', provider: 'litellm', configured: true, role: 'primary', thinkingLevels: ['off', 'low', 'medium', 'xhigh'] },
          { id: 'anthropic/claude-sonnet-5', label: 'claude-sonnet-5', provider: 'anthropic', configured: true, role: 'allowed', thinkingLevels: [] },
        ],
        error: null,
        source: 'litellm',
      });
    });

    it('defaults thinkingLevels to empty when the backend omits it', async () => {
      setDefaults();
      stubCc({ models: [{ id: 'cc-default' }], default: 'cc-default', error: null });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      const json = await res.json() as { models: Array<{ thinkingLevels: string[] }> };
      expect(json.models[0].thinkingLevels).toEqual([]);
    });

    it('marks nothing primary when the default is not in the catalog', async () => {
      setDefaults();
      stubCc({ models: [{ id: 'openai/gpt-5.2', thinking_levels: [] }], default: 'gone-from-the-proxy', error: null });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      const json = await res.json() as { models: Array<{ role: string }> };
      expect(json.models.map(m => m.role)).toEqual(['allowed']);
    });

    it('passes the FastAPI error through instead of reporting an empty catalog', async () => {
      setDefaults();
      stubCc({ models: [], default: 'cc-default', error: 'model catalog — http://localhost:4000 unreachable: timed out' });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({
        models: [],
        error: 'model catalog — http://localhost:4000 unreachable: timed out',
        source: 'litellm',
      });
    });

    it('says so when Central Command itself is unreachable', async () => {
      setDefaults();
      vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(res.status).toBe(200);
      const json = await res.json() as { models: unknown[]; error: string };
      expect(json.models).toEqual([]);
      expect(json.error).toMatch(/Could not reach Central Command.*ECONNREFUSED/);
    });

    it('reports a non-2xx from Central Command', async () => {
      setDefaults();
      stubCc({ detail: 'boom' }, 503);

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(await res.json()).toEqual({ models: [], error: 'Central Command HTTP 503', source: 'litellm' });
    });

    it('returns an explicit error when the proxy serves no models', async () => {
      setDefaults();
      stubCc({ models: [], default: 'cc-default', error: null });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(await res.json()).toEqual({
        models: [],
        error: 'LiteLLM served no models.',
        source: 'litellm',
      });
    });


    it('uses a long enough timeout for model catalog fetches', async () => {
      vi.resetModules();
      vi.doMock('node:child_process', () => ({
        execFile: (...args: unknown[]) => execFileImpl(...args),
      }));
      vi.doMock('../lib/config.js', () => ({
        config: {
          auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
          gatewayUrl: 'http://localhost:3100', gatewayToken: 'test-token',
        },
        SESSION_COOKIE_NAME: 'nerve_session_3000',
      }));
      vi.doMock('../middleware/rate-limit.js', () => ({
        rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
        rateLimitRestart: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
      }));
      vi.doMock('../lib/openclaw-bin.js', () => ({
        resolveOpenclawBin: () => '/usr/bin/openclaw',
      }));
      const mod = await import('./gateway.js');
      expect(mod.MODEL_LIST_TIMEOUT_MS).toBeGreaterThanOrEqual(15_000);
    });
  });

  describe('POST /api/gateway/restart', () => {
    afterEach(() => {
      socketMock.connectOk = false;
      vi.useRealTimers();
    });

    it('returns 500 when restart command fails', async () => {
      vi.useFakeTimers();
      execFileImpl = (_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
        (cb as (err: Error, stdout: string) => void)(new Error('restart failed'), '');
      };
      const app = buildApp();
      const resPromise = app.request('/api/gateway/restart', { method: 'POST' });
      await vi.runAllTimersAsync();
      const res = await resPromise;
      expect(res.status).toBe(500);
      const json = await res.json() as { ok: boolean; output: string };
      expect(json.ok).toBe(false);
    });

    it('returns 500 when status indicates stopped', async () => {
      vi.useFakeTimers();
      let call = 0;
      execFileImpl = (_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
        if (call === 0) {
          call++;
          (cb as (err: null, stdout: string) => void)(null, 'Restarted');
        } else {
          (cb as (err: null, stdout: string) => void)(null, 'Runtime: stopped\nlast exit 1');
        }
      };
      const app = buildApp();
      const resPromise = app.request('/api/gateway/restart', { method: 'POST' });
      await vi.runAllTimersAsync();
      const res = await resPromise;
      expect(res.status).toBe(500);
      const json = await res.json() as { ok: boolean; output: string };
      expect(json.ok).toBe(false);
      expect(json.output).toMatch(/Status:/);
      expect(json.output).toMatch(/Runtime: stopped/);
    });

    it('provides DBus session env fallbacks when vars are missing', async () => {
      vi.useFakeTimers();
      const origXdg = process.env.XDG_RUNTIME_DIR;
      const origDbus = process.env.DBUS_SESSION_BUS_ADDRESS;
      delete process.env.XDG_RUNTIME_DIR;
      delete process.env.DBUS_SESSION_BUS_ADDRESS;

      let capturedEnv: Record<string, string> | undefined;
      execFileImpl = (_bin: unknown, _args: unknown, opts: unknown, cb: unknown) => {
        capturedEnv = (opts as { env: Record<string, string> }).env;
        (cb as (err: Error, stdout: string) => void)(new Error('restart failed'), '');
      };
      const app = buildApp();
      const resPromise = app.request('/api/gateway/restart', { method: 'POST' });
      await vi.runAllTimersAsync();
      await resPromise;

      expect(capturedEnv).toBeDefined();
      expect(capturedEnv!.XDG_RUNTIME_DIR).toMatch(/^\/run\/user\/\d+$/);
      expect(capturedEnv!.DBUS_SESSION_BUS_ADDRESS).toMatch(/^unix:path=\/run\/user\/\d+\/bus$/);

      // Restore
      if (origXdg !== undefined) process.env.XDG_RUNTIME_DIR = origXdg;
      if (origDbus !== undefined) process.env.DBUS_SESSION_BUS_ADDRESS = origDbus;
    });

    it('returns 200 on successful restart with port reachable', async () => {
      vi.useFakeTimers();
      socketMock.connectOk = true;
      let call = 0;
      execFileImpl = (_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
        if (call === 0) {
          call++;
          (cb as (err: null, stdout: string) => void)(null, 'Restarted');
        } else {
          (cb as (err: null, stdout: string) => void)(null, 'Runtime: running');
        }
      };
      const app = buildApp();
      const resPromise = app.request('/api/gateway/restart', { method: 'POST' });
      await vi.runAllTimersAsync();
      const res = await resPromise;
      expect(res.status).toBe(200);
      const json = await res.json() as { ok: boolean; output: string };
      expect(json.ok).toBe(true);
      expect(json.output).toMatch(/successfully/i);
    });

    it('returns 500 when status says running but port is not reachable', async () => {
      vi.useFakeTimers();
      socketMock.connectOk = false;
      let call = 0;
      execFileImpl = (_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
        if (call === 0) {
          call++;
          (cb as (err: null, stdout: string) => void)(null, 'Restarted');
        } else {
          (cb as (err: null, stdout: string) => void)(null, 'Runtime: running');
        }
      };
      const app = buildApp();
      const resPromise = app.request('/api/gateway/restart', { method: 'POST' });
      await vi.runAllTimersAsync();
      const res = await resPromise;
      expect(res.status).toBe(500);
      const json = await res.json() as { ok: boolean; output: string };
      expect(json.ok).toBe(false);
      expect(json.output).toMatch(/port not ready/);
    });
  });
});
