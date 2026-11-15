/**
 * cc-learned — the workspace Memory tab's two per-agent reads: the agent's
 * note store (`GET /api/agents/{id}/notes`, DL-217) and the aggregate of what
 * else it has learned that no other endpoint returns for one agent
 * (`GET /api/agents/{id}/learned`: approved skill documents it proposed, and
 * its proposals still awaiting a decision).
 *
 * Read-only by design and by construction: there is no write route here
 * because there is none on the backend. A note, a skill document and a graph
 * episode all change only through a proposal the operator decides in the
 * Decisions Inbox. Thin proxy, same envelope and error passthrough as
 * cc-charter: an unknown agent is a 404, anything else a 502.
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

async function cc(path: string): Promise<Response> {
  return fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api${path}`, {
    headers: { 'content-type': 'application/json' },
  });
}

async function gvError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { detail?: string };
    return body.detail || `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

const agentPath = (agentId: string, leaf: 'notes' | 'learned') =>
  `/agents/${encodeURIComponent(agentId)}/${leaf}`;

async function proxy(c: Context, leaf: 'notes' | 'learned') {
  try {
    const res = await cc(agentPath(c.req.param('agentId') ?? '', leaf));
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 404 ? 404 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
}

app.get('/api/cc/notes/:agentId', rateLimitGeneral, (c) => proxy(c, 'notes'));
app.get('/api/cc/learned/:agentId', rateLimitGeneral, (c) => proxy(c, 'learned'));

export default app;
