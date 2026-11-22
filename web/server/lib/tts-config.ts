/**
 * TTS voice configuration — reads/writes a JSON config file.
 *
 * The voice settings of the one speech provider (the OpenAI-shaped endpoint,
 * which deploy setup points at the deployment's own speech service) live here
 * instead of env vars or hardcoded values. On first run, default settings
 * are written to `<PROJECT_ROOT>/tts-config.json`. Subsequent reads merge
 * the on-disk config with defaults so new fields are always present; sections
 * of providers that no longer exist (qwen, edge, xiaomi) are dropped on read.
 * @module
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const CONFIG_PATH = path.join(PROJECT_ROOT, 'tts-config.json');

export interface TTSVoiceConfig {
  /** OpenAI TTS settings */
  openai: {
    /** OpenAI TTS model (gpt-4o-mini-tts, tts-1, tts-1-hd) */
    model: string;
    /** Voice name (alloy, ash, ballad, cedar, coral, echo, fable, marin, nova, onyx, sage, shimmer, verse) */
    voice: string;
    /** Natural language instructions for how the voice should sound */
    instructions: string;
    /**
     * Voice the operator saved FOR the deployment's own speech service (see
     * {@link getDeploymentSpeech}). Kept apart from `voice`, which holds an
     * OpenAI voice name that the deployment's engine does not know; '' = unset.
     */
    deploymentVoice: string;
  };
}

const DEFAULTS: TTSVoiceConfig = {
  openai: {
    // Env seeds the FIRST-RUN defaults only; tts-config.json wins once written.
    model: process.env.OPENAI_TTS_MODEL || 'gpt-4o-mini-tts',
    voice: process.env.OPENAI_TTS_VOICE || 'nova',
    instructions:
      'Speak naturally and conversationally, like a real person. Warm, friendly tone with a slight British accent. Keep it casual and relaxed, not robotic or overly formal.',
    deploymentVoice: '',
  },
};

let cached: TTSVoiceConfig | null = null;

/** Load TTS config from disk, merging with defaults for any missing fields. */
export function getTTSConfig(): TTSVoiceConfig {
  if (cached) return cached;

  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
      cached = { openai: deepMerge(DEFAULTS.openai, raw?.openai ?? {}) };
      return cached;
    }
  } catch (err) {
    console.warn('[tts-config] Failed to read config, using defaults:', (err as Error).message);
  }

  // First run — write defaults to disk
  cached = { openai: { ...DEFAULTS.openai } };
  saveTTSConfig(cached);
  return cached;
}

/** Save TTS config to disk and update cache. */
export function saveTTSConfig(cfg: TTSVoiceConfig): void {
  cached = cfg;
  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2) + '\n', 'utf-8');
  } catch (err) {
    console.error('[tts-config] Failed to write config:', (err as Error).message);
  }
}

/** Update a partial config (deep merge) and save. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function updateTTSConfig(patch: Record<string, any>): TTSVoiceConfig {
  const current = getTTSConfig();
  const updated = deepMerge(current, patch) as TTSVoiceConfig;
  saveTTSConfig(updated);
  return updated;
}

// ─── Deployment speech mode ──────────────────────────────────────────────────

/**
 * The voice used for the deployment's speech service when neither the
 * environment nor the operator names one. It is the first voice of the bundled
 * engine (Kokoro) and the same default as the API's own speech route
 * (`tts_voice` in central_command/config.py, `CC_TTS_VOICE` in deploy/).
 */
export const DEFAULT_DEPLOYMENT_VOICE = 'af_heart';

/** A deployment voice is a short token: letters, digits, underscore, hyphen. */
export const DEPLOYMENT_VOICE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export interface DeploymentSpeech {
  /** The model alias the deployment serves; always the environment's value. */
  model: string;
  /** The voice that will be sent when a request names none. */
  voice: string;
  /** Where `voice` came from. */
  voiceSource: 'env' | 'saved' | 'default';
}

/** The operator-set voice in the environment, or '' (read at call time). */
export function deploymentVoiceFromEnv(): string {
  return (process.env.OPENAI_TTS_VOICE || '').trim();
}

/**
 * DEPLOYMENT SPEECH MODE — the one definition: OPENAI_TTS_MODEL is set.
 *
 * Setup points the 'openai' provider at the deployment's own speech service
 * (through the LiteLLM proxy) and writes that alias here. In this mode the
 * deployment owns the model: a stale `openai.model` in tts-config.json (written
 * before the service existed) and a `model` sent by a browser never override
 * it. The OpenAI voice name in `openai.voice` is never sent either — the
 * engine behind the alias answers 500 to a name it does not provide.
 * Outside the mode this returns null and the provider behaves as before.
 */
export function getDeploymentSpeech(): DeploymentSpeech | null {
  const model = (process.env.OPENAI_TTS_MODEL || '').trim();
  if (!model) return null;

  const envVoice = deploymentVoiceFromEnv();
  if (envVoice) return { model, voice: envVoice, voiceSource: 'env' };

  const saved = (getTTSConfig().openai.deploymentVoice || '').trim();
  if (DEPLOYMENT_VOICE_PATTERN.test(saved)) return { model, voice: saved, voiceSource: 'saved' };

  return { model, voice: DEFAULT_DEPLOYMENT_VOICE, voiceSource: 'default' };
}

/** Simple deep merge (target ← source). Only merges plain objects, overwrites everything else. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function deepMerge(target: any, source: any): any {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    const sv = source[key];
    const tv = target[key];
    if (
      sv !== undefined &&
      typeof sv === 'object' &&
      sv !== null &&
      !Array.isArray(sv) &&
      typeof tv === 'object' &&
      tv !== null &&
      !Array.isArray(tv)
    ) {
      result[key] = deepMerge(tv, sv);
    } else if (sv !== undefined) {
      result[key] = sv;
    }
  }
  return result;
}
