/**
 * Deployment speech mode (OPENAI_TTS_MODEL set): what getDeploymentSpeech()
 * resolves from the environment and the stored tts-config.json. Its own file
 * because vitest caches the node:fs mock per file, and tts-config.test.ts
 * registers a different one.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

describe('getDeploymentSpeech (deployment speech mode)', () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  // The node:fs mock is created once and cached by vitest, so it reads this
  // shared object instead of closing over one test's options.
  const disk: { stored?: Record<string, unknown> } = {};

  /** Load the module with a given environment and a tts-config.json already on disk. */
  async function loadWithEnv(opts: {
    env: Record<string, string>;
    stored?: Record<string, unknown>;
  }) {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.stubEnv('OPENAI_TTS_MODEL', '');
    vi.stubEnv('OPENAI_TTS_VOICE', '');
    vi.stubEnv('OPENAI_STT_MODEL', '');
    for (const [k, v] of Object.entries(opts.env)) vi.stubEnv(k, v);
    disk.stored = opts.stored;

    vi.doMock('node:fs', () => ({
      default: {
        existsSync: () => disk.stored !== undefined,
        readFileSync: () => JSON.stringify(disk.stored ?? {}),
        writeFileSync: () => {},
      },
    }));
    return import('./tts-config.js');
  }

  // An install whose tts-config.json predates the speech service.
  const STALE = { openai: { model: 'gpt-4o-mini-tts', voice: 'nova' } };

  it('is null when OPENAI_TTS_MODEL is not set', async () => {
    const mod = await loadWithEnv({ env: {}, stored: STALE });
    expect(mod.getDeploymentSpeech()).toBeNull();
  });

  it('treats a blank OPENAI_TTS_MODEL as not set', async () => {
    const mod = await loadWithEnv({ env: { OPENAI_TTS_MODEL: '   ' }, stored: STALE });
    expect(mod.getDeploymentSpeech()).toBeNull();
  });

  it('uses the env model even though a stale model is stored, and defaults the voice to af_heart', async () => {
    const mod = await loadWithEnv({ env: { OPENAI_TTS_MODEL: 'cc-tts' }, stored: STALE });
    expect(mod.getTTSConfig().openai.model).toBe('gpt-4o-mini-tts');
    expect(mod.getDeploymentSpeech()).toEqual({ model: 'cc-tts', modelSource: 'env', voice: 'af_heart', voiceSource: 'default' });
  });

  it('never falls back to the stored OpenAI voice name', async () => {
    const mod = await loadWithEnv({ env: { OPENAI_TTS_MODEL: 'cc-tts' }, stored: STALE });
    expect(mod.getDeploymentSpeech()?.voice).not.toBe('nova');
  });

  it('prefers the voice saved for the deployment over the default', async () => {
    const mod = await loadWithEnv({
      env: { OPENAI_TTS_MODEL: 'cc-tts' },
      stored: { openai: { ...STALE.openai, deploymentVoice: 'bf_emma' } },
    });
    expect(mod.getDeploymentSpeech()).toEqual({ model: 'cc-tts', modelSource: 'env', voice: 'bf_emma', voiceSource: 'saved' });
  });

  it('prefers OPENAI_TTS_VOICE over a saved voice', async () => {
    const mod = await loadWithEnv({
      env: { OPENAI_TTS_MODEL: 'cc-tts', OPENAI_TTS_VOICE: 'am_adam' },
      stored: { openai: { ...STALE.openai, deploymentVoice: 'bf_emma' } },
    });
    expect(mod.getDeploymentSpeech()).toEqual({ model: 'cc-tts', modelSource: 'env', voice: 'am_adam', voiceSource: 'env' });
  });

  it('ignores a saved voice that is not a valid token', async () => {
    const mod = await loadWithEnv({
      env: { OPENAI_TTS_MODEL: 'cc-tts' },
      stored: { openai: { deploymentVoice: 'not a voice!' } },
    });
    expect(mod.getDeploymentSpeech()?.voiceSource).toBe('default');
  });

  it('keeps the old openai.voice when a deployment voice is saved', async () => {
    const mod = await loadWithEnv({ env: { OPENAI_TTS_MODEL: 'cc-tts' }, stored: STALE });
    const updated = mod.updateTTSConfig({ openai: { deploymentVoice: 'bf_emma' } });
    expect(updated.openai.voice).toBe('nova');
    expect(updated.openai.deploymentVoice).toBe('bf_emma');
  });

  // ── the speech model pickers (Settings > Audio, routes/cc-speech-models.ts) ──

  it('uses the read-aloud model the operator chose over the env alias', async () => {
    const mod = await loadWithEnv({
      env: { OPENAI_TTS_MODEL: 'cc-tts' },
      stored: { openai: { ...STALE.openai, deploymentModel: 'kokoro-alt' } },
    });
    expect(mod.getDeploymentSpeech()).toMatchObject({ model: 'kokoro-alt', modelSource: 'saved' });
  });

  it('never applies a chosen model outside deployment speech mode', async () => {
    const mod = await loadWithEnv({ env: {}, stored: { openai: { deploymentModel: 'kokoro-alt' } } });
    expect(mod.getDeploymentSpeech()).toBeNull();
  });

  it('the dictation model is the choice, else the env alias, else null outside the mode', async () => {
    let mod = await loadWithEnv({ env: { OPENAI_STT_MODEL: 'cc-stt' }, stored: STALE });
    expect(mod.getDeploymentSttModel()).toBe('cc-stt');
    mod = await loadWithEnv({
      env: { OPENAI_STT_MODEL: 'cc-stt' },
      stored: { ...STALE, transcribe: { deploymentModel: 'whisper-large' } },
    });
    expect(mod.getDeploymentSttModel()).toBe('whisper-large');
    mod = await loadWithEnv({ env: { OPENAI_STT_MODEL: '' }, stored: { transcribe: { deploymentModel: 'whisper-large' } } });
    expect(mod.getDeploymentSttModel()).toBeNull();
  });
});
