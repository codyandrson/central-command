/**
 * cc-activity adapter — the Activity screen's proxy.
 *
 * These assert the two things a thin proxy can actually get wrong: that each
 * route reaches the BACKEND path it claims (a typo here is a 404 the operator
 * reads as "no activity"), and that query params — the facets and the keyset
 * cursor — survive the hop. A dropped `before_id` would page forever without
 * ever looking broken.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-activity adapter', () => {
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
    const mod = await import('./cc-activity.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  /** Capture every outbound URL so a route can be checked against the backend
   *  path it is supposed to hit, params and all. */
  function captureFetch(body: unknown = {}, status = 200) {
    const seen: { url: string; method: string }[] = [];
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      seen.push({ url, method: init?.method ?? 'GET' });
      return respond(body, status);
    });
    return seen;
  }

  it('maps each tab to its backend endpoint', async () => {
    const app = await buildApp();
    const seen = captureFetch({ ok: true });

    await app.request('/api/activity/overview');
    await app.request('/api/activity/runs');
    await app.request('/api/activity/events');
    await app.request('/api/activity/event-kinds');

    expect(seen.map(s => new URL(s.url).pathname)).toEqual([
      '/api/activity/overview',
      '/api/sessions',
      '/api/events',
      '/api/events/kinds',
    ]);
  });

  it('forwards the Runs facets and the keyset cursor verbatim', async () => {
    const app = await buildApp();
    const seen = captureFetch({ sessions: [], agents: [] });

    await app.request(
      '/api/activity/runs?limit=50&agent_id=inbox-triage&status=FAILED'
      + '&mode=oneshot&before=2026-08-11T00:00:00Z&before_id=sess_abc',
    );

    const params = new URL(seen[0].url).searchParams;
    expect(params.get('agent_id')).toBe('inbox-triage');
    expect(params.get('status')).toBe('FAILED');
    expect(params.get('mode')).toBe('oneshot');
    // Both halves of the cursor, or paging silently repeats the tie group.
    expect(params.get('before')).toBe('2026-08-11T00:00:00Z');
    expect(params.get('before_id')).toBe('sess_abc');
  });

  it('forwards the events cursor and order', async () => {
    const app = await buildApp();
    const seen = captureFetch({ events: [] });

    await app.request('/api/activity/events?order=desc&before=8038&kind=work.enrolled');

    const params = new URL(seen[0].url).searchParams;
    expect(params.get('order')).toBe('desc');
    expect(params.get('before')).toBe('8038');
    expect(params.get('kind')).toBe('work.enrolled');
  });

  it('posts the failure levers to the work endpoints', async () => {
    const app = await buildApp();
    const seen = captureFetch({ ok: true });

    await app.request('/api/activity/work/wi_1/requeue', { method: 'POST' });
    await app.request('/api/activity/work/wi_1/ack', { method: 'POST' });

    expect(seen.map(s => `${s.method} ${new URL(s.url).pathname}`)).toEqual([
      'POST /api/work/wi_1/requeue',
      'POST /api/work/wi_1/ack_failure',
    ]);
  });

  it('relays a 409 as a 409, not a transport failure', async () => {
    const app = await buildApp();
    // "already acknowledged" is a real answer the operator needs to see.
    captureFetch({ detail: 'item is not FAILED (or already acknowledged)' }, 409);

    const res = await app.request('/api/activity/work/wi_1/ack', { method: 'POST' });

    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain('already acknowledged');
  });
});
