/**
 * cc-loe — Goals (lines of effort) screen backend.
 *
 * One GET passthrough over the Central Command gateway's `/api/loe`, same shape
 * as cc-crons/cc-activity: `gvBase`/`cc` helper, rateLimitGeneral, wrapped in
 * the `{ok: true, result: ...}` envelope the cockpit's fetch hooks expect.
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

const gvBase = () => config.gatewayUrl.replace(/\/+$/, '');

async function cc(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${gvBase()}/api${path}`, {
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

const fail = (c: Context, error: unknown, status: 502 = 502) =>
  c.json({ ok: false, error: error instanceof Error ? error.message : String(error) }, status);

app.get('/api/loe', rateLimitGeneral, async (c) => {
  try {
    const history = c.req.query('history');
    const qs = history ? `?history=${encodeURIComponent(history)}` : '';
    const res = await cc(`/loe${qs}`);
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
});

export default app;
