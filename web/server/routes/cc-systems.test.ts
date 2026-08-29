/**
 * cc-systems adapter — proxies GET /api/systems and maps snake_case wire
 * fields to camelCase, same pattern as cc-tokens.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-systems adapter', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function respond(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status, headers: { 'content-type': 'application/json' },
    });
  }

  async function buildApp() {
    vi.doMock('../lib/config.js', () => ({
      config: { gatewayUrl: 'http://cc.test' },
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    const mod = await import('./cc-systems.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('maps snake_case fields to camelCase and passes url/status/credential through', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({
      systems: [
        {
          id: 'n8n', name: 'n8n', kind: 'ui', url: 'https://n8n.tail.example/', status: 'up', latency_ms: 12.3,
          credential: { label: 'n8n login', location: 'n8n login + N8N_ENCRYPTION_KEY' },
        },
        {
          id: 'postgres', name: 'Postgres', kind: 'external', url: null,
          status: 'down', latency_ms: null,
          credential: { label: 'DB URL', location: 'CC_DATABASE_URL' },
        },
      ],
    }));

    const res = await app.request('/api/systems');
    expect(res.status).toBe(200);
    const body = await res.json() as { systems: Array<Record<string, unknown>> };
    const n8n = body.systems.find((s) => s.id === 'n8n')!;
    expect(n8n.latencyMs).toBe(12.3);
    expect(n8n.url).toBe('https://n8n.tail.example/');
    expect(n8n.status).toBe('up');
    expect(n8n.credential).toEqual({ label: 'n8n login', location: 'n8n login + N8N_ENCRYPTION_KEY' });

    const pg = body.systems.find((s) => s.id === 'postgres')!;
    expect(pg.status).toBe('down');
    expect(pg.url).toBeNull();
  });

  it('relays a gateway error as 502', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ detail: 'boom' }, 500));

    const res = await app.request('/api/systems');
    expect(res.status).toBe(502);
    const body = await res.json() as { error: string };
    expect(body.error).toBe('boom');
  });
});
