import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import JSON5 from 'json5';
import { config } from './config.js';

interface OpenClawAgentEntry {
  id?: unknown;
  workspace?: unknown;
}

interface OpenClawConfigShape {
  agents?: {
    defaults?: {
      workspace?: unknown;
    };
    list?: OpenClawAgentEntry[];
  };
}

type CachedConfig = {
  path: string;
  mtimeMs: number;
  parsed: OpenClawConfigShape | null;
};

let cachedConfig: CachedConfig | null = null;

function getHomeDir(): string {
  return config.home || process.env.HOME || os.homedir();
}

export function resolveOpenClawConfigPath(): string {
  return process.env.OPENCLAW_CONFIG_PATH?.trim() || path.join(getHomeDir(), '.openclaw', 'openclaw.json');
}

function expandAndResolvePath(rawPath: string, configPath: string): string {
  const trimmed = rawPath.trim();
  if (!trimmed) return trimmed;

  const expanded = trimmed === '~'
    ? getHomeDir()
    : trimmed.startsWith('~/')
      ? path.join(getHomeDir(), trimmed.slice(2))
      : trimmed;

  if (path.isAbsolute(expanded)) return expanded;
  return path.resolve(path.dirname(configPath), expanded);
}

function loadOpenClawConfig(): { configPath: string; parsed: OpenClawConfigShape | null } {
  const configPath = resolveOpenClawConfigPath();

  try {
    const stat = fs.statSync(configPath);
    if (cachedConfig && cachedConfig.path === configPath && cachedConfig.mtimeMs === stat.mtimeMs) {
      return { configPath, parsed: cachedConfig.parsed };
    }

    const raw = fs.readFileSync(configPath, 'utf8');
    const parsed = JSON5.parse(raw) as OpenClawConfigShape;
    cachedConfig = { path: configPath, mtimeMs: stat.mtimeMs, parsed };
    return { configPath, parsed };
  } catch {
    cachedConfig = { path: configPath, mtimeMs: -1, parsed: null };
    return { configPath, parsed: null };
  }
}

export function getConfiguredAgentWorkspace(agentId: string): string | null {
  const { configPath, parsed } = loadOpenClawConfig();
  const agents = parsed?.agents?.list;
  if (!Array.isArray(agents)) return null;

  const match = agents.find((entry) => typeof entry?.id === 'string' && entry.id === agentId);
  if (!match || typeof match.workspace !== 'string' || !match.workspace.trim()) return null;

  return expandAndResolvePath(match.workspace, configPath);
}

export function getDefaultAgentWorkspaceRoot(): string | null {
  const { configPath, parsed } = loadOpenClawConfig();
  const rawWorkspace = parsed?.agents?.defaults?.workspace;
  if (typeof rawWorkspace !== 'string' || !rawWorkspace.trim()) return null;
  return expandAndResolvePath(rawWorkspace, configPath);
}

export function buildDefaultAgentWorkspacePath(agentId: string): string {
  const defaultWorkspaceRoot = getDefaultAgentWorkspaceRoot();
  if (defaultWorkspaceRoot) {
    return path.join(defaultWorkspaceRoot, agentId);
  }
  return path.join(getHomeDir(), '.openclaw', `workspace-${agentId}`);
}

export function listConfiguredAgentWorkspaces(): Array<{ agentId: string; workspaceRoot: string }> {
  const { configPath, parsed } = loadOpenClawConfig();
  const agents = parsed?.agents?.list;
  if (!Array.isArray(agents)) return [];

  const seen = new Set<string>();
  const workspaces: Array<{ agentId: string; workspaceRoot: string }> = [];

  for (const entry of agents) {
    if (typeof entry?.id !== 'string' || !entry.id.trim()) continue;
    if (typeof entry.workspace !== 'string' || !entry.workspace.trim()) continue;
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    workspaces.push({
      agentId: entry.id,
      workspaceRoot: expandAndResolvePath(entry.workspace, configPath),
    });
  }

  return workspaces;
}

