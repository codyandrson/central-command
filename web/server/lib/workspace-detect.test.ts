/** Tests for workspace local/remote detection. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

describe('workspace-detect', () => {
  let tmpDir: string;

  beforeEach(async () => {
    vi.resetModules();
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'ws-detect-test-'));
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  async function loadModule(workspaceRemote = false) {
    vi.doMock('./config.js', () => ({
      config: { workspaceRemote },
    }));
    const mod = await import('./workspace-detect.js');
    mod.clearWorkspaceDetectCache();
    return mod;
  }

  it('returns true when workspace directory exists', async () => {
    const { isWorkspaceLocal } = await loadModule();
    const workspaceRoot = path.join(tmpDir, 'workspace');
    await fs.mkdir(workspaceRoot);

    const result = await isWorkspaceLocal(workspaceRoot);
    expect(result).toBe(true);
  });

  it('returns false when workspace directory does not exist', async () => {
    const { isWorkspaceLocal } = await loadModule();
    const workspaceRoot = path.join(tmpDir, 'nonexistent');

    const result = await isWorkspaceLocal(workspaceRoot);
    expect(result).toBe(false);
  });

  it('returns false when NERVE_WORKSPACE_REMOTE is true', async () => {
    const { isWorkspaceLocal } = await loadModule(true);
    const workspaceRoot = path.join(tmpDir, 'workspace');
    await fs.mkdir(workspaceRoot);

    const result = await isWorkspaceLocal(workspaceRoot);
    expect(result).toBe(false);
  });

  it('caches results within TTL', async () => {
    const { isWorkspaceLocal } = await loadModule();
    const workspaceRoot = path.join(tmpDir, 'workspace');
    await fs.mkdir(workspaceRoot);

    // First call — should check filesystem
    expect(await isWorkspaceLocal(workspaceRoot)).toBe(true);

    // Remove the directory
    await fs.rm(workspaceRoot, { recursive: true });

    // Second call — should use cached result (still true)
    expect(await isWorkspaceLocal(workspaceRoot)).toBe(true);
  });

  it('clearWorkspaceDetectCache resets cached values', async () => {
    const { isWorkspaceLocal, clearWorkspaceDetectCache } = await loadModule();
    const workspaceRoot = path.join(tmpDir, 'workspace');
    await fs.mkdir(workspaceRoot);

    expect(await isWorkspaceLocal(workspaceRoot)).toBe(true);
    await fs.rm(workspaceRoot, { recursive: true });

    // Clear cache and re-check
    clearWorkspaceDetectCache();
    expect(await isWorkspaceLocal(workspaceRoot)).toBe(false);
  });
});
