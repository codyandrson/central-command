/**
 * cc-skills proxy — the contract is "verbatim": body forwarded, backend
 * status + body returned untouched, multipart content-type preserved.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-skills proxy', () => {
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

  async function buildApp() {
    vi.doMock('../lib/config.js', () => ({
      config: { gatewayUrl: 'http://cc.test' },
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    const mod = await import('./cc-skills.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('forwards import-site JSON to the gateway and returns its body verbatim', async () => {
    const app = await buildApp();
    const result = { imported: [{ doc_key: 'x' }], skipped: [], truncated: false, rung: 'llms-full', notes: [] };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(result), {
      status: 200, headers: { 'content-type': 'application/json' },
    }));

    const body = JSON.stringify({ skill_id: 'jira', url: 'https://example.com/docs' });
    const res = await app.request('/api/skills/import-site', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(result);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/import-site');
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(body);
  });

  it('passes backend errors through with status and {detail} intact', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "unknown skill 'nope'" }), {
      status: 404, headers: { 'content-type': 'application/json' },
    }));

    const res = await app.request('/api/skills/import-doc', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ skill_id: 'nope', doc_key: 'k', content: 'x' }),
    });

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ detail: "unknown skill 'nope'" });
  });

  it('preserves the multipart content-type (boundary and all) on import-file', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ version: 1 }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }));

    const contentType = 'multipart/form-data; boundary=----vitest123';
    const res = await app.request('/api/skills/import-file', {
      method: 'POST', headers: { 'content-type': contentType }, body: 'raw-multipart-bytes',
    });

    expect(res.status).toBe(200);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['content-type']).toBe(contentType);
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('raw-multipart-bytes');
  });

  it('forwards the update-review compute POST body untouched', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ target: '2.68.0' }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }));

    const body = JSON.stringify({ target: '2.68.0', ref: 'v2.68.0' });
    const res = await app.request('/api/skills/update-review', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });

    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/update-review');
    expect(init.method).toBe('POST');
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(body);
  });

  it('forwards the latest-review GET (no target) and keeps a 404 intact', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'no bundled-skill review is stored' }), {
      status: 404, headers: { 'content-type': 'application/json' },
    }));

    const res = await app.request('/api/skills/update-review');

    expect(res.status).toBe(404);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/update-review');
    expect(init.method).toBe('GET');
    expect(init.body).toBeUndefined();
  });

  it('forwards the update-review GET with no body and keeps a 404 intact', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "no bundled-skill review is stored for '2.68.0'" }), {
      status: 404, headers: { 'content-type': 'application/json' },
    }));

    const res = await app.request('/api/skills/update-review/2.68.0');

    expect(res.status).toBe(404);
    expect((await res.json() as { detail: string }).detail).toContain('no bundled-skill review');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/update-review/2.68.0');
    expect(init.method).toBe('GET');
    expect(init.body).toBeUndefined();
  });

  it('forwards the apply to its path and passes a 409 through', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: '1 docs still need a verdict' }), {
      status: 409, headers: { 'content-type': 'application/json' },
    }));

    const body = JSON.stringify({ actor: 'updater' });
    const res = await app.request('/api/skills/update-review/2.68.0/apply', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });

    expect(res.status).toBe(409);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/update-review/2.68.0/apply');
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(body);
  });

  it('forwards a verdict to the item path and passes a 409 through', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'the doc changed' }), {
      status: 409, headers: { 'content-type': 'application/json' },
    }));

    const body = JSON.stringify({ verdict: 'keep_mine', ours_hash: 'a', theirs_hash: null });
    const res = await app.request('/api/skills/update-review/2.68.0/items/jira/api/resolve', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });

    expect(res.status).toBe(409);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://cc.test/api/skills/update-review/2.68.0/items/jira/api/resolve');
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(body);
  });
});
