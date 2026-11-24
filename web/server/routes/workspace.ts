/**
 * Workspace file API Routes
 *
 * GET  /api/workspace/:key  — Read a workspace file by key
 * PUT  /api/workspace/:key  — Write a workspace file by key
 *
 * Strict allowlist of keys → files. No directory traversal. A workspace
 * directory that is not on this host's filesystem has no files here.
 */

import { Hono, type Context } from 'hono';
import fs from 'node:fs/promises';
import path from 'node:path';
import { readText } from '../lib/files.js';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { InvalidAgentIdError, isDefaultAgentWorkspacePath, resolveAgentWorkspace } from '../lib/agent-workspace.js';
import { isWorkspaceLocal } from '../lib/workspace-detect.js';
import { createChatPathLinksTemplate } from '../lib/chat-path-links-config.js';

const app = new Hono();

/** Strict allowlist mapping key → filename */
const FILE_MAP: Record<string, string> = {
  soul: 'SOUL.md',
  tools: 'TOOLS.md',
  user: 'USER.md',
  agents: 'AGENTS.md',
  heartbeat: 'HEARTBEAT.md',
  chatPathLinks: 'CHAT_PATH_LINKS.json',
};

function getWorkspaceRoot(agentId?: string): { agentId: string; workspaceRoot: string; isDefaultPath: boolean } {
  const workspace = resolveAgentWorkspace(agentId);
  return { agentId: workspace.agentId, workspaceRoot: workspace.workspaceRoot, isDefaultPath: isDefaultAgentWorkspacePath(agentId) };
}

function handleAgentWorkspaceError(c: Context, err: unknown) {
  if (err instanceof InvalidAgentIdError) {
    return c.json({ ok: false, error: err.message }, 400);
  }
  const message = err instanceof Error ? err.message : 'Invalid workspace request';
  return c.json({ ok: false, error: message }, 500);
}

app.get('/api/workspace/:key', rateLimitGeneral, async (c) => {
  let workspace: ReturnType<typeof getWorkspaceRoot>;
  try {
    workspace = getWorkspaceRoot(c.req.query('agentId'));
  } catch (err) {
    return handleAgentWorkspaceError(c, err);
  }

  const key = c.req.param('key') ?? '';
  const filename = FILE_MAP[key];
  if (!filename) return c.json({ ok: false, error: 'Unknown file key' }, 400);

  const filePath = path.join(workspace.workspaceRoot, filename);
  const workspaceIsLocal = await isWorkspaceLocal(workspace.workspaceRoot, { ensureDefault: workspace.isDefaultPath });

  if (workspaceIsLocal) {
    try {
      await fs.access(filePath);
      const content = await readText(filePath);
      return c.json({ ok: true, content });
    } catch (err) {
      const isEnoent = (err as NodeJS.ErrnoException | undefined)?.code === 'ENOENT';
      const isMissingChatPathLinks = key === 'chatPathLinks' && isEnoent;

      if (isMissingChatPathLinks) {
        const content = createChatPathLinksTemplate({
          platform: process.platform,
          homeDir: process.env.HOME,
          username: process.env.USER ?? process.env.LOGNAME,
          workspaceRoot: workspace.workspaceRoot,
        });

        try {
          await fs.mkdir(path.dirname(filePath), { recursive: true });
          await fs.writeFile(filePath, content, 'utf-8');
          console.warn(`[workspace] Missing ${filename}; regenerated local default template at ${filePath}`);
          return c.json({ ok: true, content });
        } catch (writeErr) {
          console.warn('[workspace] Failed to regenerate local chat path links config:', (writeErr as Error).message);
        }
      }
    }
  }

  // chatPathLinks is client DISPLAY config with a built-in default, and its
  // usual caller is `agentId=main` — the current-lane alias, which owns no
  // workspace directory here, so every page load 404'd. Empty content is what
  // the caller already reads as "use the defaults"; no other key gets this.
  if (key === 'chatPathLinks') return c.json({ ok: true, content: '' });

  return c.json({ ok: false, error: 'File not found' }, 404);
});

app.put('/api/workspace/:key', rateLimitGeneral, async (c) => {
  let body: { content?: string; agentId?: string };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ ok: false, error: 'Invalid JSON body' }, 400);
  }

  let workspace: ReturnType<typeof getWorkspaceRoot>;
  try {
    workspace = getWorkspaceRoot(body.agentId ?? c.req.query('agentId'));
  } catch (err) {
    return handleAgentWorkspaceError(c, err);
  }

  const key = c.req.param('key') ?? '';
  const filename = FILE_MAP[key];
  if (!filename) return c.json({ ok: false, error: 'Unknown file key' }, 400);

  if (typeof body.content !== 'string') {
    return c.json({ ok: false, error: 'Missing content field' }, 400);
  }
  if (body.content.length > 100_000) {
    return c.json({ ok: false, error: 'Content too large (max 100KB)' }, 400);
  }

  const filePath = path.join(workspace.workspaceRoot, filename);

  if (!(await isWorkspaceLocal(workspace.workspaceRoot))) {
    console.error(`[workspace] PUT refused: workspace ${workspace.workspaceRoot} is not on this host`);
    return c.json({ ok: false, error: 'Failed to write file' }, 500);
  }
  try {
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, body.content, 'utf-8');
    return c.json({ ok: true });
  } catch (err) {
    console.error('[workspace] PUT local error:', (err as Error).message);
    return c.json({ ok: false, error: 'Failed to write file' }, 500);
  }
});

/** List available workspace file keys and their existence status */
app.get('/api/workspace', rateLimitGeneral, async (c) => {
  let workspace: ReturnType<typeof getWorkspaceRoot>;
  try {
    workspace = getWorkspaceRoot(c.req.query('agentId'));
  } catch (err) {
    return handleAgentWorkspaceError(c, err);
  }

  const files: Array<{ key: string; filename: string; exists: boolean }> = [];
  const isLocal = await isWorkspaceLocal(workspace.workspaceRoot);

  for (const [key, filename] of Object.entries(FILE_MAP)) {
    let exists = false;
    if (isLocal) {
      try {
        await fs.access(path.join(workspace.workspaceRoot, filename));
        exists = true;
      } catch {
        // not found
      }
    }
    files.push({ key, filename, exists });
  }

  return c.json({ ok: true, files, ...(isLocal ? {} : { remoteWorkspace: true }) });
});

export default app;
