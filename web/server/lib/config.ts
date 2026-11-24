/**
 * Server configuration — all env vars, paths, and constants.
 *
 * Single source of truth for every tuneable value in the Nerve server.
 * Validated at startup via {@link validateConfig}. Also exports the
 * startup banner printer and a non-blocking gateway health probe.
 * @module
 */

import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import {
  DEFAULT_GATEWAY_URL,
  DEFAULT_PORT,
  DEFAULT_SSL_PORT,
  DEFAULT_HOST,
  DEFAULT_LANGUAGE,
  SUPPORTED_LANGUAGES,
} from './constants.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');

const HOME = process.env.HOME || os.homedir();
const SUPPORTED_LANGUAGE_CODES = new Set(SUPPORTED_LANGUAGES.map((l) => l.code));

const LANGUAGE_ENV_VALUE = process.env.NERVE_LANGUAGE ?? process.env.LANGUAGE;

function normalizeLanguagePreference(language: string | undefined): string {
  const normalized = (language || DEFAULT_LANGUAGE).trim().toLowerCase();
  if (!normalized || normalized === 'auto') return DEFAULT_LANGUAGE;

  const code = normalized.split('-')[0] || DEFAULT_LANGUAGE;
  if (!SUPPORTED_LANGUAGE_CODES.has(code)) return DEFAULT_LANGUAGE;

  return code;
}

export const config = {
  port: Number(process.env.PORT || DEFAULT_PORT),
  sslPort: Number(process.env.SSL_PORT || DEFAULT_SSL_PORT),

  // Bind address — defaults to localhost for safety; set HOST=0.0.0.0 for remote access
  host: process.env.HOST || DEFAULT_HOST,

  // Key for the OpenAI-shaped speech endpoint (OPENAI_BASE_URL) — read-aloud
  // and voice input; deploy setup mints a proxy key scoped to cc-tts + cc-stt.
  openaiApiKey: process.env.OPENAI_API_KEY || '',

  // Language preference (ISO 639-1). Invalid/auto values are normalized to English.
  // Primary env key: NERVE_LANGUAGE. Legacy LANGUAGE is still accepted as fallback.
  language: normalizeLanguagePreference(LANGUAGE_ENV_VALUE),

  // Gateway connection
  gatewayUrl: process.env.GATEWAY_URL || DEFAULT_GATEWAY_URL,
  // 'api' = the gateway owns /api/update/* + /api/version/check (single-node
  // profile); anything else = this server does (k3s helper units).
  updateBackend: process.env.CC_UPDATE_BACKEND || '',
  gatewayToken: process.env.GATEWAY_TOKEN || process.env.OPENCLAW_GATEWAY_TOKEN || '',

  // Agent identity (used in UI)
  agentName: process.env.AGENT_NAME || 'Agent',

  home: HOME,

  // Paths (configurable via env, with OpenClaw defaults)
  dist: path.join(PROJECT_ROOT, 'dist'),
  fileBrowserRoot: process.env.FILE_BROWSER_ROOT || '',
  // The file browser's mutation routes answer 403 unless this is set
  // (server/routes/cc-file-browser-guard.ts; R18 / COCKPIT-028).
  fileBrowserWritable: process.env.FILE_BROWSER_WRITABLE === '1',
  memoryPath: process.env.MEMORY_PATH || path.join(HOME, '.openclaw', 'workspace', 'MEMORY.md'),
  memoryDir: process.env.MEMORY_DIR || path.join(HOME, '.openclaw', 'workspace', 'memory'),
  certPath: path.join(PROJECT_ROOT, 'certs', 'cert.pem'),
  keyPath: path.join(PROJECT_ROOT, 'certs', 'key.pem'),
  bunPath: path.join(HOME, '.bun', 'bin', 'bunx'),
  // Limits
  limits: {
    tts: 64 * 1024,                  // 64 KB
    transcribe: 12 * 1024 * 1024,    // 12 MB
    /** Global max request body size (transcribe + 1 MB overhead) */
    maxBodyBytes: 12 * 1024 * 1024 + 1024 * 1024,  // ~13 MB
  },

  // TTS cache
  ttsCacheTtlMs: Number(process.env.TTS_CACHE_TTL_MS || 3_600_000), // 1 hour
  ttsCacheMax: Number(process.env.TTS_CACHE_MAX || 200),

  // Authentication
  auth: (process.env.NERVE_AUTH || 'false').toLowerCase() === 'true',
  passwordHash: process.env.NERVE_PASSWORD_HASH || '',
  sessionSecret: process.env.NERVE_SESSION_SECRET || '',
  sessionTtlMs: Number(process.env.NERVE_SESSION_TTL || 30 * 24 * 60 * 60 * 1000), // 30 days
} as const;

// ─── Typed config mutation ──────────────────────────────────────────────────

/** Keys that may be mutated at runtime and their accepted types. */
type MutableConfigMap = {
  language: string;
  sessionSecret: string;
};

/**
 * Centralized, type-safe config mutation.
 *
 * Only the two known mutable keys are accepted; values are validated at
 * runtime before the write so callers can never silently corrupt config.
 * The single `Record<string, unknown>` cast is confined to this function.
 */
export function updateConfig<K extends keyof MutableConfigMap>(
  key: K,
  value: MutableConfigMap[K],
): void {
  switch (key) {
    case 'language':
      if (typeof value !== 'string' || !SUPPORTED_LANGUAGE_CODES.has(value)) {
        throw new Error(`Invalid language: ${value}. Must be a supported ISO 639-1 code`);
      }
      break;
    case 'sessionSecret':
      if (typeof value !== 'string' || value.length === 0) {
        throw new Error('sessionSecret must be a non-empty string');
      }
      break;
    default: {
      // Exhaustiveness check — if we ever add a key to MutableConfigMap
      // without handling it, TypeScript will flag this line.
      const _exhaustive: never = key;
      throw new Error(`Cannot mutate config key: ${_exhaustive}`);
    }
  }
  (config as Record<string, unknown>)[key] = value;
}

/** Session cookie name — suffixed with port to avoid collisions when running multiple instances. */
export const SESSION_COOKIE_NAME = `nerve_session_${config.port}`;

/** Resolve the speech label for the startup banner (TTS and STT share the one endpoint). */
function speechLabel(): string {
  if (!config.openaiApiKey) return 'off (no OPENAI_API_KEY)';
  const tts = (process.env.OPENAI_TTS_MODEL || '').trim();
  return tts ? `deployment speech service (${tts})` : 'OpenAI-shaped endpoint';
}

/** Print startup banner with version and config summary. */
export function printStartupBanner(version: string): void {
  console.log(`\n  \x1b[33m⚡ Nerve v${version}\x1b[0m`);
  console.log(`  Agent: ${config.agentName} | Speech: ${speechLabel()}`);
  console.log(`  Gateway: ${config.gatewayUrl}`);
  if (config.auth) {
    console.log('  \x1b[32m🔒 Authentication enabled\x1b[0m');
  }
}

/** Non-blocking gateway health check at startup. */
export async function probeGateway(): Promise<void> {
  try {
    const resp = await fetch(`${config.gatewayUrl}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (resp.ok) {
      console.log('  \x1b[32m✓\x1b[0m Gateway reachable');
    } else {
      console.warn(`  \x1b[33m⚠\x1b[0m Gateway returned HTTP ${resp.status}`);
    }
  } catch {
    console.warn('  \x1b[33m⚠\x1b[0m Gateway unreachable — is it running?');
  }
}

/** Log startup warnings and validate critical configuration. */
/** Top-level entries of `root` that the default file-browser exclusions exist to hide: .env*, .git, certs. */
export function secretEntriesIn(root: string): string[] {
  try {
    return fs.readdirSync(root).filter((n) => n === '.git' || n === 'certs' || /^\.env(\.|$)/.test(n));
  } catch {
    return [];
  }
}

export function validateConfig(): void {
  // Critical: gateway token is the only required config
  if (!config.gatewayToken) {
    console.warn(
      '\n  \x1b[33m⚠ GATEWAY_TOKEN is not set\x1b[0m\n' +
      '  Gateway API calls (memories, models, session info) will fail.\n' +
      '  Set GATEWAY_TOKEN in .env\n',
    );
  }

  // ── Auth validation ──────────────────────────────────────────────
  if (config.auth && !config.passwordHash && !config.gatewayToken) {
    console.error(
      '\n  \x1b[31m✗ NERVE_AUTH is enabled but no password or gateway token is configured.\x1b[0m\n' +
      '  Set NERVE_PASSWORD_HASH in .env, or set GATEWAY_TOKEN as a fallback.\n',
    );
  }

  if (config.auth && !config.sessionSecret) {
    // Auto-generate session secret if missing
    const secret = crypto.randomBytes(32).toString('hex');
    console.warn('[config] ⚠ NERVE_SESSION_SECRET not set — generated ephemeral secret (sessions won\'t survive restarts)');
    updateConfig('sessionSecret', secret);
  }

  if (config.host === '0.0.0.0' && !config.auth) {
    if (process.env.NERVE_ALLOW_INSECURE === 'true') {
      console.warn(
        '\n  \x1b[33m⚠ INSECURE MODE: Server binds to 0.0.0.0 with authentication DISABLED.\x1b[0m\n' +
        '  All API endpoints are accessible from the network without a password.\n' +
        '  This is dangerous. Set \x1b[36mNERVE_AUTH=true\x1b[0m in .env to enable authentication.\n',
      );
    } else {
      console.error(
        '\n  \x1b[31m✗ Refusing to start: HOST=0.0.0.0 with authentication disabled.\x1b[0m\n' +
        '  This would expose all API endpoints (files, memory, API keys) to the network.\n\n' +
        '  To fix, either:\n' +
        '    1. Enable auth:  \x1b[36mNERVE_AUTH=true\x1b[0m in .env\n' +
        '    2. Bind locally: \x1b[36mHOST=127.0.0.1\x1b[0m in .env\n' +
        '    3. Override:     \x1b[36mNERVE_ALLOW_INSECURE=true\x1b[0m in .env (NOT recommended)\n',
      );
      process.exit(1);
    }
  }

  // Informational warnings
  if (!config.openaiApiKey) {
    console.warn('[config] ⚠ OPENAI_API_KEY not set — read-aloud and voice input unavailable');
  }
  if (!process.env.NERVE_LANGUAGE && process.env.LANGUAGE) {
    console.warn('[config] ⚠ LANGUAGE is deprecated — use NERVE_LANGUAGE instead');
  }
  if (config.host === '0.0.0.0' && config.auth) {
    console.warn('[config] ⚠ Server binds to 0.0.0.0 — API is accessible from the network (auth enabled).');
  } else if (config.host !== '0.0.0.0' && config.host !== '127.0.0.1' && config.host !== 'localhost' && config.host !== '::1') {
    console.warn(
      '[config] ⚠ Server binds to ' + config.host + ' — API may be accessible from the network.\n' +
      '         Set HOST=127.0.0.1 for local-only access.',
    );
  }

  // ── FILE_BROWSER_ROOT validation ───────────────────────────────────────
  if (config.fileBrowserRoot) {
    try {
      const stat = fs.statSync(config.fileBrowserRoot);
      if (!stat.isDirectory()) {
        throw new Error('FILE_BROWSER_ROOT is not a directory');
      }
    } catch {
      console.warn('[config] ⚠ FILE_BROWSER_ROOT is not a directory or is inaccessible, falling back to default');
      (config as typeof config & { fileBrowserRoot: string }).fileBrowserRoot = '';
    }
  }
  // A custom root empties the secret exclusions (lib/file-utils.ts), so a root
  // that holds secrets would serve them. Refuse it (R18 / COCKPIT-028).
  if (config.fileBrowserRoot) {
    const secrets = secretEntriesIn(config.fileBrowserRoot);
    if (secrets.length > 0) {
      console.error(
        `[config] ✖ FILE_BROWSER_ROOT contains ${secrets.join(', ')} — a custom root disables the ` +
        'secret exclusions, so the root is refused and the default workspace is used',
      );
      (config as typeof config & { fileBrowserRoot: string }).fileBrowserRoot = '';
    }
  }
}
