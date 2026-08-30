/**
 * cc-sources — verbatim proxies for the Sources panel (document catalog +
 * the email feed's visibility row). These FastAPI routes are REST, not RPC:
 * the sources/catalog surface shipped as `/api/sources…` in slice 1, and
 * without a route here the panel would 404 exactly as the Skills import
 * dialogs did (see cc-skills.ts).
 *
 * Nothing is reshaped — the panel speaks the FastAPI contract directly,
 * including `{detail}` error bodies, so status and body pass through
 * untouched. The 409 on enabling the email feed and the 404 on an unknown
 * source are meant to reach the operator verbatim.
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

const gvBase = () => config.gatewayUrl.replace(/\/+$/, '');

async function forward(c: Context, path: string, method: 'GET' | 'POST'): Promise<Response> {
  const res = await fetch(`${gvBase()}/api${path}`, {
    method,
    headers: { 'content-type': c.req.header('content-type') ?? 'application/json' },
    body: method === 'GET' ? undefined : await c.req.arrayBuffer(),
  });
  return new Response(await res.arrayBuffer(), {
    status: res.status,
    headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
  });
}

app.get('/api/sources', rateLimitGeneral, (c) => forward(c, '/sources', 'GET'));
app.post('/api/sources', rateLimitGeneral, (c) => forward(c, '/sources', 'POST'));
app.post('/api/sources/:id/walk', rateLimitGeneral, (c) =>
  forward(c, `/sources/${encodeURIComponent(c.req.param('id'))}/walk`, 'POST'));
app.post('/api/sources/:id/enable', rateLimitGeneral, (c) =>
  forward(c, `/sources/${encodeURIComponent(c.req.param('id'))}/enable`, 'POST'));

app.get('/api/catalog/documents', rateLimitGeneral, (c) => {
  const sourceId = c.req.query('source_id');
  const qs = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : '';
  return forward(c, `/catalog/documents${qs}`, 'GET');
});
app.post('/api/catalog/documents/:id/rescind', rateLimitGeneral, (c) =>
  forward(c, `/catalog/documents/${encodeURIComponent(c.req.param('id'))}/rescind`, 'POST'));
app.post('/api/catalog/documents/:id/reinstate', rateLimitGeneral, (c) =>
  forward(c, `/catalog/documents/${encodeURIComponent(c.req.param('id'))}/reinstate`, 'POST'));

export default app;
