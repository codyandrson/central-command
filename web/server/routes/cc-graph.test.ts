/**
 * cc-graph adapter — the episode-deletion routes (v2.61.0) pass the preview
 * and the finished-or-queued answer straight through.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

describe('cc-graph adapter: episode deletion', () => {
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
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }

  async function buildApp() {
    vi.doMock('../lib/config.js', () => ({ config: { gatewayUrl: 'http://cc.test' } }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    const mod = await import('./cc-graph.js');
    return mod.default;
  }

  it('proxies the preview read with its query', async () => {
    fetchMock.mockResolvedValueOnce(respond({ digest: 'd', facts: [] }));
    const app = await buildApp();
    const res = await app.request('/api/graph/episodes/delete-preview?uuid=ep-1');
    expect(fetchMock).toHaveBeenCalledWith(
      'http://cc.test/api/graph/episodes/delete-preview?uuid=ep-1', expect.anything(),
    );
    expect(await res.json()).toEqual({ ok: true, result: { digest: 'd', facts: [] } });
  });

  it('passes a 202 "queued" answer through as a result, with the digest forwarded', async () => {
    fetchMock.mockResolvedValueOnce(respond({ status: 'queued', job_id: 7 }, 202));
    const app = await buildApp();
    const res = await app.request('/api/graph/episode?uuid=ep-1&digest=d', { method: 'DELETE' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/graph/episode?uuid=ep-1&digest=d');
    expect(init.method).toBe('DELETE');
    expect(await res.json()).toEqual({ ok: true, result: { status: 'queued', job_id: 7 } });
  });

  it('turns a stale-digest refusal into an error the dialog can show', async () => {
    fetchMock.mockResolvedValueOnce(respond({ detail: 'the graph changed since this preview was read' }, 409));
    const app = await buildApp();
    const res = await app.request('/api/graph/episode?uuid=ep-1&digest=old', { method: 'DELETE' });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ ok: false, error: 'the graph changed since this preview was read' });
  });
});
