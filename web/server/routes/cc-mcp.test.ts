/**
 * cc-mcp proxy — the five MCP-tool operations reach the backend path they
 * claim, carry the `{ ok, result }` envelope, and relay 404/409/422 with the
 * API's own text (the screen shows it inline) while anything else is a 502.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('cc-mcp proxy', () => {
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
    const mod = await import('./cc-mcp.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('lists servers from /mcp/servers and wraps the payload', async () => {
    const app = await buildApp();
    const payload = { servers: [{ id: 'echo-demo', tools: [], holders: [] }] };
    fetchMock.mockResolvedValue(respond(payload));
    const res = await app.request('/api/cc/mcp/servers');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, result: payload });
    expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/mcp/servers');
  });

  it('PATCHes a tool default gate and forwards only default_gate', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ server_id: 'echo-demo', tool_name: 'echo', default_gate: 'ungated' }));
    const res = await app.request('/api/cc/mcp/servers/echo-demo/tools/echo', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ default_gate: 'ungated', extra: 'dropped' }),
    });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/mcp/servers/echo-demo/tools/echo');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ default_gate: 'ungated' });
  });

  it('reads an agent\'s MCP overrides from /agents/{id}/mcp-overrides', async () => {
    const app = await buildApp();
    const payload = { agent_id: 'jira-expert', mcp_grants: [], overrides: [], servers: [] };
    fetchMock.mockResolvedValue(respond(payload));
    const res = await app.request('/api/cc/mcp-overrides/jira-expert');
    expect(await res.json()).toEqual({ ok: true, result: payload });
    expect(fetchMock.mock.calls[0][0]).toBe('http://cc.test/api/agents/jira-expert/mcp-overrides');
  });

  it('PUTs an override with exactly server_id, tool_name and gate', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ overrides: [] }));
    const res = await app.request('/api/cc/mcp-overrides/jira-expert', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ server_id: 'echo-demo', tool_name: 'echo', gate: 'ungated', x: 1 }),
    });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/agents/jira-expert/mcp-overrides');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ server_id: 'echo-demo', tool_name: 'echo', gate: 'ungated' });
  });

  it('DELETEs an override by query string', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ overrides: [] }));
    const res = await app.request('/api/cc/mcp-overrides/jira-expert?server_id=echo-demo&tool_name=echo', {
      method: 'DELETE',
    });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://cc.test/api/agents/jira-expert/mcp-overrides?server_id=echo-demo&tool_name=echo');
    expect(init.method).toBe('DELETE');
  });

  it.each([404, 409, 422])('relays a %i with the API text', async (status) => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ detail: `refused ${status}` }, status));
    const res = await app.request('/api/cc/mcp-overrides/jira-expert?server_id=a&tool_name=b', {
      method: 'DELETE',
    });
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ ok: false, error: `refused ${status}` });
  });

  it('maps any other failure to 502', async () => {
    const app = await buildApp();
    fetchMock.mockResolvedValue(respond({ detail: 'boom' }, 500));
    const res = await app.request('/api/cc/mcp/servers');
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ ok: false, error: 'boom' });
    fetchMock.mockRejectedValue(new Error('connect refused'));
    const res2 = await app.request('/api/cc/mcp/servers');
    expect(res2.status).toBe(502);
  });
});
