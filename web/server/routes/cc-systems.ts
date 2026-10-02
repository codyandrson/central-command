/**
 * cc-systems — the cockpit's Systems launchpad. Thin proxy over Central
 * Command's GET /api/systems, mapping snake_case wire fields to camelCase
 * the same way cc-tokens.ts maps LiteLLM's usage payload. No credential
 * VALUE ever passes through here — the gateway only ever sends a label +
 * location for where a credential lives.
 *
 * Also proxies the application self-check shown beside the systems:
 * GET /api/selfcheck (the last result, from memory — spends nothing) and
 * POST /api/selfcheck/run (starts one background run; two of its checks spend
 * a model request each, so only the operator's button ever calls it).
 */
import { Hono, type Context } from 'hono';
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

interface CcSelfCheckCheck {
  name: string;
  status: 'pass' | 'warn' | 'fail' | 'skip';
  message: string;
  remedy: string | null;
  system: string | null;
  duration_ms: number | null;
}

interface CcSelfCheck {
  status: 'pass' | 'warn' | 'fail' | 'never_run';
  running: boolean;
  ran_at: string | null;
  duration_ms: number | null;
  version: string | null;
  mode: 'cli' | 'api' | null;
  checks: CcSelfCheckCheck[];
}

/** Same snake_case -> camelCase mapping as the systems list; `ranAt` stays the
 *  server's ISO instant — the browser renders it in its own local time. */
function mapSelfCheck(d: CcSelfCheck) {
  return {
    status: d.status,
    running: Boolean(d.running),
    ranAt: d.ran_at ?? null,
    durationMs: d.duration_ms ?? null,
    version: d.version ?? null,
    mode: d.mode ?? null,
    checks: (d.checks ?? []).map((k) => ({
      name: k.name,
      status: k.status,
      message: k.message,
      remedy: k.remedy ?? null,
      system: k.system ?? null,
      durationMs: k.duration_ms ?? null,
    })),
  };
}

async function relaySelfCheck(c: Context, path: string, init?: RequestInit, okStatus: 200 | 202 = 200) {
  try {
    const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}${path}`, init);
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { detail?: string };
      return c.json({ error: body.detail || `Central Command HTTP ${res.status}` }, 502);
    }
    return c.json(mapSelfCheck(await res.json() as CcSelfCheck), okStatus);
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
}

app.get('/api/selfcheck', rateLimitGeneral, (c) => relaySelfCheck(c, '/api/selfcheck'));
app.post('/api/selfcheck/run', rateLimitGeneral, (c) =>
  relaySelfCheck(c, '/api/selfcheck/run', { method: 'POST' }, 202));

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
