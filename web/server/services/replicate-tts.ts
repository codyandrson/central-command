/**
 * Replicate TTS — generic provider for Replicate-hosted TTS models.
 *
 * Calls the Replicate predictions API directly — no Python, no temp files.
 * Uses `Prefer: wait` to get synchronous results when possible, with polling
 * fallback for cold starts. WAV output is piped through ffmpeg for MP3 conversion.
 *
 * Supported models:
 *  - qwen-tts (default): Qwen3-TTS, returns WAV → converted to MP3 via ffmpeg
 *
 * To add a new Replicate model, add an entry to `REPLICATE_MODELS` below.
 * @module
 */

import { config } from '../lib/config.js';
import { getTTSConfig, resolveQwen3Language } from '../lib/tts-config.js';
import { REPLICATE_QWEN_TTS_URL } from '../lib/constants.js';

export interface ReplicateTTSResult {
  ok: true;
  buf: Buffer;
  contentType: string;
}

export interface ReplicateTTSError {
  ok: false;
  status: number;
  message: string;
}

/** Registry of supported Replicate TTS models. */
interface ReplicateModelDef {
  url: string;
  buildInput: (text: string, voice?: string) => Record<string, string>;
}

/**
 * Build Qwen TTS input from tts-config.json settings.
 * Supports voice_design (description-based) and custom_voice (preset speaker) modes.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function buildQwenInput(text: string, voice?: string): Record<string, string> {
  const qwen = getTTSConfig().qwen;
  // Use language-aware resolution: config language → qwen3 mapping → fallback to English
  const resolved = resolveQwen3Language();
  if (resolved.warning) {
    console.warn(`[tts:replicate] ${resolved.warning}`);
  }
  const input: Record<string, string> = {
    text,
    language: resolved.voice, // resolved.voice is the Qwen3 language string (e.g. 'German', 'English')
    mode: qwen.mode,
  };

  if (qwen.mode === 'voice_design') {
    if (qwen.voiceDescription) input.voice_description = qwen.voiceDescription;
    if (qwen.styleInstruction) input.style_instruction = qwen.styleInstruction;
  } else if (qwen.mode === 'custom_voice') {
    input.speaker = qwen.speaker;
    if (qwen.styleInstruction) input.style_instruction = qwen.styleInstruction;
  }

  return input;
}

const REPLICATE_MODELS: Record<string, ReplicateModelDef> = {
  'qwen-tts': {
    url: REPLICATE_QWEN_TTS_URL,
    buildInput: buildQwenInput,
  },
};

const DEFAULT_MODEL = 'qwen-tts';
const POLL_INTERVAL_MS = 1_000;
const MAX_POLL_MS = 120_000;
/** Per-fetch timeout for polling / download requests. */
const FETCH_TIMEOUT_MS = 30_000;
/** Timeout for the initial create request with Prefer: wait (Replicate holds connection). */
const CREATE_TIMEOUT_MS = 90_000;

/**
 * Generate speech via a Replicate-hosted TTS model.
 * Returns the MP3 audio buffer on success, or an error object.
 */
export async function synthesizeReplicate(
  text: string,
  options?: { model?: string; voice?: string },
): Promise<ReplicateTTSResult | ReplicateTTSError> {
  if (!config.replicateApiToken) {
    return {
      ok: false,
      status: 500,
      message: 'No TTS provider configured (need REPLICATE_API_TOKEN)',
    };
  }

  const modelId = options?.model || DEFAULT_MODEL;
  const modelDef = REPLICATE_MODELS[modelId];
  if (!modelDef) {
    return {
      ok: false,
      status: 400,
      message: `Unknown Replicate TTS model: ${modelId}. Available: ${Object.keys(REPLICATE_MODELS).join(', ')}`,
    };
  }

  const authHeader = `Bearer ${config.replicateApiToken}`;
  const input = modelDef.buildInput(text, options?.voice);

  try {
    // Create prediction via model-specific endpoint (no version hash needed)
    // Use longer timeout for Prefer: wait — Replicate holds the connection until
    // the prediction completes (up to ~60s), and cold starts can be slow.
    const createResp = await fetchWithTimeout(modelDef.url, {
      method: 'POST',
      headers: {
        Authorization: authHeader,
        'Content-Type': 'application/json',
        Prefer: 'wait',
      },
      body: JSON.stringify({ input }),
    }, CREATE_TIMEOUT_MS);

    if (!createResp.ok) {
      const errBody = await createResp.text();
      console.error('[tts:replicate] Replicate create error:', createResp.status, errBody);
      return { ok: false, status: 502, message: 'Qwen TTS: upstream error' };
    }

    let prediction = (await createResp.json()) as ReplicatePrediction;

    // Poll until terminal state (Prefer: wait may resolve immediately)
    const deadline = Date.now() + MAX_POLL_MS;
    while (!isTerminal(prediction.status)) {
      if (Date.now() > deadline) {
        return { ok: false, status: 504, message: 'Qwen TTS timed out' };
      }
      await sleep(POLL_INTERVAL_MS);

      const pollUrl = prediction.urls?.get;
      if (!pollUrl) {
        return { ok: false, status: 502, message: 'Qwen TTS: no poll URL returned' };
      }

      const pollResp = await fetchWithTimeout(pollUrl, {
        headers: { Authorization: authHeader },
      });
      if (!pollResp.ok) {
        console.error('[tts:replicate] Replicate poll error:', pollResp.status, await pollResp.text());
        return { ok: false, status: 502, message: 'Qwen TTS: upstream poll error' };
      }
      prediction = (await pollResp.json()) as ReplicatePrediction;
    }

    if (prediction.status !== 'succeeded') {
      console.error('[tts:replicate] prediction failed/canceled:', prediction.error);
      return { ok: false, status: 502, message: 'Qwen TTS prediction failed' };
    }

    // Extract the output URL — could be string, array of strings, or unexpected
    const outputUrl = extractOutputUrl(prediction.output);
    if (!outputUrl) {
      console.error('[tts:replicate] unexpected output format:', JSON.stringify(prediction.output));
      return { ok: false, status: 502, message: 'Qwen TTS: unexpected output format' };
    }

    // Download the audio
    const audioResp = await fetchWithTimeout(outputUrl);
    if (!audioResp.ok) {
      console.error(`[tts:replicate:${modelId}] audio download failed:`, audioResp.status);
      return { ok: false, status: 502, message: 'Failed to download TTS audio' };
    }

    const rawBuf = Buffer.from(await audioResp.arrayBuffer());

    // Serve WAV directly — all modern browsers support it natively.
    // Eliminates ffmpeg dependency and ~200ms conversion overhead.
    return { ok: true, buf: rawBuf, contentType: 'audio/wav' };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('[tts:replicate] error:', msg);
    return { ok: false, status: 500, message: 'Qwen TTS error' };
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface ReplicatePrediction {
  id: string;
  status: string;
  output?: unknown;
  error?: string;
  urls?: { get?: string };
}

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled']);

function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Extract a URL string from Replicate's output field (string | string[] | FileOutput). */
function extractOutputUrl(output: unknown): string | null {
  if (typeof output === 'string' && output.startsWith('http')) return output;
  if (Array.isArray(output)) {
    const first = output[0];
    if (typeof first === 'string' && first.startsWith('http')) return first;
  }
  // FileOutput or object with .url
  if (output && typeof output === 'object' && 'url' in output) {
    const url = (output as { url: unknown }).url;
    if (typeof url === 'string') return url;
  }
  // Last-resort: stringify and check if it looks like a URL
  const str = String(output);
  if (str.startsWith('http')) return str;
  return null;
}

/** Fetch with an AbortController timeout. */
function fetchWithTimeout(url: string, init?: RequestInit, timeoutMs = FETCH_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...init, signal: controller.signal }).finally(() =>
    clearTimeout(timer),
  );
}

/** Convert a WAV buffer to MP3 via ffmpeg (piped, no temp files).
 *  Currently unused — WAV is served directly to the browser. */
// function wavToMp3(wav: Buffer): Promise<Buffer> { ... }

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
