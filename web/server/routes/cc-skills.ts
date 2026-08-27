/**
 * cc-skills — verbatim proxies for the three Skills-page ingest routes that
 * are NOT in the WebSocket RPC dispatch table (`nerve_gateway.py`):
 * import-doc and import-site (JSON), import-file (multipart — the reason
 * these are REST at all; a File doesn't ride the RPC channel).
 *
 * Every other Skills operation goes over RPC. These three shipped 2026-08-07
 * as direct `fetch('/api/skills/…')` calls with no Hono route behind them, so
 * the cockpit 404'd them for twelve days while the FastAPI routes sat tested
 * and unreachable. `web/server/api-route-parity.test.ts` now guards the class.
 *
 * Unlike cc-crons, nothing is reshaped: the frontend already speaks the
 * FastAPI contract (including `{detail}` error bodies), so the proxy forwards
 * the request body and returns the backend's status + body untouched.
 *
 * Ceilings, both acceptable: uploads are capped by app.ts's bodyLimit
 * (~13 MB) below the backend's 25 MB, and a very slow site import is bounded
 * by undici's ~300 s response-headers timeout — raise those where they live
 * if a real import ever hits them.
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

const gvBase = () => config.gatewayUrl.replace(/\/+$/, '');

async function forward(c: Context, path: string): Promise<Response> {
  const res = await fetch(`${gvBase()}/api${path}`, {
    method: 'POST',
    // Content-type passes through as-is so a multipart boundary survives.
    headers: { 'content-type': c.req.header('content-type') ?? 'application/json' },
    body: await c.req.arrayBuffer(),
  });
  return new Response(await res.arrayBuffer(), {
    status: res.status,
    headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
  });
}

app.post('/api/skills/import-doc', rateLimitGeneral, (c) => forward(c, '/skills/import-doc'));
app.post('/api/skills/import-file', rateLimitGeneral, (c) => forward(c, '/skills/import-file'));
app.post('/api/skills/import-site', rateLimitGeneral, (c) => forward(c, '/skills/import-site'));

export default app;
