/**
 * cc-mcp — the cockpit's "MCP tools" screen: the operator's per-tool default
 * gate and per-agent override for the tools of a registered MCP server.
 * Thin proxy over five API routes, the same `{ ok, result | error }` envelope
 * and error shape as cc-mail-rules:
 *
 *   GET    /api/cc/mcp/servers                        -> GET    /mcp/servers
 *   PATCH  /api/cc/mcp/servers/:server/tools/:tool    -> PATCH  /mcp/servers/{id}/tools/{tool}
 *   GET    /api/cc/mcp-overrides/:agentId             -> GET    /agents/{id}/mcp-overrides
 *   PUT    /api/cc/mcp-overrides/:agentId             -> PUT    /agents/{id}/mcp-overrides
 *   DELETE /api/cc/mcp-overrides/:agentId?server_id=&tool_name=
 *                                                     -> DELETE /agents/{id}/mcp-overrides
 *
 * The writes are the operator's own direct actions (no proposal exists for
 * them) and the API decides what is valid; this proxy adds no rule of its
 * own. 404, 409 and 422 pass through with the API's text so the screen can
 * show it inline; anything else is a 502.
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

async function cc(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
}

async function gvError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return body.detail;
    if (body.detail !== undefined) return JSON.stringify(body.detail);
    return `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

const RELAYED = new Set([404, 409, 422]);

async function relay(c: Context, path: string, init?: RequestInit) {
  try {
    const res = await cc(path, init);
    if (!res.ok) {
      return c.json(
        { ok: false, error: await gvError(res) },
        (RELAYED.has(res.status) ? res.status : 502) as 404 | 409 | 422 | 502,
      );
    }
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
}

const enc = encodeURIComponent;
const overridesPath = (c: Context) => `/agents/${enc(c.req.param('agentId') ?? '')}/mcp-overrides`;

app.get('/api/cc/mcp/servers', rateLimitGeneral, (c) => relay(c, '/mcp/servers'));

app.patch('/api/cc/mcp/servers/:serverId/tools/:toolName', rateLimitGeneral, async (c) => {
  const body = await c.req.json().catch(() => ({})) as { default_gate?: string };
  return relay(
    c,
    `/mcp/servers/${enc(c.req.param('serverId') ?? '')}/tools/${enc(c.req.param('toolName') ?? '')}`,
    { method: 'PATCH', body: JSON.stringify({ default_gate: body.default_gate }) },
  );
});

app.get('/api/cc/mcp-overrides/:agentId', rateLimitGeneral, (c) => relay(c, overridesPath(c)));

app.put('/api/cc/mcp-overrides/:agentId', rateLimitGeneral, async (c) => {
  const body = await c.req.json().catch(() => ({})) as {
    server_id?: string; tool_name?: string; gate?: string;
  };
  return relay(c, overridesPath(c), {
    method: 'PUT',
    body: JSON.stringify({ server_id: body.server_id, tool_name: body.tool_name, gate: body.gate }),
  });
});

app.delete('/api/cc/mcp-overrides/:agentId', rateLimitGeneral, (c) => {
  const q = new URLSearchParams({
    server_id: c.req.query('server_id') ?? '',
    tool_name: c.req.query('tool_name') ?? '',
  });
  return relay(c, `${overridesPath(c)}?${q.toString()}`, { method: 'DELETE' });
});

export default app;
