/** Tests for the TTS voice config file: defaults, merge, and the removed providers' sections. */
import { describe, it, expect, vi } from 'vitest';

async function loadTtsModule(stored?: Record<string, unknown>) {
  vi.resetModules();
  const written: string[] = [];

  vi.doMock('node:fs', () => ({
    default: {
      existsSync: () => stored !== undefined,
      readFileSync: () => JSON.stringify(stored ?? {}),
      writeFileSync: (_path: string, data: string) => { written.push(data); },
    },
  }));

  const mod = await import('./tts-config.js');
  return { mod, written };
}

describe('getTTSConfig', () => {
  it('returns only the openai and transcribe sections, with defaults, when the config file is missing', async () => {
    const { mod, written } = await loadTtsModule();

    const cfg = mod.getTTSConfig();
    expect(Object.keys(cfg)).toEqual(['openai', 'transcribe']);
    expect(cfg.openai.deploymentVoice).toBe('');
    expect(typeof cfg.openai.voice).toBe('string');
    expect(written).toHaveLength(1);
  });

  it('drops the qwen, edge and xiaomi sections an earlier release wrote', async () => {
    const { mod } = await loadTtsModule({
      openai: { voice: 'onyx' },
      qwen: { mode: 'voice_design' },
      edge: { voice: 'en-US-AriaNeural' },
      xiaomi: { model: 'mimo-v2-tts' },
    });

    const cfg = mod.getTTSConfig();
    expect(Object.keys(cfg)).toEqual(['openai', 'transcribe']);
    expect(cfg.openai.voice).toBe('onyx');
    expect(cfg.openai.deploymentVoice).toBe('');
  });

  it('deep-merges openai patches without dropping defaults', async () => {
    const { mod } = await loadTtsModule();

    const cfg = mod.updateTTSConfig({ openai: { instructions: 'Calm.' } });
    expect(cfg.openai.instructions).toBe('Calm.');
    expect(typeof cfg.openai.model).toBe('string');
    expect(cfg.openai.deploymentVoice).toBe('');
  });
});
