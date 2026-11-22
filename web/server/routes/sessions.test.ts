/** Tests for the sessions API routes. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

describe('sessions routes', () => {
  let tmpDir: string;

  beforeEach(async () => {
    vi.resetModules();
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'sessions-test-'));
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  async function buildApp() {
    // Mock config to use our temp sessions dir
    vi.doMock('../lib/config.js', () => ({
      config: {
        home: tmpDir,
        sessionsDir: tmpDir,
        auth: false,
        port: 3000,
        host: '127.0.0.1',
        sslPort: 3443,
      },
      SESSION_COOKIE_NAME: 'nerve_session_3000',
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));

    const mod = await import('./sessions.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('rejects invalid session IDs (not UUID)', async () => {
    const app = await buildApp();
    const res = await app.request('/api/sessions/not-a-uuid/model');
    expect(res.status).toBe(400);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(false);
    expect(json.error).toContain('Invalid session ID');
  });

  it('returns 200 with missing=true when transcript does not exist', async () => {
    const app = await buildApp();
    const uuid = '12345678-1234-1234-1234-123456789abc';
    const res = await app.request(`/api/sessions/${uuid}/model`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBeNull();
    expect(json.thinking).toBeNull();
    expect(json.missing).toBe(true);
  });

  it('returns runtime defaults from transcript entries near the top', async () => {
    const app = await buildApp();
    const uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const transcript = [
      JSON.stringify({ type: 'session_start', ts: Date.now() }),
      JSON.stringify({ type: 'model_change', modelId: 'anthropic/claude-opus-4', ts: Date.now() }),
      JSON.stringify({ type: 'thinking_level_change', thinkingLevel: 'medium', ts: Date.now() }),
      JSON.stringify({ type: 'message', role: 'user', content: 'hello' }),
    ].join('\n');
    await fs.writeFile(path.join(tmpDir, `${uuid}.jsonl`), transcript);

    const res = await app.request(`/api/sessions/${uuid}/model`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBe('anthropic/claude-opus-4');
    expect(json.thinking).toBe('medium');
    expect(json.missing).toBe(false);
  });

  it('returns model: null when transcript has no runtime defaults', async () => {
    const app = await buildApp();
    const uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const transcript = [
      JSON.stringify({ type: 'session_start', ts: Date.now() }),
      JSON.stringify({ type: 'message', role: 'user', content: 'hello' }),
    ].join('\n');
    await fs.writeFile(path.join(tmpDir, `${uuid}.jsonl`), transcript);

    const res = await app.request(`/api/sessions/${uuid}/model`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBeNull();
    expect(json.thinking).toBeNull();
    expect(json.missing).toBe(false);
  });

  it('finds deleted transcripts', async () => {
    const app = await buildApp();
    const uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const transcript = JSON.stringify({ type: 'model_change', modelId: 'openai/gpt-4o', ts: Date.now() });
    await fs.writeFile(path.join(tmpDir, `${uuid}.jsonl.deleted-1234`), transcript);

    const res = await app.request(`/api/sessions/${uuid}/model`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBe('openai/gpt-4o');
    expect(json.thinking).toBeNull();
    expect(json.missing).toBe(false);
  });

  it('reads non-main agent transcripts when agentId is provided', async () => {
    const app = await buildApp();
    const uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const agentSessionsDir = path.join(tmpDir, '.openclaw', 'agents', 'smoke257', 'sessions');
    await fs.mkdir(agentSessionsDir, { recursive: true });
    await fs.writeFile(path.join(agentSessionsDir, `${uuid}.jsonl`), [
      JSON.stringify({ type: 'model_change', modelId: 'openai-codex/gpt-5.4', ts: Date.now() }),
      JSON.stringify({ type: 'thinking_level_change', thinkingLevel: 'medium', ts: Date.now() }),
    ].join('\n'));

    const res = await app.request(`/api/sessions/${uuid}/model?agentId=smoke257`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBe('openai-codex/gpt-5.4');
    expect(json.thinking).toBe('medium');
    expect(json.missing).toBe(false);
  });

  it('resolves runtime defaults by sessionKey for non-main agents', async () => {
    const app = await buildApp();
    const uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const sessionKey = 'agent:smoke257:main';
    const agentSessionsDir = path.join(tmpDir, '.openclaw', 'agents', 'smoke257', 'sessions');
    await fs.mkdir(agentSessionsDir, { recursive: true });
    await fs.writeFile(path.join(agentSessionsDir, 'sessions.json'), JSON.stringify({
      [sessionKey]: { sessionId: uuid },
    }));
    await fs.writeFile(path.join(agentSessionsDir, `${uuid}.jsonl`), [
      JSON.stringify({ type: 'model_change', modelId: 'openai-codex/gpt-5.4', ts: Date.now() }),
      JSON.stringify({ type: 'thinking_level_change', thinkingLevel: 'medium', ts: Date.now() }),
    ].join('\n'));

    const res = await app.request(`/api/sessions/runtime?sessionKey=${encodeURIComponent(sessionKey)}`);
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(true);
    expect(json.model).toBe('openai-codex/gpt-5.4');
    expect(json.thinking).toBe('medium');
    expect(json.missing).toBe(false);
  });

  it('serves omitted image bytes from a session transcript', async () => {
    const app = await buildApp();
    const sessionKey = 'agent:main:main';
    const sessionId = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const timestamp = 1775131617235;
    const base64 = Buffer.from('hello-image').toString('base64');

    await fs.writeFile(path.join(tmpDir, 'sessions.json'), JSON.stringify({
      [sessionKey]: { sessionId },
    }));
    await fs.writeFile(path.join(tmpDir, `${sessionId}.jsonl`), [
      JSON.stringify({ type: 'session_start', ts: Date.now() }),
      JSON.stringify({
        type: 'message',
        message: {
          timestamp,
          content: [
            { type: 'text', text: 'testing' },
            { type: 'image', mimeType: 'image/png', data: base64 },
          ],
        },
      }),
    ].join('\n'));

    const res = await app.request(`/api/sessions/media?sessionKey=${encodeURIComponent(sessionKey)}&timestamp=${timestamp}&imageIndex=0`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('image/png');
    expect(res.headers.get('content-disposition')).toContain(`message-${timestamp}-image-0.png`);
    const body = Buffer.from(await res.arrayBuffer()).toString('utf-8');
    expect(body).toBe('hello-image');
  });

  it('returns 404 when session transcript media cannot be resolved', async () => {
    const app = await buildApp();
    const sessionKey = 'agent:main:main';
    await fs.writeFile(path.join(tmpDir, 'sessions.json'), JSON.stringify({
      [sessionKey]: { sessionId: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' },
    }));

    const res = await app.request(`/api/sessions/media?sessionKey=${encodeURIComponent(sessionKey)}&timestamp=1775131617235&imageIndex=0`);
    expect(res.status).toBe(404);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.ok).toBe(false);
  });
});
