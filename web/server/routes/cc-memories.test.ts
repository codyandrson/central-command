/**
 * cc-memories adapter — D11-r1 scoped reads: agentId forwarded as agent_id,
 * scope passed through as a wire field (not text-mangled), writes still gated.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-memories adapter', () => {
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
    const mod = await import('./cc-memories.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('forwards agentId as agent_id, defaults scope=private, and carries total/hasMore', async () => {
    const app = await buildApp();
    let requestedUrl: string | null = null;
    fetchMock.mockImplementation(async (url: string) => {
      requestedUrl = url;
      return respond({
        episodes: [
          { uuid: '2', name: 'private fact', content: 'body2', created_at: '2026-08-02T00:00:00Z', scope: 'private' },
        ],
        total: 7,
        has_more: true,
      });
    });

    const res = await app.request('/api/memories?agentId=jira-expert');
    expect(res.status).toBe(200);
    const url = new URL(requestedUrl as unknown as string);
    expect(url.searchParams.get('agent_id')).toBe('jira-expert');
    expect(url.searchParams.get('scope')).toBe('private');
    expect(url.searchParams.get('limit')).toBe('50');

    const body = await res.json() as { memories: Array<{ text: string; scope: string }>; total: number; hasMore: boolean };
    expect(body.total).toBe(7);
    expect(body.hasMore).toBe(true);
    const priv = body.memories.find((m) => m.text.includes('private fact'));
    expect(priv?.scope).toBe('private');
    expect(priv?.text).not.toContain('[private]');
    // no markdown-bold artifact either — text is plain, rendered verbatim
    expect(priv?.text).not.toContain('**');
  });

  it('forwards a custom limit and passes through with no agentId, defaulting scope to shared', async () => {
    const app = await buildApp();
    let requestedUrl: string | null = null;
    fetchMock.mockImplementation(async (url: string) => {
      requestedUrl = url;
      return respond({
        episodes: [
          { uuid: '1', name: 'shared fact', content: 'body', created_at: '2026-08-01T00:00:00Z' },
        ],
        total: 1,
        has_more: false,
      });
    });

    const res = await app.request('/api/memories?limit=100');
    expect(res.status).toBe(200);
    const url = new URL(requestedUrl as unknown as string);
    expect(url.searchParams.has('agent_id')).toBe(false);
    expect(url.searchParams.get('limit')).toBe('100');

    const body = await res.json() as { memories: Array<{ text: string; scope: string }>; total: number; hasMore: boolean };
    expect(body.memories[0].text).not.toContain('[private]');
    expect(body.memories[0].scope).toBe('shared');
    expect(body.total).toBe(1);
    expect(body.hasMore).toBe(false);
  });

  it('still refuses writes with 501', async () => {
    const app = await buildApp();

    const post = await app.request('/api/memories', { method: 'POST' });
    expect(post.status).toBe(501);

    const del = await app.request('/api/memories', { method: 'DELETE' });
    expect(del.status).toBe(501);

    const sectionPut = await app.request('/api/memories/section', { method: 'PUT' });
    expect(sectionPut.status).toBe(501);
  });
});
