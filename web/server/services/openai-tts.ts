/**
 * OpenAI TTS provider.
 *
 * Supports multiple models: gpt-4o-mini-tts (default), tts-1, tts-1-hd.
 * Voice and model settings are read from `tts-config.json` via {@link getTTSConfig}.
 * @module
 */

import { config } from '../lib/config.js';
import { getTTSConfig, getDeploymentSpeech } from '../lib/tts-config.js';
import { OPENAI_TTS_URL } from '../lib/constants.js';
import { FifoGateAborted, ttsGate } from '../lib/fifo-gate.js';

export interface OpenAITTSResult {
  ok: true;
  buf: Buffer;
}

export interface OpenAITTSError {
  ok: false;
  status: number;
  message: string;
}

/**
 * Generate speech via OpenAI TTS API.
 * Returns the audio buffer on success, or an error object.
 *
 * In deployment speech mode the call goes to the deployment's own proxy alias
 * (`cc-tts`, one job at a time, and LiteLLM 1.110 REJECTS a second concurrent
 * request with a 429), so it takes its place in a FIFO line first. `signal`
 * aborting while it still waits leaves the line (status 499, nothing sent). A
 * real OpenAI endpoint has no such limit and is not gated.
 */
export async function synthesizeOpenAI(
  text: string,
  voice?: string,
  model?: string,
  signal?: AbortSignal,
): Promise<OpenAITTSResult | OpenAITTSError> {
  if (!config.openaiApiKey) {
    return { ok: false, status: 500, message: 'OpenAI API key not configured' };
  }

  const ttsConfig = getTTSConfig().openai;
  // Deployment speech mode (OPENAI_TTS_MODEL set): the deployment owns the model
  // and, unless the caller names one, the voice — see getDeploymentSpeech().
  const deployment = getDeploymentSpeech();
  const effectiveModel = deployment ? deployment.model : model || ttsConfig.model;
  const effectiveVoice = deployment ? voice || deployment.voice : voice || ttsConfig.voice;

  const send = async (): Promise<OpenAITTSResult | OpenAITTSError> => {
    const resp = await fetch(OPENAI_TTS_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.openaiApiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: effectiveModel,
        voice: effectiveVoice,
        input: text,
        response_format: 'mp3',
        // instructions only supported by gpt-4o-mini-tts (never sent to the deployment's service)
        ...(!deployment && effectiveModel === 'gpt-4o-mini-tts' && {
          instructions: ttsConfig.instructions,
        }),
      }),
    });

    if (!resp.ok) {
      const errBody = await resp.text();
      console.error('[tts:openai] API error:', resp.status, errBody);
      return { ok: false, status: resp.status, message: errBody };
    }

    const buf = Buffer.from(await resp.arrayBuffer());
    return { ok: true, buf };
  };

  if (!deployment) return send();
  try {
    return await ttsGate.run(send, signal);
  } catch (err) {
    if (err instanceof FifoGateAborted) return { ok: false, status: 499, message: err.message };
    throw err;
  }
}
