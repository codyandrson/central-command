/**
 * Self-heal coverage for GET /api/workspace/:key.
 *
 * A per-agent workspace whose root is the *computed default* path (no
 * `openclaw.json` entry for that agent) is auto-created if missing, so
 * DB-only agents stop 404ing. An *explicitly-configured* but missing path
 * stays a visible error — that's a real misconfiguration, not a fresh
 * agent, so it must not be silently created.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

describe('workspace self-heal (default-path auto-create)', () => {
  let homeDir: string;
  let mainWorkspace: string;
  let memoryPath: string;
  let memoryDir: string;

  beforeEach(async () => {
    vi.resetModules();
    homeDir = await fs.mkdtemp(path.join(os.tmpdir(), 'workspace-selfheal-test-'));
    mainWorkspace = path.join(homeDir, '.openclaw', 'workspace');
    memoryPath = path.join(mainWorkspace, 'MEMORY.md');
    memoryDir = path.join(mainWorkspace, 'memory');
    await fs.mkdir(memoryDir, { recursive: true });
  });

  afterEach(async () => {
    vi.restoreAllMocks();
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
    vi.doMock('../lib/gateway-rpc.js', () => ({
      gatewayFilesList: vi.fn(),
      gatewayFilesGet: vi.fn().mockResolvedValue(null),
      gatewayFilesSet: vi.fn(),
    }));

    const mod = await import('./workspace.js');
    const { Hono } = await import('hono');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('auto-creates a DB-only agent\'s default workspace dir and self-heals identity + chatPathLinks', async () => {
    const app = await buildApp();
    const workspaceRoot = path.join(homeDir, '.openclaw', 'workspace-freshagent');

    const identityRes = await app.request('/api/workspace/identity?agentId=freshagent');
    expect(identityRes.status).toBe(200);
    const identityJson = (await identityRes.json()) as { ok: boolean; content: string };
    expect(identityJson.ok).toBe(true);
    expect(identityJson.content).toContain('Name:');
    expect(identityJson.content).toContain('freshagent');
    await expect(fs.readFile(path.join(workspaceRoot, 'IDENTITY.md'), 'utf-8')).resolves.toBe(identityJson.content);

    const chatLinksRes = await app.request('/api/workspace/chatPathLinks?agentId=freshagent');
    expect(chatLinksRes.status).toBe(200);
    const chatLinksJson = (await chatLinksRes.json()) as { ok: boolean };
    expect(chatLinksJson.ok).toBe(true);
  });

  it('does not auto-create an explicitly-configured but missing workspace path (stays 404)', async () => {
    const configuredMissingPath = path.join(homeDir, 'somewhere-else', 'configured-agent');
    const openclawDir = path.join(homeDir, '.openclaw');
    await fs.mkdir(openclawDir, { recursive: true });
    await fs.writeFile(
      path.join(openclawDir, 'openclaw.json'),
      JSON.stringify({ agents: { list: [{ id: 'configuredagent', workspace: configuredMissingPath }] } }),
    );

    const app = await buildApp();
    const res = await app.request('/api/workspace/identity?agentId=configuredagent');

    expect(res.status).toBe(404);
    await expect(fs.access(configuredMissingPath)).rejects.toThrow();
  });

  it('rejects a malformed agentId before touching the filesystem', async () => {
    const app = await buildApp();
    const res = await app.request('/api/workspace/identity?agentId=..%2Fbad');

    expect(res.status).toBe(400);
    const json = (await res.json()) as { ok: boolean; error: string };
    expect(json.ok).toBe(false);
    expect(json.error).toContain('Invalid agent id');
  });
});
