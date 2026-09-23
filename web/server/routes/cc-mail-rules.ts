/**
 * cc-mail-rules — standing inbox rules ("auto-dismiss mail matching these
 * criteria"). Agents PROPOSE a rule (capability `mail.create_rule`, routed
 * through the normal Decisions Inbox gate) or the operator creates one
 * directly here; matching mail folds at claim time without an agent run.
 * Thin proxy over the API's `/mail/rules` endpoints, same envelope/helper
 * shape as cc-charter.ts — the payload passes through unchanged.
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

async function gvError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { detail?: string };
    return body.detail || `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

app.get('/api/cc/mail-rules', rateLimitGeneral, async (c) => {
  try {
    const includeRevoked = c.req.query('include_revoked') === 'true';
    const res = await cc(`/mail/rules?include_revoked=${includeRevoked}`);
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

app.post('/api/cc/mail-rules/preview', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json();
    const res = await cc('/mail/rules/preview', { method: 'POST', body: JSON.stringify(body) });
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 422 ? 422 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

app.post('/api/cc/mail-rules', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json();
    const res = await cc('/mail/rules', { method: 'POST', body: JSON.stringify(body) });
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 422 ? 422 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

app.post('/api/cc/mail-rules/:id/revoke', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json().catch(() => ({}));
    const res = await cc(`/mail/rules/${encodeURIComponent(c.req.param('id'))}/revoke`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (!res.ok) return c.json({ ok: false, error: await gvError(res) }, res.status === 404 ? 404 : 502);
    return c.json({ ok: true, result: await res.json() });
  } catch (err) {
    return c.json({ ok: false, error: err instanceof Error ? err.message : String(err) }, 502);
  }
});

export default app;
