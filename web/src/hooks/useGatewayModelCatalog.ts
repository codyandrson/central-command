/**
 * useGatewayModelCatalog — fetches the gateway's model catalog (GET
 * /api/gateway/models) once per mount. Shared by the chat header's
 * model/effort selectors, the agent model dialog, and the cron dialog so all
 * three consumers read the same shape instead of each rolling its own fetch.
 */
import { useEffect, useState } from 'react';

export interface GatewayModelInfo {
  id: string;
  label: string;
  provider: string;
  role?: 'primary' | 'fallback' | 'allowed';
  /** Thinking/reasoning-effort levels this model accepts, e.g.
   *  ["off","low","medium","xhigh"]. Absent on older payload shapes;
   *  an explicit empty array means the model has no thinking control. */
  thinkingLevels?: string[];
}

interface GatewayModelsResponse {
  models: GatewayModelInfo[];
  error: string | null;
}

async function fetchGatewayModels(): Promise<GatewayModelsResponse> {
  try {
    const res = await fetch('/api/gateway/models');
    if (!res.ok) return { models: [], error: `Gateway HTTP ${res.status}` };
    const data = await res.json() as { models?: GatewayModelInfo[]; error?: string | null };
    return {
      models: Array.isArray(data.models) ? data.models : [],
      error: typeof data.error === 'string' ? data.error : null,
    };
  } catch {
    return { models: [], error: 'Could not load configured models' };
  }
}

export function useGatewayModelCatalog(): { models: GatewayModelInfo[] | null; error: string | null } {
  const [models, setModels] = useState<GatewayModelInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchGatewayModels().then((result) => {
      if (cancelled) return;
      setModels(result.models);
      setError(result.error);
    });
    return () => { cancelled = true; };
  }, []);

  return { models, error };
}
