/**
 * Workspace locality detection.
 *
 * Determines whether the agent workspace directory exists on the local
 * filesystem. When it doesn't (e.g. Nerve running on DGX host while the
 * workspace lives inside an OpenShell sandbox), route handlers fall back
 * to gateway RPC for file access.
 *
 * The result is cached with a short TTL to avoid probing the filesystem
 * on every HTTP request. Setting `NERVE_WORKSPACE_REMOTE=true` forces
 * gateway-only mode unconditionally.
 * @module
 */

import { access, mkdir } from 'node:fs/promises';
import { config } from './config.js';

const CACHE_TTL_MS = 60_000; // 60 seconds

interface CacheEntry {
  isLocal: boolean;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry>();

/**
 * Check whether the workspace root directory exists locally.
 *
 * Returns `true` when the directory is accessible on the local filesystem,
 * `false` when it isn't (or when `NERVE_WORKSPACE_REMOTE=true` is set).
 *
 * When `ensureDefault` is set and the directory is missing, it is created
 * (`mkdir -p`) and treated as local. Only pass this for a workspace root
 * that is a *computed default* path (no explicit `openclaw.json` entry) —
 * an explicitly-configured-but-missing path should stay a visible error,
 * not get silently created.
 */
export async function isWorkspaceLocal(
  workspaceRoot: string,
  options?: { ensureDefault?: boolean },
): Promise<boolean> {
  // Env override — always treat as remote
  if (config.workspaceRemote) return false;

  const now = Date.now();
  const cached = cache.get(workspaceRoot);
  if (cached && now < cached.expiresAt) {
    return cached.isLocal;
  }

  let isLocal: boolean;
  try {
    await access(workspaceRoot);
    isLocal = true;
  } catch {
    if (options?.ensureDefault) {
      try {
        await mkdir(workspaceRoot, { recursive: true });
        isLocal = true;
      } catch {
        isLocal = false;
      }
    } else {
      isLocal = false;
    }
  }

  cache.set(workspaceRoot, { isLocal, expiresAt: now + CACHE_TTL_MS });
  return isLocal;
}

/** Clear the detection cache (useful for tests). */
export function clearWorkspaceDetectCache(): void {
  cache.clear();
}
