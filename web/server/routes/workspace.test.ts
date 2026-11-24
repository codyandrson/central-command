import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Hono } from 'hono';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createChatPathLinksTemplate } from '../lib/chat-path-links-config.js';

describe('workspace routes', () => {
  let homeDir: string;
  let mainWorkspace: string;
  let researchWorkspace: string;
  let memoryPath: string;
  let memoryDir: string;
  let originalHome: string | undefined;
  let originalUser: string | undefined;

  beforeEach(async () => {
    vi.resetModules();
    homeDir = await fs.mkdtemp(path.join(os.tmpdir(), 'workspace-routes-test-'));
    mainWorkspace = path.join(homeDir, '.openclaw', 'workspace');
    researchWorkspace = path.join(homeDir, '.openclaw', 'workspace-research');
    memoryPath = path.join(mainWorkspace, 'MEMORY.md');
    memoryDir = path.join(mainWorkspace, 'memory');
    originalHome = process.env.HOME;
    originalUser = process.env.USER;
    process.env.HOME = homeDir;
    process.env.USER = 'tester';

    await fs.mkdir(memoryDir, { recursive: true });
    await fs.mkdir(researchWorkspace, { recursive: true });
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    process.env.HOME = originalHome;
    process.env.USER = originalUser;
    await fs.rm(homeDir, { recursive: true, force: true });
  });

  async function buildApp() {
    vi.resetModules();
    vi.doMock('../lib/config.js', () => ({
      config: {
        auth: false,
        port: 3000,
        host: '127.0.0.1',
        sslPort: 3443,
        home: homeDir,
        memoryPath,
        memoryDir,
      },
      SESSION_COOKIE_NAME: 'nerve_session_3000',
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));

    const mod = await import('./workspace.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('reads allowlisted files from the requested agent workspace', async () => {
    await fs.writeFile(path.join(mainWorkspace, 'TOOLS.md'), 'main tools');
    await fs.writeFile(path.join(researchWorkspace, 'TOOLS.md'), 'research tools');

    const app = await buildApp();
    const res = await app.request('/api/workspace/tools?agentId=research');

    expect(res.status).toBe(200);
    const json = (await res.json()) as { ok: boolean; content: string };
    expect(json.ok).toBe(true);
    expect(json.content).toBe('research tools');
  });

  it('writes allowlisted files into the requested agent workspace', async () => {
    await fs.writeFile(path.join(mainWorkspace, 'SOUL.md'), 'main soul');

    const app = await buildApp();
    const res = await app.request('/api/workspace/soul', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agentId: 'research', content: 'research soul' }),
    });

    expect(res.status).toBe(200);
    await expect(fs.readFile(path.join(researchWorkspace, 'SOUL.md'), 'utf-8')).resolves.toBe('research soul');
    await expect(fs.readFile(path.join(mainWorkspace, 'SOUL.md'), 'utf-8')).resolves.toBe('main soul');
  });

  it('self-heals missing local CHAT_PATH_LINKS.json using the shared template', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const app = await buildApp();

    const res = await app.request('/api/workspace/chatPathLinks');

    expect(res.status).toBe(200);
    const json = (await res.json()) as { ok: boolean; content: string };
    expect(json.ok).toBe(true);

    const expected = createChatPathLinksTemplate({
      platform: process.platform,
      homeDir: homeDir,
      workspaceRoot: mainWorkspace,
    });

    expect(json.content).toBe(expected);
    await expect(fs.readFile(path.join(mainWorkspace, 'CHAT_PATH_LINKS.json'), 'utf-8')).resolves.toBe(expected);
    expect(warnSpy).toHaveBeenCalledWith(
      `[workspace] Missing CHAT_PATH_LINKS.json; regenerated local default template at ${path.join(mainWorkspace, 'CHAT_PATH_LINKS.json')}`,
    );
  });

  it('lists file existence for the requested agent workspace', async () => {
    await fs.writeFile(path.join(mainWorkspace, 'USER.md'), 'main user');
    await fs.writeFile(path.join(researchWorkspace, 'TOOLS.md'), 'research tools');

    const app = await buildApp();
    const res = await app.request('/api/workspace?agentId=research');

    expect(res.status).toBe(200);
    const json = (await res.json()) as {
      ok: boolean;
      files: Array<{ key: string; exists: boolean }>;
    };

    expect(json.ok).toBe(true);
    expect(json.files.find((file) => file.key === 'tools')?.exists).toBe(true);
    expect(json.files.find((file) => file.key === 'user')?.exists).toBe(false);
  });

    it('rejects invalid agent ids', async () => {
      const app = await buildApp();
      const res = await app.request('/api/workspace?agentId=../bad');

      expect(res.status).toBe(400);
      const json = (await res.json()) as { ok: boolean; error: string };
      expect(json.ok).toBe(false);
      expect(json.error).toContain('Invalid agent id');
    });

  describe('a workspace that is not on this host', () => {
    // A workspace root that does NOT exist on disk.
    let remoteHomeDir: string;
    let remoteWorkspace: string;

    beforeEach(() => {
      remoteHomeDir = path.join(homeDir, 'remote-nonexistent');
      remoteWorkspace = path.join(remoteHomeDir, '.openclaw', 'workspace');
    });

    async function buildRemoteApp() {
      vi.resetModules();
      vi.doMock('../lib/config.js', () => ({
        config: {
          auth: false,
          port: 3000,
          host: '127.0.0.1',
          sslPort: 3443,
          home: remoteHomeDir,
          memoryPath: path.join(remoteWorkspace, 'MEMORY.md'),
          memoryDir: path.join(remoteWorkspace, 'memory'),
        },
        SESSION_COOKIE_NAME: 'nerve_session_3000',
      }));
      vi.doMock('../middleware/rate-limit.js', () => ({
        rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
      }));
      // Clear workspace detect cache so it re-checks
      const detectMod = await import('../lib/workspace-detect.js');
      detectMod.clearWorkspaceDetectCache();

      const mod = await import('./workspace.js');
      const app = new Hono();
      app.route('/', mod.default);
      return app;
    }

    it('GET /api/workspace/:key returns 404 for a file that is not there', async () => {
      const app = await buildRemoteApp();
      const res = await app.request('/api/workspace/soul');

      expect(res.status).toBe(404);
      const json = (await res.json()) as { ok: boolean; error: string };
      expect(json.ok).toBe(false);
    });

    it('GET chatPathLinks?agentId=main returns 200-empty instead of 404', async () => {
      // `main` is the current-lane alias and owns no workspace — the cockpit
      // asked for this on every page load and got a 404.
      const app = await buildRemoteApp();
      const res = await app.request('/api/workspace/chatPathLinks?agentId=main');

      expect(res.status).toBe(200);
      const json = (await res.json()) as { ok: boolean; content: string };
      expect(json.ok).toBe(true);
      expect(json.content).toBe('');
    });

    it('PUT /api/workspace/:key returns 500 and writes nothing', async () => {
      const app = await buildRemoteApp();
      const res = await app.request('/api/workspace/soul', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: '# Remote Soul' }),
      });

      expect(res.status).toBe(500);
      const json = (await res.json()) as { ok: boolean; error: string };
      expect(json.ok).toBe(false);
      await expect(fs.access(path.join(remoteWorkspace, 'SOUL.md'))).rejects.toThrow();
    });

    it('GET /api/workspace lists every file as absent', async () => {
      const app = await buildRemoteApp();
      const res = await app.request('/api/workspace');

      expect(res.status).toBe(200);
      const json = (await res.json()) as {
        ok: boolean;
        files: Array<{ key: string; exists: boolean }>;
        remoteWorkspace?: boolean;
      };
      expect(json.ok).toBe(true);
      expect(json.remoteWorkspace).toBe(true);
      expect(json.files.every((f) => !f.exists)).toBe(true);
    });
  });
});
