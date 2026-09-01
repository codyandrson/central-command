/**
 * cc-systems — the cockpit's Systems launchpad. Thin proxy over Central
 * Command's GET /api/systems, mapping snake_case wire fields to camelCase
 * the same way cc-tokens.ts maps LiteLLM's usage payload. No credential
 * VALUE ever passes through here — the gateway only ever sends a label +
 * location for where a credential lives.
 */
import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

interface CcSystem {
  id: string;
  name: string;
  kind: 'ui' | 'api' | 'store' | 'external';
  url: string | null;
  link_label?: string;
  status: 'up' | 'down' | 'unknown';
  latency_ms: number | null;
  credential: { label: string; location: string };
}

app.get('/api/systems', rateLimitGeneral, async (c) => {
  try {
    const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/systems`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { detail?: string };
      return c.json({ error: body.detail || `Central Command HTTP ${res.status}` }, 502);
    }
    const data = await res.json() as { systems: CcSystem[] };
    return c.json({
      systems: data.systems.map((s) => ({
        id: s.id,
        name: s.name,
        kind: s.kind,
        url: s.url,
        linkLabel: s.link_label ?? null,
        status: s.status,
        latencyMs: s.latency_ms,
        credential: { label: s.credential.label, location: s.credential.location },
      })),
      updatedAt: Date.now(),
    });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

// Same-origin passthrough for the API's own Swagger page (the Systems page
// links to /api/docs). The gateway serves both under its /api prefix; only
// these two GETs are proxied — nothing else of the API port is exposed.
app.get('/api/docs', rateLimitGeneral, async (c) => {
  const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/docs`);
  return c.html(await res.text(), res.status as 200);
});
app.get('/api/openapi.json', rateLimitGeneral, async (c) => {
  const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/openapi.json`);
  return c.json(await res.json(), res.status as 200);
});

export default app;
