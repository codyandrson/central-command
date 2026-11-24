/** GET/PUT /api/speech/models on the k3s profile: lists from Central Command, the choice kept here. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

const LISTS = {
  tts: { selected: 'cc-tts', default: 'cc-tts', models: ['cc-tts', 'kokoro-alt'] },
  stt: { selected: 'cc-stt', default: 'cc-stt', models: ['cc-stt', 'whisper-large'] },
  error: null,
};

describe('speech model routes', () => {
  // What tts-config.json holds, as the mocked module reads it.
  let stored: { tts: string; stt: string };
  const updateTTSConfig = vi.fn((patch: Record<string, { deploymentModel?: string }>) => {
    if (patch.openai?.deploymentModel !== undefined) stored.tts = patch.openai.deploymentModel;
    if (patch.transcribe?.deploymentModel !== undefined) stored.stt = patch.transcribe.deploymentModel;
  });

  beforeEach(() => {
    vi.resetModules();
    stored = { tts: '', stt: '' };
    updateTTSConfig.mockClear();
    vi.stubEnv('OPENAI_TTS_MODEL', 'cc-tts');
    vi.stubEnv('OPENAI_STT_MODEL', 'cc-stt');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  async function buildApp(listResponse: () => Promise<Response>) {
    vi.doMock('../lib/config.js', () => ({ config: { gatewayUrl: 'http://127.0.0.1:8080' } }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    vi.doMock('../lib/tts-config.js', async () => ({
      getDeploymentSpeech: () => (process.env.OPENAI_TTS_MODEL
        ? { model: stored.tts || process.env.OPENAI_TTS_MODEL, modelSource: stored.tts ? 'saved' : 'env', voice: 'af_heart', voiceSource: 'default' }
        : null),
      getDeploymentSttModel: () => (process.env.OPENAI_STT_MODEL ? stored.stt || process.env.OPENAI_STT_MODEL : null),
      updateTTSConfig,
      SPEECH_MODEL_PATTERN: (await vi.importActual<typeof import('../lib/tts-config.js')>('../lib/tts-config.js'))
        .SPEECH_MODEL_PATTERN,
    }));
    const fetchMock = vi.fn(listResponse);
    vi.stubGlobal('fetch', fetchMock);
    const mod = await import('./cc-speech-models.js');
    const app = new Hono();
    app.route('/', mod.default);
    return { app, fetchMock };
  }

  const ok = () => Promise.resolve(new Response(JSON.stringify(LISTS), { status: 200 }));
  const put = (app: Hono, body: unknown) => app.request('/api/speech/models', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });

  it('lists from Central Command and reports the env aliases as selected by default', async () => {
    const { app, fetchMock } = await buildApp(ok);
    const res = await app.request('/api/speech/models');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(LISTS);
    expect(String(fetchMock.mock.calls[0][0])).toBe('http://127.0.0.1:8080/api/speech/models');
  });

  it('still answers, with the current models, when the list cannot be fetched', async () => {
    const { app } = await buildApp(() => Promise.resolve(new Response('nope', { status: 404 })));
    const body = await (await app.request('/api/speech/models')).json();
    expect(body.tts).toEqual({ selected: 'cc-tts', default: 'cc-tts', models: [] });
    expect(body.stt.selected).toBe('cc-stt');
    expect(body.error).toBe('Central Command HTTP 404');
  });

  it('a side outside deployment speech mode is null', async () => {
    vi.stubEnv('OPENAI_STT_MODEL', '');
    const { app } = await buildApp(ok);
    const body = await (await app.request('/api/speech/models')).json();
    expect(body.stt).toBeNull();
    expect((await put(app, { stt: 'whisper-large' })).status).toBe(409);
  });

  it('saves a listed model and refuses one that is not listed for its mode', async () => {
    const { app } = await buildApp(ok);
    const res = await put(app, { tts: 'kokoro-alt', stt: 'whisper-large' });
    expect(res.status).toBe(200);
    expect(stored).toEqual({ tts: 'kokoro-alt', stt: 'whisper-large' });
    expect((await res.json()).tts.selected).toBe('kokoro-alt');

    for (const bad of [{ tts: 'whisper-large' }, { stt: 'cc-default' }, { tts: 'a b' }, { tts: 7 }, { other: 'x' }, {}]) {
      expect((await put(app, bad)).status).toBe(422);
    }
    expect(stored).toEqual({ tts: 'kokoro-alt', stt: 'whisper-large' });
  });

  it('answers each refusal as plain text the Settings page shows', async () => {
    const { app } = await buildApp(ok);
    const cases: Array<[unknown, number, string]> = [
      [{ tts: 7 }, 422, '"tts" must be a string or null'],
      [{ stt: 'a b' }, 422, '"stt" is not a model id'],
      [{ other: 'x' }, 422, 'Name tts and/or stt, and nothing else'],
      [{}, 422, 'Name tts and/or stt, and nothing else'],
      [['tts'], 400, 'Body must be a JSON object'],
    ];
    for (const [bad, status, text] of cases) {
      const res = await put(app, bad);
      expect(res.status).toBe(status);
      expect(await res.text()).toBe(text);
    }
    const malformed = await app.request('/api/speech/models', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{nope',
    });
    expect(malformed.status).toBe(400);
    expect(updateTTSConfig).not.toHaveBeenCalled();
  });

  it('trims a chosen model before checking and saving it', async () => {
    const { app } = await buildApp(ok);
    expect((await put(app, { tts: '  kokoro-alt  ' })).status).toBe(200);
    expect(stored.tts).toBe('kokoro-alt');
  });

  it('a refused half saves nothing', async () => {
    const { app } = await buildApp(ok);
    expect((await put(app, { tts: 'kokoro-alt', stt: 'nope' })).status).toBe(422);
    expect(updateTTSConfig).not.toHaveBeenCalled();
  });

  it('the alias or null goes back to the default without needing the list', async () => {
    const { app } = await buildApp(() => Promise.reject(new Error('down')));
    stored = { tts: 'kokoro-alt', stt: 'whisper-large' };
    expect((await put(app, { tts: 'cc-tts', stt: null })).status).toBe(200);
    expect(stored).toEqual({ tts: '', stt: '' });
    expect((await put(app, { tts: 'kokoro-alt' })).status).toBe(502);
  });
});
