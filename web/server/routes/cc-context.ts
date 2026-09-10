/**
 * cc-context — Settings › Context (2026-09-10): the operator's toggles for
 * what the runtime does to an agent's WORKING window as it fills (drop old
 * reasoning, clear old tool output, warn the agent, reserve output headroom,
 * summarize). Thin proxy over the API's `/context/settings`, same envelope
 * as cc-graph.
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

async function ccError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { detail?: string };
    return body.detail || `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

app.get('/api/context/settings', rateLimitGeneral, async (c) => {
  try {
    const res = await cc('/context/settings');
    if (!res.ok) throw new Error(await ccError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

app.put('/api/context/settings', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json();
    const res = await cc('/context/settings', { method: 'PUT', body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await ccError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

export default app;
