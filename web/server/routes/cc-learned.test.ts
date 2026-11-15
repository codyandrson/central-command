/**
 * cc-learned proxy — the Memory tab's two reads reach the backend path they
 * claim (a typo is a 404 the operator reads as "nothing learned"), carry the
 * `{ ok, result }` envelope, relay an unknown agent as 404 and everything
 * else as 502, and offer no write method.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-learned proxy', () => {
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
      config: { gatewayUrl: 'http://cc.test/' },
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    const mod = await import('./cc-learned.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('reads the notes from /agents/{id}/notes and wraps the payload', async () => {
    const app = await buildApp();
    const payload = { agent_id: 'jira-expert', rendered: true, private: [], operator: [],
      budgets: { private: 2000, operator: 1500 }, usage: { private: 0, operator: 0 } };
    fetchMock.mockResolvedValue(respond(payload));

    const res = await app.request('/api/cc/notes/jira-expert');

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, result: payload });
    expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/agents/jira-expert/notes');
  });

  it('reads the aggregate from /agents/{id}/learned', async () => {
    const app = await buildApp();
    const payload = { agent_id: 'jira-expert', skill_docs: [], pending: [] };
    fetchMock.mockResolvedValue(respond(payload));

    const res = await app.request('/api/cc/learned/jira-expert');

    expect(await res.json()).toEqual({ ok: true, result: payload });
    expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/agents/jira-expert/learned');
  });

  it('encodes the agent id into the backend path', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({}));
    await app.request('/api/cc/learned/a%2Fb');
    expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/agents/a%2Fb/learned');
  });

  it('relays an unknown agent as 404 with the backend detail', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ detail: "unknown agent 'nope'" }, 404));

    const res = await app.request('/api/cc/notes/nope');

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ ok: false, error: "unknown agent 'nope'" });
  });

  it('turns any other backend failure, and a network error, into a 502', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValueOnce(respond({ detail: 'database down' }, 500));
    const failed = await app.request('/api/cc/learned/jira-expert');
    expect(failed.status).toBe(502);
    expect(await failed.json()).toEqual({ ok: false, error: 'database down' });

    fetchMock.mockRejectedValueOnce(new Error('connect ECONNREFUSED'));
    const down = await app.request('/api/cc/notes/jira-expert');
    expect(down.status).toBe(502);
    expect(await down.json()).toEqual({ ok: false, error: 'connect ECONNREFUSED' });
  });

  it('has no write route: a POST reaches no backend path', async () => {
    const app = await buildApp();
    const res = await app.request('/api/cc/notes/jira-expert', { method: 'POST', body: '{}' });
    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
