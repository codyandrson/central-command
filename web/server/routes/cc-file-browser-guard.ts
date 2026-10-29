/**
 * Central Command's read-only guard for the vendored file browser
 * (R18 / COCKPIT-028).
 *
 * `routes/file-browser.ts` is mounted whatever the UI flag says, and its
 * mutations (write, rename, move, trash, restore, and the gateway files-set
 * fallback inside write) have no off switch upstream. This middleware answers
 * every non-read request under /api/files/ with 403 unless FILE_BROWSER_WRITABLE=1.
 * The GETs (tree, raw, resolve, read) stay open. Agent-scoped workspaces are
 * served by the same routes, so they sit under the same guard.
 * @module
 */

import type { MiddlewareHandler } from 'hono';
import { config } from '../lib/config.js';

export const FILE_BROWSER_READ_ONLY_ERROR = 'the file browser is read-only';

const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export const fileBrowserGuard: MiddlewareHandler = async (c, next) => {
  if (READ_METHODS.has(c.req.method) || config.fileBrowserWritable) return next();
  return c.json({ ok: false, error: FILE_BROWSER_READ_ONLY_ERROR }, 403);
};
