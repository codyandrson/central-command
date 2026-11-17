/**
 * A browser's saved `oc-tts-model` (written before the deployment's speech
 * service existed) must not be sent as the model once the server reports
 * deployment speech mode: the deployment owns the model.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsProvider, useSettings } from './SettingsContext';

class FakeAudio {
  addEventListener() {}
  pause() {}
  play() { return Promise.resolve(); }
}

let ttsConfig: Record<string, unknown>;
let ttsBodies: Array<Record<string, unknown>>;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('oc-tts-provider', 'openai');
  localStorage.setItem('oc-tts-model', 'gpt-4o-mini-tts');
  ttsBodies = [];
  vi.stubGlobal('Audio', FakeAudio);
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: { body?: string }) => {
    if (url === '/api/tts') {
      ttsBodies.push(JSON.parse(init?.body ?? '{}'));
      return new Response(new Uint8Array([1]), { status: 200, headers: { 'Content-Type': 'audio/mpeg' } });
    }
    if (url === '/api/tts/config') return new Response(JSON.stringify(ttsConfig), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

afterEach(() => { vi.unstubAllGlobals(); });

async function speakAfterConfigLoaded() {
  const { result } = renderHook(() => useSettings(), { wrapper: SettingsProvider });
  // The config read lands in an effect; wait until it has been requested and applied.
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/tts/config'));
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  await act(async () => { await result.current.speakNow('Hello'); });
  expect(ttsBodies).toHaveLength(1);
  return ttsBodies[0];
}

describe('the TTS model a request carries', () => {
  it('omits the saved model in deployment speech mode', async () => {
    ttsConfig = { defaultProvider: 'openai', deploymentSpeech: { model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' } };
    const body = await speakAfterConfigLoaded();
    expect(body).toEqual({ text: 'Hello', provider: 'openai' });
  });

  it('still sends the saved model outside that mode', async () => {
    ttsConfig = { defaultProvider: 'openai', deploymentSpeech: null };
    const body = await speakAfterConfigLoaded();
    expect(body.model).toBe('gpt-4o-mini-tts');
  });
});
