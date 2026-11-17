/**
 * synthesizeOpenAI: what is sent to the speech endpoint, in and out of
 * deployment speech mode (OPENAI_TTS_MODEL set).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// An install whose tts-config.json was written before the speech service existed.
const STALE_STORED = { openai: { model: 'gpt-4o-mini-tts', voice: 'nova', instructions: 'Speak warmly.' } };

// The node:fs mock is created once and cached by vitest, so it reads this
// shared object instead of closing over one call's arguments.
const disk: { stored: Record<string, unknown> } = { stored: STALE_STORED };

async function load(env: Record<string, string>, stored: Record<string, unknown> = STALE_STORED) {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.stubEnv('OPENAI_TTS_MODEL', '');
  vi.stubEnv('OPENAI_TTS_VOICE', '');
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  disk.stored = stored;

  vi.doMock('../lib/config.js', () => ({
    config: { openaiApiKey: 'sk-test', language: 'en', edgeVoiceGender: 'female' },
  }));
  vi.doMock('../lib/language.js', () => ({
    getEdgeTtsVoice: () => 'en-US-AriaNeural',
    getQwen3Language: () => null,
    getFallbackInfo: () => ({ supported: true }),
    resolveLanguage: () => undefined,
  }));
  vi.doMock('node:fs', () => ({
    default: {
      existsSync: () => true,
      readFileSync: () => JSON.stringify(disk.stored),
      writeFileSync: () => {},
    },
  }));
  return import('./openai-tts.js');
}

/** Run one synthesis and return the JSON body that reached fetch. */
async function sent(
  mod: Awaited<ReturnType<typeof load>>,
  voice?: string,
  model?: string,
): Promise<Record<string, unknown>> {
  const fetchMock = vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const result = await mod.synthesizeOpenAI('Hello', voice, model);
  expect(result.ok).toBe(true);
  const call = fetchMock.mock.calls[0] as unknown as [string, { body: string }];
  return JSON.parse(call[1].body);
}

describe('synthesizeOpenAI', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  describe('deployment speech mode', () => {
    it('sends the env model, not the stored one, and the default deployment voice, not the stored OpenAI voice', async () => {
      const body = await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts' }));
      expect(body.model).toBe('cc-tts');
      expect(body.voice).toBe('af_heart');
    });

    it('ignores a model sent by a browser (stale localStorage)', async () => {
      const body = await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts' }), undefined, 'gpt-4o-mini-tts');
      expect(body.model).toBe('cc-tts');
    });

    it('voice order: env, then the voice saved for the deployment, then the default', async () => {
      const saved = { openai: { ...STALE_STORED.openai, deploymentVoice: 'bf_emma' } };
      expect((await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts', OPENAI_TTS_VOICE: 'am_adam' }, saved))).voice).toBe('am_adam');
      expect((await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts' }, saved))).voice).toBe('bf_emma');
      expect((await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts' }))).voice).toBe('af_heart');
    });

    it('lets a voice named in the request win', async () => {
      const body = await sent(await load({ OPENAI_TTS_MODEL: 'cc-tts', OPENAI_TTS_VOICE: 'am_adam' }), 'bm_george');
      expect(body.voice).toBe('bm_george');
    });

    it('never sends instructions, even if the alias is named gpt-4o-mini-tts', async () => {
      const body = await sent(await load({ OPENAI_TTS_MODEL: 'gpt-4o-mini-tts' }));
      expect(body.model).toBe('gpt-4o-mini-tts');
      expect(body).not.toHaveProperty('instructions');
    });
  });

  describe('outside deployment speech mode (no OPENAI_TTS_MODEL)', () => {
    it('is unchanged: stored model and voice, a request model overrides, instructions for gpt-4o-mini-tts', async () => {
      const mod = await load({});
      const plain = await sent(mod);
      expect(plain.model).toBe('gpt-4o-mini-tts');
      expect(plain.voice).toBe('nova');
      expect(plain.instructions).toBe('Speak warmly.');

      const withModel = await sent(mod, 'alloy', 'tts-1');
      expect(withModel.model).toBe('tts-1');
      expect(withModel.voice).toBe('alloy');
      expect(withModel).not.toHaveProperty('instructions');
    });
  });
});
