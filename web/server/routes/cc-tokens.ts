/**
 * cc-tokens — the cockpit usage panel, showing LiteLLM's numbers.
 *
 * LiteLLM is Central Command's system of record for usage tracking and spend
 * limits (the operator, 2026-07-22) — this adapter only relays its figures via
 * Central Command's /api/usage. It replaces Nerve's transcript-scanning
 * tokens route; nothing is re-derived or persisted here. Coverage note:
 * these are the proxy's numbers, i.e. traffic routed through LiteLLM.
 */
import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

interface CcUsage {
  spend: number;
  max_budget: number | null;
  window_days: number;
  window_spend?: number;
  daily?: Array<{ date: string; spend: number; input: number; output: number; requests: number }>;
  proxy_ui_url?: string | null;
  models: Array<{ model: string; cost: number; input: number; output: number; requests: number }>;
}

app.get('/api/tokens', rateLimitGeneral, async (c) => {
  try {
    const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/usage`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { detail?: string };
      return c.json({ error: body.detail || `Central Command HTTP ${res.status}` }, 502);
    }
    const usage = await res.json() as CcUsage;
    const totalInput = usage.models.reduce((s, m) => s + m.input, 0);
    const totalOutput = usage.models.reduce((s, m) => s + m.output, 0);
    const totalMessages = usage.models.reduce((s, m) => s + m.requests, 0);
    return c.json({
      entries: usage.models.map((m) => ({
        source: m.model,
        cost: m.cost,
        inputTokens: m.input,
        outputTokens: m.output,
        messageCount: m.requests,
      })),
      totalCost: usage.spend,
      totalInput,
      totalOutput,
      totalMessages,
      windowCost: usage.window_spend,
      windowDays: usage.window_days,
      daily: usage.daily?.map((d) => ({
        date: d.date,
        cost: d.spend,
        input: d.input,
        output: d.output,
        requests: d.requests,
      })),
      proxyUiUrl: usage.proxy_ui_url ?? null,
      updatedAt: Date.now(),
    });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

export default app;
