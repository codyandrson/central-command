/**
 * cc-skills — verbatim proxies for the Skills routes that the cockpit reaches
 * over HTTP rather than the WebSocket RPC dispatch table (`nerve_gateway.py`):
 * the three Skills-page ingest routes — import-doc and import-site (JSON),
 * import-file (multipart, the reason these are REST at all; a File doesn't
 * ride the RPC channel) — and the four bundled-skill update-review routes the
 * update dialog polls and posts to (compute, read, resolve one item, apply).
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

async function forward(c: Context, path: string, method: 'GET' | 'POST' = 'POST'): Promise<Response> {
  const res = await fetch(`${gvBase()}/api${path}`, method === 'GET' ? { method } : {
    method,
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

// The update review: the path is forwarded as received, so its percent-encoding
// reaches the backend as the dialog wrote it.
const reviewPath = (c: Context) => new URL(c.req.url).pathname.slice('/api'.length);
app.post('/api/skills/update-review', rateLimitGeneral, (c) => forward(c, '/skills/update-review'));
app.get('/api/skills/update-review/:target', rateLimitGeneral, (c) => forward(c, reviewPath(c), 'GET'));
app.post(
  '/api/skills/update-review/:target/items/:skill_id/:doc_key/resolve',
  rateLimitGeneral,
  (c) => forward(c, reviewPath(c)),
);
app.post('/api/skills/update-review/:target/apply', rateLimitGeneral, (c) => forward(c, reviewPath(c)));

export default app;
