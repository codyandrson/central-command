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
import { request as httpRequest } from 'node:http';
import { Readable } from 'node:stream';
import { config } from '../lib/config.js';

const app = new Hono();

// node:http, not fetch: undici's fetch gives up on response headers after
// 300 s, and `POST /api/update/upload` stages the zip (init/import/plan)
// inside the request — 13 minutes on a Windows box (2026-09-18: "gateway
// unreachable: fetch failed" at 334 s while the API went on staging). No
// timeout here; the API's own gates bound the work.
function forward(c: Context): Promise<Response> {
  const url = new URL(c.req.url);
  const target = new URL(`${config.gatewayUrl.replace(/\/+$/, '')}${url.pathname}${url.search}`);
  const method = c.req.method;
  const headers: Record<string, string> = {};
  const ct = c.req.header('content-type');
  if (ct) headers['content-type'] = ct;
  const cl = c.req.header('content-length');
  if (cl) headers['content-length'] = cl;
  return new Promise((resolve) => {
    const req = httpRequest(
      { host: target.hostname, port: target.port || 80, path: `${target.pathname}${target.search}`, method, headers },
      (res) => {
        resolve(new Response(Readable.toWeb(res) as ReadableStream, {
          status: res.statusCode ?? 502,
          headers: { 'content-type': res.headers['content-type'] ?? 'application/json' },
        }));
      },
    );
    req.on('error', (err) => resolve(c.json({ error: `gateway unreachable: ${err.message}` }, 502)));
    if (method === 'GET' || method === 'HEAD' || !c.req.raw.body) req.end();
    else Readable.fromWeb(c.req.raw.body as import('node:stream/web').ReadableStream).pipe(req);
  });
}

// Literal registrations, not a table: api-route-parity.test.ts reads
// `app.<method>('/api/...')` from source.
app.get('/api/version/check', forward);
app.get('/api/update/status', forward);
app.post('/api/update/stage', forward);
app.post('/api/update/upload', forward);
app.post('/api/update/apply', forward);

export default app;
