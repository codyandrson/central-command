/**
 * POST /api/tts — Text-to-speech synthesis.
 *
 * One provider: the OpenAI-shaped speech endpoint (`OPENAI_BASE_URL`), which
 * deploy setup points at the deployment's own speech service through the
 * LiteLLM proxy (`OPENAI_TTS_MODEL=cc-tts`, deployment speech mode — see
 * lib/tts-config.ts getDeploymentSpeech). Needs OPENAI_API_KEY.
 * Body: { text: string, voice?: string, model?: string, provider?: string }
 * (`provider` is accepted for wire compatibility and ignored, as in the API's
 * own speech route, central_command/api/speech.py).
 * Response: audio/mpeg binary
 */

import { Hono } from 'hono';
import { zValidator } from '@hono/zod-validator';
import { z } from 'zod';
import crypto from 'node:crypto';
import {
  getTTSConfig,
  updateTTSConfig,
  getDeploymentSpeech,
  deploymentVoiceFromEnv,
  DEPLOYMENT_VOICE_PATTERN,
} from '../lib/tts-config.js';
import { getTtsCache, setTtsCache } from '../services/tts-cache.js';
import { synthesizeOpenAI } from '../services/openai-tts.js';
import { rateLimitTTS, rateLimitGeneral } from '../middleware/rate-limit.js';
import type { ContentfulStatusCode } from 'hono/utils/http-status';

const app = new Hono();

const MAX_TEXT_LENGTH = 5000;

const ttsSchema = z.object({
  text: z
    .string()
    .min(1, 'Text is required')
    .max(MAX_TEXT_LENGTH, `Text too long (max ${MAX_TEXT_LENGTH} chars)`)
    .refine((s) => s.trim().length > 0, 'Text cannot be empty or whitespace'),
  voice: z.string().optional(),
  // Accepted for wire compatibility (a browser may still hold an old saved
  // provider); there is one provider and the value is not read.
  provider: z.string().optional(),
  model: z.string().optional(),
});

type SynthesisOutcome =
  | { ok: true; buf: Buffer; contentType: string }
  | { ok: false; status: number; message: string };

/**
 * A synthesis under way, by cache key (see the POST handler). `subscribers` is
 * the number of requests still waiting on it; `controller` aborts its place in
 * the speech alias's FIFO line when the last of them has gone (it has no effect
 * on a synthesis that is already running).
 */
interface InflightSynthesis {
  promise: Promise<SynthesisOutcome>;
  subscribers: number;
  controller: AbortController;
}
const inflightSyntheses = new Map<string, InflightSynthesis>();

function audioResponse(buf: Buffer, contentType = 'audio/mpeg'): Response {
  return new Response(buf, {
    status: 200,
    headers: { 'Content-Type': contentType },
  });
}

app.post(
  '/api/tts',
  rateLimitTTS,
  zValidator('json', ttsSchema, (result, c) => {
    if (!result.success) {
      return c.text(result.error.issues[0]?.message || 'Invalid request', 400);
    }
  }),
  async (c) => {
    try {
      const { text, voice, model } = c.req.valid('json');
      console.log(`[tts] voice=${voice} text="${text.slice(0, 50)}..."`);

      // In deployment speech mode the server decides the model (and the default
      // voice), so the cache key carries those, not a stale browser `model`.
      const deployment = getDeploymentSpeech();
      const keyModel = deployment ? deployment.model : model || '';
      const keyVoice = deployment ? voice || deployment.voice : voice || '';

      // Cache key: model + voice + text. The leading 'openai' and the empty
      // field keep the key shape of the multi-provider route it replaced.
      const hash = crypto
        .createHash('md5')
        .update(`openai:${keyModel}:${keyVoice}::${text}`)
        .digest('hex');

      const cached = getTtsCache(hash);
      if (cached) {
        // Detect WAV (starts with "RIFF") vs MP3 for correct content type
        const cachedCt = cached.length > 4 && cached.toString('ascii', 0, 4) === 'RIFF' ? 'audio/wav' : 'audio/mpeg';
        return audioResponse(cached, cachedCt);
      }

      // One synthesis per key at a time: a request whose key is already being
      // synthesised (another tab or device speaking the same text, a repeat
      // click) waits for that job and gets its bytes. The job is not tied to
      // any one request, so a client that disconnects cancels nothing for the
      // others. The entry goes when the job settles; a failure reaches every
      // waiter and is not cached, so the next request tries again. A job that is
      // still WAITING for its turn at the proxy (the speech alias runs one job at
      // a time, in order: lib/fifo-gate.ts) leaves the line when the last request
      // waiting on it disconnects; one already running finishes and is cached.
      let job = inflightSyntheses.get(hash);
      if (job) {
        console.log('[tts] joining an in-flight synthesis');
      } else {
        const controller = new AbortController();
        const promise = (async (): Promise<SynthesisOutcome> => {
          const result = await synthesizeOpenAI(text, voice, model, controller.signal);
          if (!result.ok) return { ok: false, status: result.status, message: result.message };
          setTtsCache(hash, result.buf);
          return { ok: true, buf: result.buf, contentType: 'audio/mpeg' };
        })().finally(() => inflightSyntheses.delete(hash));
        job = { promise, subscribers: 0, controller };
        inflightSyntheses.set(hash, job);
      }

      const waitingOn = job;
      waitingOn.subscribers += 1;
      let left = false;
      const leave = () => {
        if (left) return;
        left = true;
        waitingOn.subscribers -= 1;
        if (waitingOn.subscribers <= 0) waitingOn.controller.abort();
      };
      const requestSignal = c.req.raw.signal;
      requestSignal.addEventListener('abort', leave, { once: true });
      let outcome: SynthesisOutcome;
      try {
        outcome = await waitingOn.promise;
      } finally {
        left = true;
        requestSignal.removeEventListener('abort', leave);
      }
      if (!outcome.ok) {
        return c.text(outcome.message, outcome.status as ContentfulStatusCode);
      }
      return audioResponse(outcome.buf, outcome.contentType);
    } catch (err) {
      console.error('[tts] error:', (err as Error).message || err);
      return c.text('TTS failed', 500);
    }
  },
);

// ---------------------------------------------------------------------------
// TTS voice config API — read & update tts-config.json
// ---------------------------------------------------------------------------

/** GET /api/tts/config — return current TTS voice config */
app.get('/api/tts/config', rateLimitGeneral, (c) => {
  // deploymentSpeech: non-null when OPENAI_TTS_MODEL is set — the deployment owns
  // the model and the voice (see getDeploymentSpeech); the UI shows these, not
  // the stored OpenAI model/voice, which are not sent in that mode.
  // defaultProvider: the one provider, the same value the API's own route sends.
  return c.json({
    ...getTTSConfig(),
    defaultProvider: 'openai',
    deploymentSpeech: getDeploymentSpeech(),
  });
});

/** Allowed top-level keys and their allowed child keys (all must be strings) */
const TTS_CONFIG_SCHEMA: Record<string, string[]> = {
  openai: ['model', 'voice', 'instructions', 'deploymentVoice'],
};

/** Validate TTS config patch — only allow known keys with string values */
function validateTTSPatch(patch: unknown): string | null {
  if (typeof patch !== 'object' || patch === null || Array.isArray(patch)) {
    return 'Body must be a JSON object';
  }
  for (const [key, val] of Object.entries(patch as Record<string, unknown>)) {
    if (!(key in TTS_CONFIG_SCHEMA)) return `Unknown section: "${key}"`;
    if (typeof val !== 'object' || val === null || Array.isArray(val)) {
      return `"${key}" must be an object`;
    }
    const allowed = TTS_CONFIG_SCHEMA[key];
    for (const [subKey, subVal] of Object.entries(val as Record<string, unknown>)) {
      if (!allowed.includes(subKey)) return `Unknown key: "${key}.${subKey}"`;
      if (typeof subVal !== 'string') return `"${key}.${subKey}" must be a string`;
      if (subVal.length > 2000) return `"${key}.${subKey}" exceeds max length (2000)`;
      if (key === 'openai' && subKey === 'deploymentVoice' && !DEPLOYMENT_VOICE_PATTERN.test(subVal)) {
        return '"openai.deploymentVoice" must be 1-64 characters: letters, digits, underscore or hyphen';
      }
    }
  }
  return null;
}

/** PUT /api/tts/config — partial update TTS voice config */
app.put('/api/tts/config', rateLimitGeneral, async (c) => {
  try {
    const patch = await c.req.json();
    const err = validateTTSPatch(patch);
    if (err) return c.text(err, 400);
    // The environment's voice wins and is the operator's deployment setting:
    // a saved value would be silently ignored, so say so instead.
    if (patch.openai && 'deploymentVoice' in patch.openai && deploymentVoiceFromEnv()) {
      return c.text(
        'The deployment voice is set by OPENAI_TTS_VOICE in the environment and cannot be changed here',
        409,
      );
    }
    const updated = updateTTSConfig(patch);
    return c.json({ ...updated, deploymentSpeech: getDeploymentSpeech() });
  } catch (err) {
    console.error('[tts-config] update error:', (err as Error).message);
    return c.text('Invalid config', 400);
  }
});

export default app;
