/**
 * cc-decisions — verbatim proxies for the Bulk Dismiss dialog's two REST
 * routes, which are NOT in the WebSocket RPC dispatch table
 * (`nerve_gateway.py`). Same story as cc-skills: the dialog shipped calling
 * `fetch('/api/work/bulk_dismiss…')` with no Hono route behind it, so both
 * buttons 404'd while the FastAPI handlers sat tested and unreachable
 * (found by the 2026-08-20 route audit — the parity test missed it because
 * the dialog fetches through a `postJSON` wrapper the guard's regex didn't
 * see; the guard now matches the house wrappers too).
 *
 * Nothing is reshaped: the dialog already speaks the FastAPI contract,
 * so the proxy forwards the JSON body and returns status + body untouched.
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
    headers: { 'content-type': c.req.header('content-type') ?? 'application/json' },
    body: await c.req.arrayBuffer(),
  });
  return new Response(await res.arrayBuffer(), {
    status: res.status,
    headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
  });
}

app.post('/api/work/bulk_dismiss/preview', rateLimitGeneral, (c) => forward(c, '/work/bulk_dismiss/preview'));
app.post('/api/work/bulk_dismiss', rateLimitGeneral, (c) => forward(c, '/work/bulk_dismiss'));

export default app;
