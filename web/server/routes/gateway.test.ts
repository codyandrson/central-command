/** Tests for the gateway routes (models). */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { Hono } from 'hono';

vi.mock('../lib/config.js', () => ({
  config: {
    auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
    gatewayUrl: 'http://localhost:3100', gatewayToken: 'test-token',
  },
  SESSION_COOKIE_NAME: 'nerve_session_3000',
}));

vi.mock('../middleware/rate-limit.js', () => ({
  rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
}));

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
          // The id IS the label, slash or not: a LiteLLM alias is one name,
          // and the provider is always the proxy itself.
          { id: 'cc-default', label: 'cc-default', provider: 'litellm', configured: true, role: 'primary', thinkingLevels: ['off', 'low', 'medium', 'xhigh'] },
          { id: 'anthropic/claude-sonnet-5', label: 'anthropic/claude-sonnet-5', provider: 'litellm', configured: true, role: 'allowed', thinkingLevels: [] },
        ],
        error: null,
        source: 'litellm',
      });
    });

    it('defaults thinkingLevels to empty when the backend omits it', async () => {
      stubCc({ models: [{ id: 'cc-default' }], default: 'cc-default', error: null });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      const json = await res.json() as { models: Array<{ thinkingLevels: string[] }> };
      expect(json.models[0].thinkingLevels).toEqual([]);
    });

    it('marks nothing primary when the default is not in the catalog', async () => {
      stubCc({ models: [{ id: 'openai/gpt-5.2', thinking_levels: [] }], default: 'gone-from-the-proxy', error: null });

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      const json = await res.json() as { models: Array<{ role: string }> };
      expect(json.models.map(m => m.role)).toEqual(['allowed']);
    });

    it('passes the FastAPI error through instead of reporting an empty catalog', async () => {
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
      vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(res.status).toBe(200);
      const json = await res.json() as { models: unknown[]; error: string };
      expect(json.models).toEqual([]);
      expect(json.error).toMatch(/Could not reach Central Command.*ECONNREFUSED/);
    });

    it('reports a non-2xx from Central Command', async () => {
      stubCc({ detail: 'boom' }, 503);

      const app = buildApp();
      const res = await app.request('/api/gateway/models');
      expect(await res.json()).toEqual({ models: [], error: 'Central Command HTTP 503', source: 'litellm' });
    });

    it('returns an explicit error when the proxy serves no models', async () => {
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
      vi.doMock('../lib/config.js', () => ({
        config: {
          auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
          gatewayUrl: 'http://localhost:3100', gatewayToken: 'test-token',
        },
        SESSION_COOKIE_NAME: 'nerve_session_3000',
      }));
      vi.doMock('../middleware/rate-limit.js', () => ({
        rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
      }));
      const mod = await import('./gateway.js');
      expect(mod.MODEL_LIST_TIMEOUT_MS).toBeGreaterThanOrEqual(15_000);
    });
  });
});
