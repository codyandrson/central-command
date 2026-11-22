/**
 * POST /api/transcribe — Audio transcription.
 *
 * One provider: the OpenAI-shaped transcription endpoint (`OPENAI_BASE_URL`),
 * which deploy setup points at the deployment's own speech service through the
 * LiteLLM proxy (`OPENAI_STT_MODEL=cc-stt`). Needs OPENAI_API_KEY.
 * Body: multipart/form-data with a "file" field containing audio data.
 * Response: { text: string }
 *
 * Also the voice LANGUAGE setting (GET/PUT /api/language), written to
 * NERVE_LANGUAGE in web/.env.
 */

import { Hono } from 'hono';
import { config, updateConfig } from '../lib/config.js';
import { OPENAI_STT_MODEL, SUPPORTED_LANGUAGES } from '../lib/constants.js';
import { writeEnvKey } from '../lib/env-file.js';
import { transcribe as transcribeOpenAI } from '../services/openai-whisper.js';
import { rateLimitTranscribe, rateLimitGeneral } from '../middleware/rate-limit.js';
import type { ContentfulStatusCode } from 'hono/utils/http-status';

const MAX_FILE_SIZE = config.limits.transcribe; // 12 MB

/** MIME types accepted for transcription */
const ALLOWED_AUDIO_TYPES = new Set([
  'audio/webm',
  'audio/mp3',
  'audio/mpeg',
  'audio/mp4',
  'audio/m4a',
  'audio/wav',
  'audio/x-wav',
  'audio/ogg',
  'audio/flac',
  'audio/x-flac',
]);

const app = new Hono();

app.post('/api/transcribe', rateLimitTranscribe, async (c) => {
  try {
    const body = await c.req.parseBody();
    const file = body['file'];

    if (!file || !(file instanceof File)) {
      return c.text('No file found in request', 400);
    }

    if (file.size > MAX_FILE_SIZE) {
      return c.text(`File too large (max ${MAX_FILE_SIZE / 1024 / 1024}MB)`, 413);
    }

    if (file.type && !ALLOWED_AUDIO_TYPES.has(file.type)) {
      return c.text(`Unsupported audio format: ${file.type}`, 415);
    }

    const arrayBuf = await file.arrayBuffer();
    const fileData = Buffer.from(arrayBuf);
    const filename = file.name || 'audio.webm';

    if (!config.openaiApiKey) {
      return c.text('OpenAI API key not configured. Set OPENAI_API_KEY in web/.env', 500);
    }
    // Pass the language hint
    const result = await transcribeOpenAI(fileData, filename, file.type || 'audio/webm', config.language, c.req.raw.signal);

    if (!result.ok) {
      return c.text(result.message, result.status as ContentfulStatusCode);
    }

    return c.json({ text: result.text });
  } catch (err) {
    console.error('[transcribe] error:', (err as Error).message || err);
    return c.text('Transcription failed', 500);
  }
});

/** GET /api/transcribe/config — the STT provider and model (the API's own route sends the same shape). */
app.get('/api/transcribe/config', (c) => {
  return c.json({
    provider: 'openai',
    model: OPENAI_STT_MODEL,
    language: config.language,
    modelReady: true,
  });
});

// ─── Language API ────────────────────────────────────────────────────────────

/** GET /api/language — current language + supported list */
app.get('/api/language', rateLimitGeneral, (c) => {
  return c.json({
    language: config.language,
    supported: SUPPORTED_LANGUAGES.map((l) => ({
      code: l.code,
      name: l.name,
      nativeName: l.nativeName,
    })),
  });
});

/** PUT /api/language — switch language at runtime (hot-reload) */
app.put('/api/language', rateLimitGeneral, async (c) => {
  try {
    const body = await c.req.json() as { language?: string };

    if (body.language !== undefined) {
      const lang = body.language;
      if (!SUPPORTED_LANGUAGES.find((l) => l.code === lang)) {
        return c.text(`Unsupported language: ${lang}. Available: ${SUPPORTED_LANGUAGES.map((l) => l.code).join(', ')}`, 400);
      }
      updateConfig('language', lang);
      await writeEnvKey('NERVE_LANGUAGE', lang);
    }

    return c.json({ language: config.language });
  } catch (err) {
    if (err instanceof SyntaxError) {
      return c.text('Invalid request', 400);
    }
    console.error('[language] update failed:', err);
    return c.text('Failed to update language settings', 500);
  }
});

export default app;
