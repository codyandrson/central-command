/**
 * GET /api/version — the PRODUCT version, from the repo-root VERSION file
 * (`version=<semver>`, the 2026-08-27 setup-update contract). The vendored
 * cockpit's package.json version is Nerve's, not Central Command's.
 */

import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// server-dist/routes -> ../../.. = the repo root.
const repoRoot = resolve(__dirname, '../../..');

function readProductVersion(): string {
  try {
    const raw = readFileSync(resolve(repoRoot, 'VERSION'), 'utf-8');
    const match = /^version=(.+)$/m.exec(raw);
    if (match) return match[1].trim();
  } catch {
    // fall through
  }
  return '0.0.0';
}

const app = new Hono();

app.get('/api/version', rateLimitGeneral, (c) =>
  c.json({ version: readProductVersion(), name: 'central-command' }));

export default app;
