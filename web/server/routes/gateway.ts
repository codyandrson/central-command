/**
 * Gateway API Routes
 *
 * GET  /api/gateway/models       — Returns the models Central Command's LiteLLM proxy serves.
 *
 * Response (models):       { models: Array<{ id: string; label: string; provider: string; configured: true; role: string; thinkingLevels: string[] }>, error: string | null, source: 'litellm' }
 */

import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

export const MODEL_LIST_TIMEOUT_MS = 15_000;

export interface GatewayModelInfo {
  id: string;
  label: string;
  provider: string;
  alias?: string;
  configured: true;
  role: 'primary' | 'fallback' | 'allowed';
  /** Thinking/reasoning-effort levels this model accepts. Empty = no thinking control. */
  thinkingLevels: string[];
}


// ─── Model catalog via Central Command → LiteLLM ───────────────────────────────────

const NO_CONFIGURED_MODELS_ERROR = 'LiteLLM served no models.';

/**
 * The catalog is Central Command's, not OpenClaw's: FastAPI asks the LiteLLM proxy
 * which models it actually serves, and reports the session/agent default. There
 * is no openclaw.json on this deployment — reading one only ever produced the
 * "Could not read OpenClaw config" badge.
 */
interface CcModelEntry {
  id: string;
  thinking_levels?: string[];
}

interface CcModelsResponse {
  models?: CcModelEntry[];
  default?: string | null;
  error?: string | null;
}

async function getModelCatalog(): Promise<{ models: GatewayModelInfo[]; error: string | null }> {
  const gvBase = config.gatewayUrl.replace(/\/+$/, '');

  let data: CcModelsResponse;
  try {
    const res = await fetch(`${gvBase}/api/models`, {
      signal: AbortSignal.timeout(MODEL_LIST_TIMEOUT_MS),
    });
    if (!res.ok) return { models: [], error: `Central Command HTTP ${res.status}` };
    data = await res.json() as CcModelsResponse;
  } catch (err) {
    console.warn('[gateway/models] Central Command catalog fetch failed:', (err as Error).message);
    return { models: [], error: `Could not reach Central Command: ${(err as Error).message}` };
  }

  if (data.error) return { models: [], error: data.error };

  // The agent/global default is what the dropdown shows as "primary"
  // (inherited); a per-session override rides sessions.patch, not this catalog.
  const primary = data.default || null;
  // The id IS the label: a LiteLLM alias such as `kilo-auto/free` is one
  // name, not a provider prefix to strip — it rendered as "free".
  const models: GatewayModelInfo[] = (data.models || []).map(({ id, thinking_levels }) => {
    return {
      id,
      label: id,
      provider: 'litellm',
      configured: true as const,
      role: id === primary ? 'primary' : 'allowed',
      thinkingLevels: Array.isArray(thinking_levels) ? thinking_levels : [],
    };
  });

  if (models.length === 0) return { models: [], error: NO_CONFIGURED_MODELS_ERROR };
  return { models, error: null };
}

app.get('/api/gateway/models', rateLimitGeneral, async (c) => {
  const { models, error } = await getModelCatalog();
  return c.json({ models, error, source: 'litellm' });
});

export default app;
