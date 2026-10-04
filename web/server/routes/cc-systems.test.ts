/**
 * cc-systems adapter — proxies GET /api/systems and maps snake_case wire
 * fields to camelCase, same pattern as cc-tokens.test.ts. Also covers the
 * self-check proxy (GET the last result, POST to start a run).
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
        {
          id: 'graphiti', name: 'Graphiti (in-process)', kind: 'api', url: null,
          status: 'up', latency_ms: null,
          detail: 'ingest queue: 2 queued, 0 running, 0 failed',
          credential: { label: 'key', location: 'CC_LLM_API_KEY' },
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

    // The optional status line rides through; rows without one carry null.
    expect(body.systems.find((s) => s.id === 'graphiti')!.detail)
      .toBe('ingest queue: 2 queued, 0 running, 0 failed');
    expect(n8n.detail).toBeNull();
  });

  it('relays a gateway error as 502', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ detail: 'boom' }, 500));

    const res = await app.request('/api/systems');
    expect(res.status).toBe(502);
    const body = await res.json() as { error: string };
    expect(body.error).toBe('boom');
  });

  describe('self-check proxy', () => {
    const DOC = {
      status: 'warn', running: false, ran_at: '2026-10-02T15:04:05Z', duration_ms: 1234,
      version: '2.56.0', mode: 'cli',
      checks: [
        { name: 'spine', status: 'pass', message: 'ok', remedy: null, system: 'postgres', duration_ms: 12 },
        { name: 'links', status: 'warn', message: '2 blank', remedy: 'set CC_LINK_X', system: null, duration_ms: 1 },
      ],
    };

    it('GET maps the document to camelCase and does not POST', async () => {
      const app = await buildApp();
      fetchMock.mockImplementation(async () => respond(DOC));

      const res = await app.request('/api/selfcheck');
      expect(res.status).toBe(200);
      const body = await res.json() as Record<string, unknown>;
      expect(body).toMatchObject({ status: 'warn', running: false, ranAt: '2026-10-02T15:04:05Z', durationMs: 1234, mode: 'cli' });
      expect((body.checks as Array<Record<string, unknown>>)[0]).toEqual({
        name: 'spine', status: 'pass', message: 'ok', remedy: null, system: 'postgres', durationMs: 12,
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/selfcheck');
      expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    });

    it('POST /run is forwarded upstream as a POST and relays the 202 document', async () => {
      const app = await buildApp();
      fetchMock.mockImplementation(async () => respond({ ...DOC, running: true }, 202));

      const res = await app.request('/api/selfcheck/run', { method: 'POST' });
      expect(res.status).toBe(202);
      expect((await res.json() as { running: boolean }).running).toBe(true);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/selfcheck/run');
      expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    });

    it('relays an upstream error as 502 on both routes', async () => {
      const app = await buildApp();
      fetchMock.mockImplementation(async () => respond({ detail: 'boom' }, 500));

      const get = await app.request('/api/selfcheck');
      expect(get.status).toBe(502);
      expect((await get.json() as { error: string }).error).toBe('boom');
      const post = await app.request('/api/selfcheck/run', { method: 'POST' });
      expect(post.status).toBe(502);
      expect((await post.json() as { error: string }).error).toBe('boom');
    });

    it('reports an unreachable gateway as 502', async () => {
      const app = await buildApp();
      fetchMock.mockImplementation(async () => { throw new Error('connect ECONNREFUSED'); });

      const res = await app.request('/api/selfcheck');
      expect(res.status).toBe(502);
      expect((await res.json() as { error: string }).error).toContain('ECONNREFUSED');
    });
  });
});
