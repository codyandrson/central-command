/**
 * cc-mail-rules adapter — thin proxy over the API's /mail/rules endpoints.
 * Same mocking pattern as cc-systems.test.ts / cc-charter's proxy style.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-mail-rules adapter', () => {
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
    const mod = await import('./cc-mail-rules.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('passes include_revoked through on the list GET', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ rules: [{ id: 'r1' }] }));

    const res = await app.request('/api/cc/mail-rules?include_revoked=true');
    expect(res.status).toBe(200);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/mail/rules?include_revoked=true');
    const body = await res.json() as { ok: boolean; result: { rules: unknown[] } };
    expect(body.ok).toBe(true);
    expect(body.result.rules).toHaveLength(1);
  });

  it('defaults include_revoked to false when absent', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ rules: [] }));

    await app.request('/api/cc/mail-rules');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/mail/rules?include_revoked=false');
  });

  it('proxies a preview POST and returns the result', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ queued_matches: 3, samples: [], description: 'dismisses mail from x' }));

    const res = await app.request('/api/cc/mail-rules/preview', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ criteria: { from_domain: 'example.com' } }),
    });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/mail/rules/preview');
    expect(JSON.parse(String(init?.body))).toEqual({ criteria: { from_domain: 'example.com' } });
    const body = await res.json() as { ok: boolean; result: { queued_matches: number } };
    expect(body.result.queued_matches).toBe(3);
  });

  it('relays a 422 from create as 422', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ detail: 'no criteria given' }, 422));

    const res = await app.request('/api/cc/mail-rules', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ criteria: {}, reason: 'x', apply_to_queued: true }),
    });
    expect(res.status).toBe(422);
    const body = await res.json() as { ok: boolean; error: string };
    expect(body.ok).toBe(false);
    expect(body.error).toBe('no criteria given');
  });

  it('proxies revoke to the id-scoped path', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ ok: true }));

    const res = await app.request('/api/cc/mail-rules/rule-1/revoke', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ reason: 'no longer needed' }),
    });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/mail/rules/rule-1/revoke');
    expect(JSON.parse(String(init?.body))).toEqual({ reason: 'no longer needed' });
  });

  it('relays a 404 from revoke as 404', async () => {
    const app = await buildApp();
    fetchMock.mockImplementation(async () => respond({ detail: 'not found' }, 404));

    const res = await app.request('/api/cc/mail-rules/missing/revoke', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(404);
  });
});
