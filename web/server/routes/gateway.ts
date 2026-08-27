/**
 * Gateway API Routes
 *
 * GET  /api/gateway/models       — Returns the models Central Command's LiteLLM proxy serves.
 * POST /api/gateway/restart      — Restart the OpenClaw gateway service via `openclaw gateway restart`.
 *
 * Response (models):       { models: Array<{ id: string; label: string; provider: string; configured: true; role: string; thinkingLevels: string[] }>, error: string | null, source: 'litellm' }
 * Response (restart):      { ok: boolean; output: string }
 */

import { Hono } from 'hono';
import { execFile } from 'node:child_process';
import { Socket } from 'node:net';
import { homedir } from 'node:os';
import { rateLimitGeneral, rateLimitRestart } from '../middleware/rate-limit.js';
import { resolveOpenclawBin } from '../lib/openclaw-bin.js';
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

const openclawBin = resolveOpenclawBin();

/** Directory containing the node binary — needed in PATH for `#!/usr/bin/env node` shims. */
const nodeBinDir = process.execPath.replace(/\/node$/, '');

const NO_CONFIGURED_MODELS_ERROR = 'LiteLLM served no models.';

/**
 * Infer the HOME directory for openclaw execution.
 * When server runs as root but openclaw is installed under a user account
 * (e.g., /home/username/.nvm/...), we need to use that user's HOME so openclaw
 * can find its config at ~/.openclaw/openclaw.json.
 *
 * Extracts home from paths like:
 *   /home/username/.nvm/... → /home/username
 *   /Users/username/.nvm/... → /Users/username
 *
 * Falls back to process.env.HOME if extraction fails.
 */
function inferOpenclawHome(): string {
  const match = openclawBin.match(/^(\/home\/[^/]+|\/Users\/[^/]+)/);
  if (match) return match[1];

  return process.env.HOME || homedir();
}

const openclawHome = inferOpenclawHome();

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
  const models: GatewayModelInfo[] = (data.models || []).map(({ id, thinking_levels }) => {
    const [provider, ...rest] = id.split('/');
    return {
      id,
      label: rest.length ? rest.join('/') : id,
      provider: rest.length ? provider : 'litellm',
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

// ── POST /api/gateway/restart ───────────────────────────────────────

const GATEWAY_RESTART_TIMEOUT_MS = 15_000;

app.post('/api/gateway/restart', rateLimitRestart, async (c) => {
  // DBus session vars are required for `systemctl --user` commands.
  // When Nerve runs as a system service these may be absent; provide fallbacks.
  const uid = process.getuid?.() ?? 1000;
  const xdgRuntime = process.env.XDG_RUNTIME_DIR || `/run/user/${uid}`;

  const execEnv = {
    ...process.env,
    HOME: openclawHome,
    PATH: `${nodeBinDir}:${process.env.PATH || '/usr/bin:/bin'}`,
    XDG_RUNTIME_DIR: xdgRuntime,
    DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || `unix:path=${xdgRuntime}/bus`,
  };

  // Step 1: restart the gateway
  const restartResult = await new Promise<{ ok: boolean; output: string }>((resolve) => {
    execFile(openclawBin, ['gateway', 'restart'], {
      timeout: GATEWAY_RESTART_TIMEOUT_MS,
      maxBuffer: 512 * 1024,
      env: execEnv,
    }, (err, stdout, stderr) => {
      const output = (stdout + stderr).trim();
      if (err) {
        resolve({ ok: false, output: output || err.message });
      } else {
        // Treat zero exit code as success; actual health is verified in step 2.
        resolve({ ok: true, output });
      }
    });
  });

  if (!restartResult.ok) {
    return c.json(restartResult, 500);
  }

  // Step 2: verify gateway is actually running AND listening (not just systemd reporting)
  // Wait 2s after restart command, then retry up to 8 times with 1s delay (max ~10s total)
  await new Promise(resolve => setTimeout(resolve, 2000));
  
  let statusResult: { ok: boolean; output: string } | null = null;
  for (let attempt = 0; attempt < 8; attempt++) {
    if (attempt > 0) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    
    // First check if systemd reports it as running
    statusResult = await new Promise<{ ok: boolean; output: string }>((resolve) => {
      execFile(openclawBin, ['gateway', 'status'], {
        timeout: 5000,
        maxBuffer: 512 * 1024,
        env: execEnv,
      }, (err, stdout, stderr) => {
        const output = (stdout + stderr).trim();
        if (err) {
          resolve({ ok: false, output: output || err.message });
        } else {
          // Check for positive running state AND absence of failure indicators
          const running = output.includes('Runtime: running');
          const activating = output.includes('state activating');
          const failed = output.includes('last exit 1') && !running;
          // activating is a normal transitional state -- keep retrying
          const ok = running && !failed;
          if (activating && !running) { resolve({ ok: false, output }); return; }
          resolve({ ok, output });
        }
      });
    });
    
    if (!statusResult.ok) continue;
    
    // If systemd reports running, verify the port is actually listening
    const portTest = await new Promise<boolean>((resolve) => {
      const socket = new Socket();
      
      const gwUrl = new URL(config.gatewayUrl);
      const gwPort = parseInt(gwUrl.port, 10) || 18789;
      socket.setTimeout(2000);
      socket.connect(gwPort, gwUrl.hostname, () => {
        socket.end();
        resolve(true);
      });
      
      socket.on('error', () => {
        socket.destroy();
        resolve(false);
      });
      socket.on('timeout', () => {
        socket.destroy();
        resolve(false);
      });
    });
    
    if (portTest) break;
    
    // Port not ready yet, continue retrying
    statusResult.ok = false;
    statusResult.output += '\nGateway running but port not ready yet';
  }

  if (!statusResult || !statusResult.ok) {
    return c.json({
      ok: false,
      output: `Gateway restarted but not running. Status:\n${statusResult?.output || 'Status check failed'}`,
    }, 500);
  }

  return c.json({ ok: true, output: 'Gateway restarted successfully' });
});

export default app;
