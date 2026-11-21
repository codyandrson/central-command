/** Tests for the TTS route (POST /api/tts, GET/PUT /api/tts/config). */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('TTS routes', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockDeps(overrides: {
    openaiKey?: string;
    replicateToken?: string;
    mimoKey?: string;
    edgeResult?: { ok: boolean; buf?: Buffer; message?: string; status?: number; contentType?: string };
    openaiResult?: { ok: boolean; buf?: Buffer; message?: string; status?: number };
    /** Replaces the OpenAI synthesiser outright (to hold a job open, or to throw). */
    openaiImpl?: (text: string) => Promise<{ ok: boolean; buf?: Buffer; message?: string; status?: number }>;
    replicateResult?: { ok: boolean; buf?: Buffer; message?: string; status?: number };
    xiaomiResult?: { ok: boolean; buf?: Buffer; message?: string; status?: number; contentType?: string };
    /** Deployment speech mode as getDeploymentSpeech() would report it (null = not in the mode). */
    deployment?: { model: string; voice: string; voiceSource: 'env' | 'saved' | 'default' } | null;
    /** OPENAI_TTS_VOICE as the environment holds it. */
    envVoice?: string;
  } = {}) {
    vi.doMock('../lib/config.js', () => ({
      config: {
        auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
        openaiApiKey: overrides.openaiKey || '',
        replicateApiToken: overrides.replicateToken || '',
        mimoApiKey: overrides.mimoKey || '',
      },
      SESSION_COOKIE_NAME: 'nerve_session_3000',
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitTTS: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    vi.doMock('../services/tts-cache.js', () => ({
      getTtsCache: vi.fn(() => null),
      setTtsCache: vi.fn(),
    }));
    vi.doMock('../services/edge-tts.js', () => ({
      synthesizeEdge: vi.fn(async () =>
        overrides.edgeResult || { ok: true, buf: Buffer.from('fake-audio'), contentType: 'audio/mpeg' }
      ),
    }));
    vi.doMock('../services/openai-tts.js', () => ({
      synthesizeOpenAI: vi.fn(async (text: string) =>
        overrides.openaiImpl
          ? overrides.openaiImpl(text)
          : overrides.openaiResult || { ok: true, buf: Buffer.from('fake-openai-audio') }
      ),
    }));
    vi.doMock('../services/replicate-tts.js', () => ({
      synthesizeReplicate: vi.fn(async () =>
        overrides.replicateResult || { ok: true, buf: Buffer.from('fake-replicate-audio') }
      ),
    }));
    vi.doMock('../services/xiaomi-tts.js', () => ({
      synthesizeXiaomi: vi.fn(async () =>
        overrides.xiaomiResult || { ok: true, buf: Buffer.from('RIFFdemo'), contentType: 'audio/wav' }
      ),
    }));
    vi.doMock('../lib/tts-config.js', () => ({
      getTTSConfig: vi.fn(() => ({
        openai: { voice: 'alloy', model: 'tts-1', instructions: '' },
        edge: { voice: 'en-US-JennyNeural' },
        qwen: {},
        xiaomi: { model: 'mimo-v2-tts', voice: 'mimo_default', style: 'Happy' },
      })),
      updateTTSConfig: vi.fn((patch: unknown) => patch),
      getDeploymentSpeech: vi.fn(() => overrides.deployment ?? null),
      deploymentVoiceFromEnv: vi.fn(() => overrides.envVoice ?? ''),
      DEPLOYMENT_VOICE_PATTERN: /^[A-Za-z0-9_-]{1,64}$/,
    }));
  }

  async function buildApp() {
    const mod = await import('./tts.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  describe('POST /api/tts', () => {
    it('returns 400 when text is missing', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      expect(res.status).toBe(400);
    });

    it('returns 400 when text is empty', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: '   ' }),
      });
      expect(res.status).toBe(400);
    });

    it('falls back to edge TTS when no API keys set', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello world' }),
      });
      expect(res.status).toBe(200);
      expect(res.headers.get('Content-Type')).toContain('audio');
    });

    it('uses OpenAI when key is set and no explicit provider', async () => {
      mockDeps({ openaiKey: 'sk-test' });
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello' }),
      });
      expect(res.status).toBe(200);
    });

    it('uses explicit edge provider even when OpenAI key exists', async () => {
      mockDeps({ openaiKey: 'sk-test' });
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello', provider: 'edge' }),
      });
      expect(res.status).toBe(200);
    });

    it('uses explicit Xiaomi provider and returns WAV audio', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello', provider: 'xiaomi' }),
      });
      expect(res.status).toBe(200);
      expect(res.headers.get('Content-Type')).toBe('audio/wav');
    });

    it('honors explicit Xiaomi provider even when other keys exist', async () => {
      mockDeps({ openaiKey: 'sk-test', replicateToken: 'r8-test', mimoKey: 'sk-mimo' });
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello', provider: 'xiaomi' }),
      });
      expect(res.status).toBe(200);
      expect(res.headers.get('Content-Type')).toBe('audio/wav');
    });

    it('returns Xiaomi provider errors', async () => {
      mockDeps({ xiaomiResult: { ok: false, message: 'Xiaomi failed', status: 502 } });
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello', provider: 'xiaomi' }),
      });
      expect(res.status).toBe(502);
    });

    it('returns error from provider failure', async () => {
      mockDeps({ edgeResult: { ok: false, message: 'Edge TTS failed', status: 500 } });
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello' }),
      });
      expect(res.status).toBe(500);
    });

    it('returns 400 for text exceeding max length', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'a'.repeat(5001) }),
      });
      expect(res.status).toBe(400);
    });
  });

  describe('POST /api/tts: identical requests in flight', () => {
    function post(app: Hono, text: string, extra: Record<string, unknown> = {}) {
      return app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, provider: 'openai', ...extra }),
      });
    }

    /** A synthesiser whose jobs are held open until the test releases them. */
    function heldSynth() {
      const jobs: Array<{ text: string; finish: (r: { ok: boolean; buf?: Buffer; message?: string; status?: number }) => void; fail: (e: Error) => void }> = [];
      const impl = (text: string) =>
        new Promise<{ ok: boolean; buf?: Buffer; message?: string; status?: number }>((resolve, reject) => {
          jobs.push({ text, finish: resolve, fail: reject });
        });
      return { jobs, impl };
    }

    async function synthCalls() {
      const mod = await import('../services/openai-tts.js');
      return vi.mocked(mod.synthesizeOpenAI);
    }

    it('runs one synthesis for two concurrent identical requests and gives both the audio', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const first = post(app, 'Same sentence.');
      const second = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      expect(await synthCalls()).toHaveBeenCalledTimes(1);
      held.jobs[0].finish({ ok: true, buf: Buffer.from('the-audio') });
      const [a, b] = await Promise.all([first, second]);
      expect(a.status).toBe(200);
      expect(b.status).toBe(200);
      expect(Buffer.from(await a.arrayBuffer()).toString()).toBe('the-audio');
      expect(Buffer.from(await b.arrayBuffer()).toString()).toBe('the-audio');
      expect(await synthCalls()).toHaveBeenCalledTimes(1);
    });

    it('runs a synthesis per text when the texts differ', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const first = post(app, 'One sentence.');
      const second = post(app, 'Another sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(2));
      held.jobs[0].finish({ ok: true, buf: Buffer.from('a') });
      held.jobs[1].finish({ ok: true, buf: Buffer.from('b') });
      await Promise.all([first, second]);
      expect(await synthCalls()).toHaveBeenCalledTimes(2);
    });

    it('also separates requests that differ in voice', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const first = post(app, 'Same sentence.', { voice: 'nova' });
      const second = post(app, 'Same sentence.', { voice: 'onyx' });
      await vi.waitFor(() => expect(held.jobs).toHaveLength(2));
      held.jobs.forEach((j) => j.finish({ ok: true, buf: Buffer.from('x') }));
      await Promise.all([first, second]);
    });

    it('uses the cache key, so a stale browser model does not split requests in deployment speech mode', async () => {
      const held = heldSynth();
      mockDeps({
        openaiKey: 'sk-test',
        openaiImpl: held.impl,
        deployment: { model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' },
      });
      const app = await buildApp();
      const first = post(app, 'Same sentence.', { model: 'gpt-4o-mini-tts' });
      const second = post(app, 'Same sentence.', { model: 'tts-1' });
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      held.jobs[0].finish({ ok: true, buf: Buffer.from('x') });
      await Promise.all([first, second]);
      expect(await synthCalls()).toHaveBeenCalledTimes(1);
    });

    it('delivers a refusal to every waiter, and the next request tries again', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const first = post(app, 'Same sentence.');
      const second = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      held.jobs[0].finish({ ok: false, status: 503, message: 'engine busy' });
      const [a, b] = await Promise.all([first, second]);
      expect(a.status).toBe(503);
      expect(b.status).toBe(503);
      expect(await a.text()).toBe('engine busy');
      expect(await b.text()).toBe('engine busy');

      const third = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(2));
      held.jobs[1].finish({ ok: true, buf: Buffer.from('later') });
      expect((await third).status).toBe(200);
      expect(await synthCalls()).toHaveBeenCalledTimes(2);
    });

    it('delivers a thrown failure to every waiter, and the next request tries again', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      vi.spyOn(console, 'error').mockImplementation(() => {});
      const app = await buildApp();
      const first = post(app, 'Same sentence.');
      const second = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      held.jobs[0].fail(new Error('connection reset'));
      const [a, b] = await Promise.all([first, second]);
      expect(a.status).toBe(500);
      expect(b.status).toBe(500);

      const third = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(2));
      held.jobs[1].finish({ ok: true, buf: Buffer.from('later') });
      expect((await third).status).toBe(200);
    });

    it('lets the job finish for the others when one client gives up', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const controller = new AbortController();
      const leaving = app.request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Same sentence.', provider: 'openai' }),
        signal: controller.signal,
      });
      const staying = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      controller.abort();
      held.jobs[0].finish({ ok: true, buf: Buffer.from('still-here') });
      const res = await staying;
      expect(res.status).toBe(200);
      expect(Buffer.from(await res.arrayBuffer()).toString()).toBe('still-here');
      expect(await synthCalls()).toHaveBeenCalledTimes(1);
      await leaving.catch(() => {});
    });

    it("takes the job out of the speech alias's line only when the LAST request waiting on it is gone", async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const one = new AbortController();
      const two = new AbortController();
      const req = (signal: AbortSignal) =>
        app.request('/api/tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: 'Same sentence.', provider: 'openai' }),
          signal,
        });
      const first = req(one.signal);
      const second = req(two.signal);
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      const call = (await synthCalls()).mock.calls[0];
      const lineSignal = call[3] as AbortSignal; // what synthesizeOpenAI queues on
      expect(lineSignal.aborted).toBe(false);
      one.abort();
      await new Promise((r) => setTimeout(r, 5));
      expect(lineSignal.aborted).toBe(false); // the second request still wants it
      two.abort();
      await vi.waitFor(() => expect(lineSignal.aborted).toBe(true));
      held.jobs[0].finish({ ok: false, status: 499, message: 'client disconnected before its turn' });
      await Promise.allSettled([first, second]);
    });

    it('surfaces a proxy 429 as the same status and body, and a later request tries again', async () => {
      const held = heldSynth();
      mockDeps({ openaiKey: 'sk-test', openaiImpl: held.impl });
      const app = await buildApp();
      const first = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(1));
      held.jobs[0].finish({ ok: false, status: 429, message: 'slots in use' });
      const res = await first;
      expect(res.status).toBe(429);
      expect(await res.text()).toBe('slots in use');
      const again = post(app, 'Same sentence.');
      await vi.waitFor(() => expect(held.jobs).toHaveLength(2));
      held.jobs[1].finish({ ok: true, buf: Buffer.from('ok') });
      expect((await again).status).toBe(200);
    });
  });

  describe('GET /api/tts/config', () => {
    it('returns current TTS config', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts/config');
      expect(res.status).toBe(200);
      const json = (await res.json()) as Record<string, unknown>;
      expect(json).toHaveProperty('openai');
      expect(json).toHaveProperty('edge');
    });

    it('reports deploymentSpeech as null outside deployment speech mode', async () => {
      mockDeps();
      const app = await buildApp();
      const json = (await (await app.request('/api/tts/config')).json()) as Record<string, unknown>;
      expect(json.deploymentSpeech).toBeNull();
    });

    it('reports the deployment model, voice and where the voice came from, keeping the existing keys', async () => {
      mockDeps({ openaiKey: 'sk-test', deployment: { model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' } });
      const app = await buildApp();
      const json = (await (await app.request('/api/tts/config')).json()) as Record<string, unknown>;
      expect(json.deploymentSpeech).toEqual({ model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' });
      expect(json).toHaveProperty('openai');
      expect(json).toHaveProperty('edge');
      expect(json.defaultProvider).toBe('openai');
    });
  });

  describe('PUT /api/tts/config', () => {
    it('rejects unknown sections', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unknown_section: { voice: 'test' } }),
      });
      expect(res.status).toBe(400);
    });

    it('accepts valid config patch', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edge: { voice: 'en-US-GuyNeural' } }),
      });
      expect(res.status).toBe(200);
    });

    it('accepts valid Xiaomi config patch', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ xiaomi: { model: 'mimo-v2-tts', voice: 'default_en', style: 'Happy' } }),
      });
      expect(res.status).toBe(200);
    });

    function putConfig(app: Hono, body: unknown) {
      return app.request('/api/tts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }

    it('accepts a deployment voice and returns the effective deploymentSpeech', async () => {
      mockDeps({ deployment: { model: 'cc-tts', voice: 'bf_emma', voiceSource: 'saved' } });
      const app = await buildApp();
      const res = await putConfig(app, { openai: { deploymentVoice: 'bf_emma' } });
      expect(res.status).toBe(200);
      const json = (await res.json()) as Record<string, unknown>;
      expect(json.deploymentSpeech).toEqual({ model: 'cc-tts', voice: 'bf_emma', voiceSource: 'saved' });
    });

    it.each(['', 'nova voice', 'af/heart', 'voice;rm', 'a'.repeat(65), 'café'])(
      'rejects the deployment voice %j with 400',
      async (voice) => {
        mockDeps();
        const app = await buildApp();
        const res = await putConfig(app, { openai: { deploymentVoice: voice } });
        expect(res.status).toBe(400);
      },
    );

    it('accepts a 64-character deployment voice and the token characters _ and -', async () => {
      mockDeps();
      const app = await buildApp();
      expect((await putConfig(app, { openai: { deploymentVoice: 'a'.repeat(64) } })).status).toBe(200);
      expect((await putConfig(app, { openai: { deploymentVoice: 'af_heart-2' } })).status).toBe(200);
    });

    it('refuses to change the deployment voice when OPENAI_TTS_VOICE is set, and says why', async () => {
      mockDeps({ envVoice: 'am_adam', deployment: { model: 'cc-tts', voice: 'am_adam', voiceSource: 'env' } });
      const app = await buildApp();
      const res = await putConfig(app, { openai: { deploymentVoice: 'bf_emma' } });
      expect(res.status).toBe(409);
      expect(await res.text()).toContain('OPENAI_TTS_VOICE');
    });

    it('still saves other openai settings when OPENAI_TTS_VOICE is set', async () => {
      mockDeps({ envVoice: 'am_adam' });
      const app = await buildApp();
      expect((await putConfig(app, { openai: { instructions: 'Calm.' } })).status).toBe(200);
    });

    it('rejects non-string values', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/tts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ openai: { voice: 123 } }),
      });
      expect(res.status).toBe(400);
    });
  });
});
