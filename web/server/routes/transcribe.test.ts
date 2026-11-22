// @vitest-environment node
/** Tests for the transcribe and language routes. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

describe('transcribe routes', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockDeps(overrides: {
    openaiKey?: string;
    language?: string;
  } = {}) {
    const mockConfig: Record<string, unknown> = {
      auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443,
      openaiApiKey: overrides.openaiKey ?? 'sk-test',
      language: overrides.language || 'en',
      limits: { transcribe: 12 * 1024 * 1024 },
    };
    vi.doMock('../lib/config.js', () => ({
      config: mockConfig,
      updateConfig: (key: string, value: unknown) => { mockConfig[key] = value; },
      SESSION_COOKIE_NAME: 'nerve_session_3000',
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitTranscribe: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    vi.doMock('../lib/constants.js', () => ({
      OPENAI_STT_MODEL: 'cc-stt',
      SUPPORTED_LANGUAGES: [
        { code: 'en', name: 'English', nativeName: 'English' },
        { code: 'de', name: 'German', nativeName: 'Deutsch' },
      ],
    }));
    vi.doMock('../lib/env-file.js', () => ({
      writeEnvKey: vi.fn(async () => {}),
    }));
    vi.doMock('../services/openai-whisper.js', () => ({
      transcribe: vi.fn(async () => ({ ok: true, text: 'openai transcription' })),
    }));
  }

  async function buildApp() {
    const mod = await import('./transcribe.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  /** A multipart audio upload, as the browser sends it (the node environment above: jsdom cannot parse one). */
  function audioUpload(): RequestInit {
    const boundary = '----testboundary';
    const body = [
      `--${boundary}`,
      'Content-Disposition: form-data; name="file"; filename="clip.webm"',
      'Content-Type: audio/webm',
      '',
      'abc',
      `--${boundary}--`,
      '',
    ].join('\r\n');
    return { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body };
  }

  describe('POST /api/transcribe', () => {
    it('transcribes through the OpenAI-shaped endpoint with the language hint', async () => {
      mockDeps({ language: 'de' });
      const app = await buildApp();
      const res = await app.request('/api/transcribe', audioUpload());
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ text: 'openai transcription' });
      const whisper = await import('../services/openai-whisper.js');
      const call = vi.mocked(whisper.transcribe).mock.calls[0];
      expect(call[1]).toBe('clip.webm');
      expect(call[3]).toBe('de');
    });

    it('says the key is missing rather than trying a local engine', async () => {
      mockDeps({ openaiKey: '' });
      const app = await buildApp();
      const res = await app.request('/api/transcribe', audioUpload());
      expect(res.status).toBe(500);
      expect(await res.text()).toContain('OPENAI_API_KEY');
      const whisper = await import('../services/openai-whisper.js');
      expect(vi.mocked(whisper.transcribe)).not.toHaveBeenCalled();
    });

    it('rejects a request with no file', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/transcribe', { method: 'POST', body: new FormData() });
      expect(res.status).toBe(400);
    });
  });

  describe('GET /api/transcribe/config', () => {
    it('reports the one provider and its model, the same shape as the API route', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/transcribe/config');
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ provider: 'openai', model: 'cc-stt', language: 'en', modelReady: true });
    });

    it('has no PUT: there is no provider or model to switch', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/transcribe/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: 'local' }),
      });
      expect(res.status).toBe(404);
    });
  });

  describe('GET /api/language', () => {
    it('returns the language and the supported list', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/language');
      expect(res.status).toBe(200);
      const json = (await res.json()) as Record<string, unknown>;
      expect(json.language).toBe('en');
      expect(json.supported).toEqual([
        { code: 'en', name: 'English', nativeName: 'English' },
        { code: 'de', name: 'German', nativeName: 'Deutsch' },
      ]);
      expect(json).not.toHaveProperty('edgeVoiceGender');
      expect(json).not.toHaveProperty('providers');
    });
  });

  describe('PUT /api/language', () => {
    it('rejects unsupported language code', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/language', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: 'xx' }),
      });
      expect(res.status).toBe(400);
    });

    it('accepts a valid language code and writes NERVE_LANGUAGE', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/language', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: 'de' }),
      });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ language: 'de' });
      const envFile = await import('../lib/env-file.js');
      expect(vi.mocked(envFile.writeEnvKey)).toHaveBeenCalledWith('NERVE_LANGUAGE', 'de');
    });

    it('returns 400 for invalid JSON', async () => {
      mockDeps();
      const app = await buildApp();
      const res = await app.request('/api/language', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: 'not json',
      });
      expect(res.status).toBe(400);
    });
  });
});
