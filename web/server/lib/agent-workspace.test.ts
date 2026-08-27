import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

describe('agent-workspace', () => {
  let homeDir: string;
  let memoryPath: string;

  beforeEach(async () => {
    vi.resetModules();
    homeDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-workspace-test-'));
    memoryPath = path.join(homeDir, '.openclaw', 'workspace', 'MEMORY.md');
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await fs.rm(homeDir, { recursive: true, force: true });
  });

  async function loadModule() {
    vi.doMock('./config.js', () => ({
      config: {
        home: homeDir,
        memoryPath,
      },
    }));

    return import('./agent-workspace.js');
  }

  it('resolves main to the default workspace root', async () => {
    const { resolveAgentWorkspace } = await loadModule();

    expect(resolveAgentWorkspace()).toEqual({
      agentId: 'main',
      workspaceRoot: path.dirname(memoryPath),
      memoryPath,
      memoryDir: path.join(path.dirname(memoryPath), 'memory'),
    });

    expect(resolveAgentWorkspace('   ')).toEqual({
      agentId: 'main',
      workspaceRoot: path.dirname(memoryPath),
      memoryPath,
      memoryDir: path.join(path.dirname(memoryPath), 'memory'),
    });
  });

  it('falls back to a per-agent workspace for non-main agents', async () => {
    const { resolveAgentWorkspace } = await loadModule();

    expect(resolveAgentWorkspace('research')).toEqual({
      agentId: 'research',
      workspaceRoot: path.join(homeDir, '.openclaw', 'workspace-research'),
      memoryPath: path.join(homeDir, '.openclaw', 'workspace-research', 'MEMORY.md'),
      memoryDir: path.join(homeDir, '.openclaw', 'workspace-research', 'memory'),
    });
  });

  it('prefers explicitly configured agent workspaces from openclaw.json', async () => {
    const configDir = path.join(homeDir, '.openclaw');
    await fs.mkdir(configDir, { recursive: true });
    await fs.writeFile(path.join(configDir, 'openclaw.json'), JSON.stringify({
      agents: {
        defaults: { workspace: '/managed/workspaces' },
        list: [
          { id: 'research', workspace: '/vaults/research' },
        ],
      },
    }, null, 2));

    const { resolveAgentWorkspace } = await loadModule();

    expect(resolveAgentWorkspace('research')).toEqual({
      agentId: 'research',
      workspaceRoot: '/vaults/research',
      memoryPath: '/vaults/research/MEMORY.md',
      memoryDir: '/vaults/research/memory',
    });
  });

  it('uses agents.defaults.workspace for new non-main agents when configured', async () => {
    const configDir = path.join(homeDir, '.openclaw');
    await fs.mkdir(configDir, { recursive: true });
    await fs.writeFile(path.join(configDir, 'openclaw.json'), JSON.stringify({
      agents: {
        defaults: { workspace: '/managed/workspaces' },
      },
    }, null, 2));

    const { resolveAgentWorkspace } = await loadModule();

    expect(resolveAgentWorkspace('research')).toEqual({
      agentId: 'research',
      workspaceRoot: '/managed/workspaces/research',
      memoryPath: '/managed/workspaces/research/MEMORY.md',
      memoryDir: '/managed/workspaces/research/memory',
    });
  });

  it('isDefaultAgentWorkspacePath: true only for an unconfigured non-main agent', async () => {
    const configDir = path.join(homeDir, '.openclaw');
    await fs.mkdir(configDir, { recursive: true });
    await fs.writeFile(path.join(configDir, 'openclaw.json'), JSON.stringify({
      agents: {
        list: [
          { id: 'configured', workspace: '/vaults/configured' },
        ],
      },
    }, null, 2));

    const { isDefaultAgentWorkspacePath } = await loadModule();

    expect(isDefaultAgentWorkspacePath('main')).toBe(false);
    expect(isDefaultAgentWorkspacePath('configured')).toBe(false);
    expect(isDefaultAgentWorkspacePath('freshagent')).toBe(true);
  });

  it('returns workspaceRoot, memoryPath, and memoryDir together', async () => {
    const { resolveAgentWorkspace } = await loadModule();

    const workspace = resolveAgentWorkspace('research');

    expect(workspace.workspaceRoot).toBe(path.join(homeDir, '.openclaw', 'workspace-research'));
    expect(workspace.memoryPath).toBe(path.join(workspace.workspaceRoot, 'MEMORY.md'));
    expect(workspace.memoryDir).toBe(path.join(workspace.workspaceRoot, 'memory'));
  });

  it('rejects invalid agent ids', async () => {
    const { resolveAgentWorkspace } = await loadModule();

    expect(() => resolveAgentWorkspace('../oops')).toThrow(/agent id/i);
    expect(() => resolveAgentWorkspace('bad/name')).toThrow(/agent id/i);
    expect(() => resolveAgentWorkspace('two words')).toThrow(/agent id/i);
    expect(() => resolveAgentWorkspace('bad_agent')).toThrow(/agent id/i);
  });
});
