/**
 * cc-update-proxy — the single-node profile's update routes.
 *
 * On k3s the Node server OWNS /api/update/* and /api/version/check (root
 * helper units, local git tags). On the single-node profile the FastAPI app
 * owns them (deploy/single/update.sh through a detached runner, upload from
 * file), and this server merely fronts the cockpit — so with
 * CC_UPDATE_BACKEND=api in web/.env these literal registrations forward each
 * call to the gateway, body streamed, and are mounted BEFORE the /api/*
 * bodyLimit so a release zip (hundreds of MB) is not a 413 here
 * (2026-09-18 Windows run: "Up to date" at v2.27.3 with v2.36.4 published,
 * and Update from file → 413).
 */
import { Hono, type Context } from 'hono';
import { config } from '../lib/config.js';

const app = new Hono();

async function forward(c: Context): Promise<Response> {
  const url = new URL(c.req.url);
  const target = `${config.gatewayUrl.replace(/\/+$/, '')}${url.pathname}${url.search}`;
  const method = c.req.method;
  const headers: Record<string, string> = {};
  const ct = c.req.header('content-type');
  if (ct) headers['content-type'] = ct;
  try {
    const res = await fetch(target, {
      method,
      headers,
      body: method === 'GET' || method === 'HEAD' ? undefined : c.req.raw.body,
      duplex: 'half',
    });
    return new Response(res.body, {
      status: res.status,
      headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
    });
  } catch (err) {
    return c.json({ error: `gateway unreachable: ${err instanceof Error ? err.message : String(err)}` }, 502);
  }
}

// Literal registrations, not a table: api-route-parity.test.ts reads
// `app.<method>('/api/...')` from source.
app.get('/api/version/check', forward);
app.get('/api/update/status', forward);
app.post('/api/update/stage', forward);
app.post('/api/update/upload', forward);
app.post('/api/update/apply', forward);

export default app;
