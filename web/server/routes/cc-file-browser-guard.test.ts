/**
 * R18 / COCKPIT-028: the file browser's mutation routes are read-only before
 * any explicit enable, a secret-bearing FILE_BROWSER_ROOT is refused, and the
 * GETs the chat input bar uses stay open.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

vi.mock('dotenv/config', () => ({}));

const READ_ONLY = { ok: false, error: 'the file browser is read-only' };

describe('R18 / COCKPIT-028 file browser read-only guard', () => {
  let homeDir: string;
  let ws: string;

  beforeEach(async () => {
    homeDir = await fs.mkdtemp(path.join(os.tmpdir(), 'fbguard-test-'));
    ws = path.join(homeDir, '.openclaw', 'workspace');
    await fs.mkdir(ws, { recursive: true });
    await fs.writeFile(path.join(ws, 'MEMORY.md'), '# Memories\n');
    await fs.writeFile(path.join(ws, 'note.md'), 'hello');
    await fs.mkdir(path.join(ws, 'docs'));
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await fs.rm(homeDir, { recursive: true, force: true });
  });

  async function buildApp(writable: boolean, opts?: { remote?: boolean }) {
    vi.resetModules();
    vi.doUnmock('../lib/gateway-rpc.js');
    const remote = opts?.remote ?? false;
    const home = remote ? path.join(homeDir, 'remote-nonexistent') : homeDir;
    const wsRoot = remote ? path.join(home, '.openclaw', 'workspace') : ws;
    vi.doMock('../lib/config.js', () => ({
      config: {
        auth: false, port: 3000, host: '127.0.0.1', sslPort: 3443, home,
        memoryPath: path.join(wsRoot, 'MEMORY.md'),
        memoryDir: path.join(wsRoot, 'memory'),
        fileBrowserRoot: '',
        fileBrowserWritable: writable,
        workspaceRemote: false,
      },
      SESSION_COOKIE_NAME: 'nerve_session_3000',
    }));
    const gatewayFilesSet = vi.fn().mockResolvedValue({ ok: true });
    if (remote) {
      vi.doMock('../lib/gateway-rpc.js', () => ({
        gatewayFilesList: vi.fn().mockResolvedValue([]),
        gatewayFilesGet: vi.fn(),
        gatewayFilesSet,
      }));
      const detect = await import('../lib/workspace-detect.js');
      detect.clearWorkspaceDetectCache();
    }
    const mod = await import('./file-browser.js');
    const app = new Hono();
    app.route('/', mod.default);
    return { app, gatewayFilesSet };
  }

  const json = (method: string, p: string, body: object) => ({
    method, path: p,
    init: { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
  });

  const mutations = [
    json('PUT', '/api/files/write', { path: 'new.md', content: 'x' }),
    json('POST', '/api/files/rename', { path: 'note.md', newName: 'renamed.md' }),
    json('POST', '/api/files/move', { sourcePath: 'note.md', targetDirPath: 'docs' }),
    json('POST', '/api/files/trash', { path: 'note.md' }),
    json('POST', '/api/files/restore', { path: '.trash/none' }),
  ];

  describe('without the write flag', () => {
    for (const m of mutations) {
      it(`${m.method} ${m.path} answers 403 and touches nothing`, async () => {
        const { app } = await buildApp(false);
        const res = await app.request(m.path, m.init);
        expect(res.status).toBe(403);
        expect(await res.json()).toEqual(READ_ONLY);
        await expect(fs.readFile(path.join(ws, 'note.md'), 'utf-8')).resolves.toBe('hello');
        await expect(fs.stat(path.join(ws, 'new.md'))).rejects.toThrow();
        await expect(fs.stat(path.join(ws, '.trash'))).rejects.toThrow();
      });
    }

    it('guards the agent-scoped workspaces too', async () => {
      const { app } = await buildApp(false);
      const res = await app.request('/api/files/write', json('PUT', '', {
        path: 'x.md', content: 'x', agentId: 'research',
      }).init);
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual(READ_ONLY);
    });

    it('guards the remote gateway files-set fallback', async () => {
      const { app, gatewayFilesSet } = await buildApp(false, { remote: true });
      const res = await app.request('/api/files/write', json('PUT', '', {
        path: 'AGENTS.md', content: 'x',
      }).init);
      expect(res.status).toBe(403);
      expect(gatewayFilesSet).not.toHaveBeenCalled();
    });

    it('keeps the GETs open: tree, resolve and raw', async () => {
      await fs.writeFile(path.join(ws, 'pic.png'), 'png');
      const { app } = await buildApp(false);
      expect((await app.request('/api/files/tree')).status).toBe(200);
      expect((await app.request('/api/files/resolve?path=note.md')).status).toBe(200);
      expect((await app.request('/api/files/raw?path=pic.png')).status).toBe(200);
      expect((await app.request('/api/files/read?path=note.md')).status).toBe(200);
    });
  });

  describe('with FILE_BROWSER_WRITABLE', () => {
    it('write, rename, move, trash and restore take their normal 200 path', async () => {
      const { app } = await buildApp(true);
      const post = async (m: ReturnType<typeof json>) => app.request(m.path, m.init);

      const w = await post(mutations[0]);
      expect(w.status).toBe(200);
      await expect(fs.readFile(path.join(ws, 'new.md'), 'utf-8')).resolves.toBe('x');

      expect((await post(mutations[1])).status).toBe(200);
      await expect(fs.readFile(path.join(ws, 'renamed.md'), 'utf-8')).resolves.toBe('hello');

      expect((await post(json('POST', '/api/files/move', { sourcePath: 'renamed.md', targetDirPath: 'docs' }))).status).toBe(200);
      await expect(fs.readFile(path.join(ws, 'docs', 'renamed.md'), 'utf-8')).resolves.toBe('hello');

      const t = await post(json('POST', '/api/files/trash', { path: 'docs/renamed.md' }));
      expect(t.status).toBe(200);
      const trashed = ((await t.json()) as { to: string }).to;
      const r = await post(json('POST', '/api/files/restore', { path: trashed }));
      expect(r.status).toBe(200);
      await expect(fs.readFile(path.join(ws, 'docs', 'renamed.md'), 'utf-8')).resolves.toBe('hello');
    });
  });

  describe('a secret-bearing FILE_BROWSER_ROOT', () => {
    async function validateWithRoot(root: string) {
      vi.resetModules();
      vi.doUnmock('../lib/config.js');
      vi.stubEnv('FILE_BROWSER_ROOT', root);
      const err = vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      const mod = await import('../lib/config.js');
      mod.validateConfig();
      return { config: mod.config, err };
    }

    for (const secret of ['.env', '.env.production', '.git', 'certs']) {
      it(`refuses a root containing ${secret}`, async () => {
        const root = path.join(homeDir, 'root');
        await fs.mkdir(root);
        if (secret.startsWith('.env')) await fs.writeFile(path.join(root, secret), 'KEY=1');
        else await fs.mkdir(path.join(root, secret));
        const { config, err } = await validateWithRoot(root);
        expect(config.fileBrowserRoot).toBe('');
        expect(err.mock.calls.join(' ')).toContain(secret);
      });
    }

    it('accepts a root with no secrets', async () => {
      const root = path.join(homeDir, 'clean');
      await fs.mkdir(root);
      await fs.writeFile(path.join(root, 'README.md'), 'hi');
      const { config } = await validateWithRoot(root);
      expect(config.fileBrowserRoot).toBe(root);
    });
  });
});
