/**
 * cc-charter — the agent's governed CHARTER, which is what "per-agent config"
 * means in Central Command.
 *
 * The vendored /api/workspace routes expect a Nerve filesystem workspace
 * (~/.openclaw/workspace/SOUL.md …) that does not exist here; the real
 * per-agent guidance is the DB-backed, versioned charter behind FastAPI's
 * GET/POST /api/agents/{id}/charter. This is a thin proxy — the payload is
 * passed through unchanged, because the charter's capability list is GENERATED
 * from grants at run start and must never be edited or synthesised in the UI.
 */
import { Hono } from 'hono';
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
    const body = await res.json() as { detail?: string };
    return body.detail || `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

const charterPath = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/charter`;

app.get('/api/cc/charter/:agentId', rateLimitGeneral, async (c) => {
  try {
    const res = await cc(charterPath(c.req.param('agentId')));
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 404 ? 404 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

app.post('/api/cc/charter/:agentId', rateLimitGeneral, async (c) => {
  try {
    const { content, rationale } = await c.req.json() as { content?: string; rationale?: string };
    const res = await cc(charterPath(c.req.param('agentId')), {
      method: 'POST',
      body: JSON.stringify({ content: content ?? '', rationale: rationale?.trim() || null }),
    });
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 422 ? 422 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

export default app;
