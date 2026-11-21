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

  describe('the speech alias runs one job at a time, in order (LiteLLM 1.110 rejects a second with a 429)', () => {
    /** A fetch that records how many calls are in flight and in what order they arrived. */
    function slowFetch() {
      const state = { inside: 0, peak: 0, order: [] as string[] };
      const releases: Array<() => void> = [];
      const fn = vi.fn(async (_url: string, init: { body: string }) => {
        state.inside += 1;
        state.peak = Math.max(state.peak, state.inside);
        state.order.push(JSON.parse(init.body).input);
        await new Promise<void>((resolve) => releases.push(resolve));
        state.inside -= 1;
        return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
      });
      return { fn, state, releases };
    }
    const tick = () => new Promise((r) => setTimeout(r, 5));

    it('lets one call reach the proxy at a time, in arrival order', async () => {
      const mod = await load({ OPENAI_TTS_MODEL: 'cc-tts' });
      const f = slowFetch();
      vi.stubGlobal('fetch', f.fn);
      const calls = ['one', 'two', 'three'].map((t) => mod.synthesizeOpenAI(t));
      await tick();
      expect(f.state.order).toEqual(['one']);
      for (const expected of [['one', 'two'], ['one', 'two', 'three']]) {
        f.releases.shift()!();
        await tick();
        expect(f.state.order).toEqual(expected);
      }
      f.releases.shift()!();
      const results = await Promise.all(calls);
      expect(results.every((r) => r.ok)).toBe(true);
      expect(f.state.peak).toBe(1);
    });

    it('a call aborted while it waits sends nothing and does not hold the next one up', async () => {
      const mod = await load({ OPENAI_TTS_MODEL: 'cc-tts' });
      const f = slowFetch();
      vi.stubGlobal('fetch', f.fn);
      const first = mod.synthesizeOpenAI('first');
      const gone = new AbortController();
      const abandoned = mod.synthesizeOpenAI('abandoned', undefined, undefined, gone.signal);
      const last = mod.synthesizeOpenAI('last');
      await tick();
      gone.abort();
      expect(await abandoned).toEqual({ ok: false, status: 499, message: 'client disconnected before its turn' });
      f.releases.shift()!();
      await tick();
      expect(f.state.order).toEqual(['first', 'last']); // 'abandoned' never reached the proxy
      f.releases.shift()!();
      expect((await first).ok).toBe(true);
      expect((await last).ok).toBe(true);
    });

    it('a 429 from the proxy is the error it is: its status and body, no retry, and the line moves on', async () => {
      const mod = await load({ OPENAI_TTS_MODEL: 'cc-tts' });
      const fn = vi.fn(async () => new Response('slots in use', { status: 429 }));
      vi.stubGlobal('fetch', fn);
      expect(await mod.synthesizeOpenAI('x')).toEqual({ ok: false, status: 429, message: 'slots in use' });
      expect(await mod.synthesizeOpenAI('y')).toEqual({ ok: false, status: 429, message: 'slots in use' });
      expect(fn).toHaveBeenCalledTimes(2);
    });

    it('does not gate a plain OpenAI endpoint (no OPENAI_TTS_MODEL)', async () => {
      const mod = await load({});
      const f = slowFetch();
      vi.stubGlobal('fetch', f.fn);
      const calls = [mod.synthesizeOpenAI('a'), mod.synthesizeOpenAI('b')];
      await tick();
      expect(f.state.order).toEqual(['a', 'b']);
      f.releases.splice(0).forEach((r) => r());
      await Promise.all(calls);
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
