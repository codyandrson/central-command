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

export default app;
