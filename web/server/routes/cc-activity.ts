/**
 * cc-activity — the Activity screen's backend (2026-08-11 activity-coverage spec).
 *
 * Thin proxy over endpoints the Central Command API already served and nothing
 * consumed. That is the whole character of this file: the audit that produced
 * the spec found `/dispatch` returning a complete dashboard payload, `/events`
 * documenting `order=desc` + `before` as "the audit-trail browser paging
 * backwards through history", and `/ledger?state=FAILED` carrying `failure_ack`
 * for a Failures strip that was never built. None of it had a caller.
 *
 * Same shape as cc-graph: forward query params verbatim, wrap in the
 * `{ok, result}` envelope the cockpit's fetch hooks expect. No shape mapping —
 * these are our own endpoints, not a vendored Nerve contract to satisfy.
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

const fail = (c: Context, error: unknown, status: 409 | 502 = 502) =>
  c.json({ ok: false, error: error instanceof Error ? error.message : String(error) }, status);

/** Proxy a GET, forwarding every query param verbatim. */
async function proxyGet(c: Context, backendPath: string) {
  try {
    const search = new URLSearchParams(c.req.query()).toString();
    const res = await cc(`${backendPath}${search ? `?${search}` : ''}`);
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
}

/** Proxy a POST with no body. Both work-item levers are pure verbs on an id.
 *  A 409 is the backend saying the row is not FAILED (or already acked) —
 *  relayed as itself, because "already handled" is a real answer the operator
 *  needs to see, not a transport failure. */
async function proxyPost(c: Context, backendPath: string) {
  try {
    const res = await cc(backendPath, { method: 'POST' });
    if (!res.ok) {
      const message = await gvError(res);
      return fail(c, new Error(message), res.status === 409 ? 409 : 502);
    }
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return fail(c, err);
  }
}

// Overview — one payload, composed server-side so the operator can never read
// a dispatch number from one instant beside a ledger number from another.
app.get('/api/activity/overview', rateLimitGeneral, (c) => proxyGet(c, '/activity/overview'));

// Runs — faceted sessions with the (before, before_id) keyset cursor.
app.get('/api/activity/runs', rateLimitGeneral, (c) => proxyGet(c, '/sessions'));

// Events — the durable log. `order=desc` + `before` is the backwards page.
app.get('/api/activity/events', rateLimitGeneral, (c) => proxyGet(c, '/events'));
app.get('/api/activity/event-kinds', rateLimitGeneral, (c) => proxyGet(c, '/events/kinds'));

// The Failures strip's two levers. Requeue re-parses the stored raw email;
// acknowledge leaves the row FAILED (the record is the truth) and stops it
// asking for action.
app.post('/api/activity/work/:id/requeue', rateLimitGeneral, (c) =>
  proxyPost(c, `/work/${encodeURIComponent(c.req.param('id'))}/requeue`));
app.post('/api/activity/work/:id/ack', rateLimitGeneral, (c) =>
  proxyPost(c, `/work/${encodeURIComponent(c.req.param('id'))}/ack_failure`));

export default app;
