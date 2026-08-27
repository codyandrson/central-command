/**
 * cc-graph — cockpit Graph panel (rung 3, D2026-08-09 graph-inspection spec).
 *
 * Thin proxy over the Central Command API's read-only graph endpoints
 * (`integrations/neo4j_reader.py`, canned parameterized bolt queries — no raw
 * Cypher). Same shape as cc-crons: fetch the gateway URL, forward query
 * params straight through, wrap in the `{ok, result}` envelope the cockpit's
 * fetch hooks expect.
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

const fail = (c: Context, error: unknown, status: 404 | 422 | 502 = 502) =>
  c.json({ ok: false, error: error instanceof Error ? error.message : String(error) }, status);

/** Proxy a GET, forwarding every query param verbatim. */
async function proxyGet(c: Context, backendPath: string) {
  try {
    const qs = c.req.query();
    const search = new URLSearchParams(qs).toString();
    const res = await cc(`${backendPath}${search ? `?${search}` : ''}`);
    if (!res.ok) throw new Error(await gvError(res));
    const result = await res.json();
    return c.json({ ok: true, result });
  } catch (err) {
    return fail(c, err);
  }
}

app.get('/api/graph/search', rateLimitGeneral, (c) => proxyGet(c, '/graph/search'));
app.get('/api/graph/all', rateLimitGeneral, (c) => proxyGet(c, '/graph/all'));
app.get('/api/graph/neighborhood', rateLimitGeneral, (c) => proxyGet(c, '/graph/neighborhood'));
app.get('/api/graph/provenance', rateLimitGeneral, (c) => proxyGet(c, '/graph/provenance'));
app.get('/api/graph/status', rateLimitGeneral, (c) => proxyGet(c, '/graph/status'));

/** Proxy a body-carrying write, forwarding the JSON verbatim. */
async function proxyBody(c: Context, method: 'POST' | 'PATCH', backendPath: string) {
  try {
    const body = await c.req.json();
    const res = await cc(backendPath, { method, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
}

/** Proxy a DELETE, whose target rides in the query string. */
async function proxyDelete(c: Context, backendPath: string) {
  try {
    const search = new URLSearchParams(c.req.query()).toString();
    const res = await cc(`${backendPath}?${search}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
}

// Graph verification worklist (2026-08-19 spec, Task 6). Same thin-proxy
// shape as the rest of this file; the two POSTs carry an id in the path,
// which the generic proxyGet/proxyBody helpers above don't parameterize.
app.get('/api/graph/verifications', rateLimitGeneral, (c) => proxyGet(c, '/graph/verifications'));

app.post('/api/graph/verifications/:id/confirm', rateLimitGeneral, async (c) => {
  try {
    const res = await cc(`/graph/verifications/${c.req.param('id')}/confirm`, { method: 'POST' });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
});

app.post('/api/graph/verifications/:id/problem', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json();
    const res = await cc(`/graph/verifications/${c.req.param('id')}/problem`, {
      method: 'POST', body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
});

// Operator curation (2026-08-15). These are WRITES with no approval gate, and
// that is the design: the operator is the author, not an agent proposing. The
// backend keeps the query set closed — this proxy adds no Cypher of its own.
app.get('/api/graph/entity-types', rateLimitGeneral, (c) => proxyGet(c, '/graph/entity-types'));
app.post('/api/graph/node', rateLimitGeneral, (c) => proxyBody(c, 'POST', '/graph/node'));
app.patch('/api/graph/node', rateLimitGeneral, (c) => proxyBody(c, 'PATCH', '/graph/node'));
app.delete('/api/graph/node', rateLimitGeneral, (c) => proxyDelete(c, '/graph/node'));
app.post('/api/graph/edge', rateLimitGeneral, (c) => proxyBody(c, 'POST', '/graph/edge'));
app.patch('/api/graph/edge', rateLimitGeneral, (c) => proxyBody(c, 'PATCH', '/graph/edge'));
app.delete('/api/graph/edge', rateLimitGeneral, (c) => proxyDelete(c, '/graph/edge'));
app.post('/api/graph/merge', rateLimitGeneral, (c) => proxyBody(c, 'POST', '/graph/merge'));

app.put('/api/graph/settings', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json();
    const res = await cc('/graph/settings', { method: 'PUT', body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await gvError(res));
    const result = await res.json();
    return c.json({ ok: true, result });
  } catch (err) {
    return fail(c, err);
  }
});

export default app;
