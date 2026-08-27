/** Tests for root workspace watcher discovery. */
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

type WatchCallback = (eventType: string, filename: string | Buffer | null) => void;
type WatchRecord = { target: string; callback: WatchCallback; close: ReturnType<typeof vi.fn> };

const runtime = vi.hoisted(() => ({
  configPath: '/tmp/home/.openclaw/openclaw.json',
  existing: new Set<string>(),
  watched: [] as WatchRecord[],
  listConfiguredAgentWorkspaces: vi.fn(() => [] as Array<{ agentId: string; workspaceRoot: string }>),
}));

vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>();
  const mock = {
    ...actual,
    existsSync: (target: actual.PathLike) => runtime.existing.has(String(target)),
    readdirSync: vi.fn(() => []),
    watch: ((target: actual.PathLike, optionsOrListener: unknown, maybeListener?: unknown) => {
      const callback = (typeof optionsOrListener === 'function' ? optionsOrListener : maybeListener) as WatchCallback;
      const close = vi.fn();
      runtime.watched.push({ target: String(target), callback, close });
      return { close } as unknown as actual.FSWatcher;
    }) satisfies typeof actual.watch,
  };
  return { ...mock, default: mock };
});

vi.mock('../routes/events.js', () => ({
  broadcast: vi.fn(),
}));

vi.mock('./config.js', () => ({
  config: {
    home: '/tmp/home',
    workspaceWatchRecursive: false,
    memoryPath: '/tmp/home/workspace/MEMORY.md',
    memoryDir: '/tmp/home/workspace/memory',
  },
}));

vi.mock('./agent-workspace.js', () => ({
  resolveAgentWorkspace: (agentId?: string) => {
    const normalized = !agentId || agentId === 'main' ? 'main' : agentId;
    const workspaceRoot = normalized === 'main'
      ? '/tmp/home/workspace'
      : path.join('/tmp/home', `workspace-${normalized}`);
    return {
      agentId: normalized,
      workspaceRoot,
      memoryPath: path.join(workspaceRoot, 'MEMORY.md'),
      memoryDir: path.join(workspaceRoot, 'memory'),
    };
  },
}));

vi.mock('./file-utils.js', () => ({
  isBinary: () => false,
  isExcluded: () => false,
}));

vi.mock('./openclaw-config.js', () => ({
  listConfiguredAgentWorkspaces: (...args: unknown[]) => runtime.listConfiguredAgentWorkspaces(...args),
  resolveOpenClawConfigPath: () => runtime.configPath,
}));

vi.mock('./workspace-detect.js', () => ({
  isWorkspaceLocal: vi.fn(async () => true),
}));

async function loadWatcherModule() {
  vi.resetModules();
  return import('./file-watcher.js');
}

afterEach(async () => {
  const mod = await loadWatcherModule();
  mod.stopFileWatcher();
  runtime.watched = [];
  runtime.existing.clear();
  runtime.listConfiguredAgentWorkspaces.mockReset();
  runtime.listConfiguredAgentWorkspaces.mockReturnValue([]);
  vi.clearAllMocks();
});

describe('startFileWatcher', () => {
  it('watches the custom config directory when OPENCLAW_CONFIG_PATH points outside ~/.openclaw', async () => {
    runtime.configPath = '/tmp/custom-configs/nerve-openclaw.json';
    runtime.existing = new Set(['/tmp/home/.openclaw', '/tmp/custom-configs']);

    const mod = await loadWatcherModule();
    await mod.startFileWatcher();

    expect(runtime.watched.map((entry) => entry.target).sort()).toEqual([
      '/tmp/custom-configs',
      '/tmp/home/.openclaw',
    ]);

    const initialCalls = runtime.listConfiguredAgentWorkspaces.mock.calls.length;
    runtime.watched.find((entry) => entry.target === '/tmp/custom-configs')
      ?.callback('rename', 'nerve-openclaw.json');

    expect(runtime.listConfiguredAgentWorkspaces.mock.calls.length).toBeGreaterThan(initialCalls);
  });

  it('refreshes when a custom config basename changes inside ~/.openclaw', async () => {
    runtime.configPath = '/tmp/home/.openclaw/custom-openclaw.json';
    runtime.existing = new Set(['/tmp/home/.openclaw']);

    const mod = await loadWatcherModule();
    await mod.startFileWatcher();

    expect(runtime.watched.map((entry) => entry.target)).toEqual(['/tmp/home/.openclaw']);

    const initialCalls = runtime.listConfiguredAgentWorkspaces.mock.calls.length;
    runtime.watched[0]?.callback('rename', 'custom-openclaw.json');

    expect(runtime.listConfiguredAgentWorkspaces.mock.calls.length).toBeGreaterThan(initialCalls);
  });

  it('still refreshes when legacy workspace-* directories change', async () => {
    runtime.configPath = '/tmp/custom-configs/nerve-openclaw.json';
    runtime.existing = new Set(['/tmp/home/.openclaw', '/tmp/custom-configs']);

    const mod = await loadWatcherModule();
    await mod.startFileWatcher();

    const initialCalls = runtime.listConfiguredAgentWorkspaces.mock.calls.length;
    runtime.watched.find((entry) => entry.target === '/tmp/home/.openclaw')
      ?.callback('rename', 'workspace-research');

    expect(runtime.listConfiguredAgentWorkspaces.mock.calls.length).toBeGreaterThan(initialCalls);
  });
});
