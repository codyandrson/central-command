/**
 * GET/PUT /api/speech/models — the speech model pickers in Settings > Audio.
 *
 * The LISTS come from Central Command, which reads LiteLLM's `/v1/model/info`
 * and keeps the rows whose declared `mode` is `audio_speech` (read-aloud) or
 * `audio_transcription` (dictation): `GET {gatewayUrl}/api/speech/models`,
 * central_command/api/speech.py, the same way `/api/gateway/models` reads the
 * chat catalog from `/api/models`. The CHOICE is this server's, because this
 * server makes the speech calls on the k3s profile: it is saved in
 * tts-config.json (`openai.deploymentModel`, `transcribe.deploymentModel`) and
 * read by getDeploymentSpeech() / getDeploymentSttModel(). On the single-node
 * profile there is no Node server and the API answers both routes itself.
 *
 * Response (both profiles): {
 *   tts: { selected, default, models: string[] } | null,
 *   stt: { selected, default, models: string[] } | null,
 *   error: string | null
 * }
 * A side is null outside deployment speech mode (its OPENAI_*_MODEL unset), where
 * the plain OpenAI provider's controls apply. `error` set with empty lists means
 * the list could not be read; `selected` still says what is in use.
 *
 * PUT body: { tts?: string | null, stt?: string | null }. A model must be in the
 * list for its mode (422; 502 when the list cannot be read). The env alias, '' or
 * null go back to the default and need no list.
 */

import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';
import {
  getDeploymentSpeech,
  getDeploymentSttModel,
  updateTTSConfig,
  SPEECH_MODEL_PATTERN,
} from '../lib/tts-config.js';

const app = new Hono();

export const SPEECH_LIST_TIMEOUT_MS = 15_000;

export interface SpeechSide {
  selected: string;
  default: string;
  models: string[];
}

export interface SpeechModelsResponse {
  tts: SpeechSide | null;
  stt: SpeechSide | null;
  error: string | null;
}

interface SpeechLists {
  tts: string[];
  stt: string[];
  error: string | null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/** The two lists, from Central Command. Never throws: a failure is the `error`. */
async function fetchSpeechLists(): Promise<SpeechLists> {
  const base = config.gatewayUrl.replace(/\/+$/, '');
  try {
    const res = await fetch(`${base}/api/speech/models`, {
      signal: AbortSignal.timeout(SPEECH_LIST_TIMEOUT_MS),
    });
    if (!res.ok) return { tts: [], stt: [], error: `Central Command HTTP ${res.status}` };
    const data = await res.json() as {
      tts?: { models?: unknown } | null;
      stt?: { models?: unknown } | null;
      error?: string | null;
    };
    if (data.error) return { tts: [], stt: [], error: data.error };
    return { tts: stringList(data.tts?.models), stt: stringList(data.stt?.models), error: null };
  } catch (err) {
    console.warn('[speech/models] Central Command list fetch failed:', (err as Error).message);
    return { tts: [], stt: [], error: `Could not reach Central Command: ${(err as Error).message}` };
  }
}

function envAlias(name: 'OPENAI_TTS_MODEL' | 'OPENAI_STT_MODEL'): string {
  return (process.env[name] || '').trim();
}

function body(lists: SpeechLists): SpeechModelsResponse {
  const tts = getDeploymentSpeech();
  const stt = getDeploymentSttModel();
  return {
    tts: tts ? { selected: tts.model, default: envAlias('OPENAI_TTS_MODEL'), models: lists.tts } : null,
    stt: stt ? { selected: stt, default: envAlias('OPENAI_STT_MODEL'), models: lists.stt } : null,
    error: lists.error,
  };
}

app.get('/api/speech/models', rateLimitGeneral, async (c) => {
  return c.json(body(await fetchSpeechLists()));
});

app.put('/api/speech/models', rateLimitGeneral, async (c) => {
  let patch: Record<string, unknown>;
  try {
    patch = await c.req.json();
  } catch {
    return c.text('Body must be JSON', 400);
  }
  if (typeof patch !== 'object' || patch === null || Array.isArray(patch)) {
    return c.text('Body must be a JSON object', 400);
  }
  const keys = Object.keys(patch);
  if (keys.length === 0 || keys.some((k) => k !== 'tts' && k !== 'stt')) {
    return c.text('Name tts and/or stt, and nothing else', 422);
  }

  const sides = [
    { key: 'tts', env: 'OPENAI_TTS_MODEL', section: 'openai', mode: 'audio_speech' },
    { key: 'stt', env: 'OPENAI_STT_MODEL', section: 'transcribe', mode: 'audio_transcription' },
  ] as const;

  // Check every named side before saving any, so a refused half saves nothing.
  const updates: Array<{ section: 'openai' | 'transcribe'; value: string }> = [];
  let lists: SpeechLists | null = null;
  for (const side of sides) {
    if (!(side.key in patch)) continue;
    const raw = patch[side.key];
    if (raw !== null && typeof raw !== 'string') return c.text(`"${side.key}" must be a string or null`, 422);
    const alias = envAlias(side.env);
    if (!alias) {
      return c.text(`${side.env} is not set, so this deployment has no ${side.mode} model to choose`, 409);
    }
    const value = (raw ?? '').trim();
    if (!value || value === alias) {
      updates.push({ section: side.section, value: '' });
      continue;
    }
    if (!SPEECH_MODEL_PATTERN.test(value)) return c.text(`"${side.key}" is not a model id`, 422);
    lists ??= await fetchSpeechLists();
    if (lists.error) return c.text(`Cannot check the model right now: ${lists.error}`, 502);
    const listed = side.key === 'tts' ? lists.tts : lists.stt;
    if (!listed.includes(value)) {
      return c.text(
        `"${value}" is not a ${side.mode} model LiteLLM serves; choose one of: ${listed.join(', ') || '(none)'}`,
        422,
      );
    }
    updates.push({ section: side.section, value });
  }
  for (const u of updates) updateTTSConfig({ [u.section]: { deploymentModel: u.value } });
  return c.json(body(lists ?? await fetchSpeechLists()));
});

export default app;
