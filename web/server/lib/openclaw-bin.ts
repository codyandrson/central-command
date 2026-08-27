/**
 * Resolve the `openclaw` binary path.
 *
 * Checks (in order):
 *  1. `OPENCLAW_BIN` env var (explicit override)
 *  2. Sibling of current Node binary (nvm, fnm, volta)
 *  3. Common system paths (`/opt/homebrew/bin`, `/usr/local/bin`, etc.)
 *  4. Falls back to bare `'openclaw'` (relies on `PATH`)
 *
 * Result is cached after the first call.
 * @module
 */

import { accessSync, constants } from 'node:fs';
import { homedir } from 'node:os';

let cached: string | null = null;

export function resolveOpenclawBin(): string {
  if (cached) return cached;
  if (process.env.OPENCLAW_BIN) { cached = process.env.OPENCLAW_BIN; return cached; }

  const home = process.env.HOME || homedir();
  const nodeBin = process.execPath.replace(/\/node$/, '');
  const candidates = [
    `${nodeBin}/openclaw`,                             // same dir as current node (nvm, fnm, volta)
    '/opt/homebrew/bin/openclaw',                      // macOS Apple Silicon (Homebrew)
    '/usr/local/bin/openclaw',                         // macOS Intel (Homebrew) / global npm
    '/usr/bin/openclaw',                               // system package (Linux)
    `${home}/.npm-global/bin/openclaw`,                // custom npm prefix (npm set prefix)
    `${home}/.local/bin/openclaw`,                     // pip-style local bin
    `${home}/.volta/bin/openclaw`,                     // volta
    `${home}/.fnm/aliases/default/bin/openclaw`,       // fnm
  ];

  for (const c of candidates) {
    try { accessSync(c, constants.X_OK); cached = c; return cached; } catch { /* next */ }
  }

  console.warn('[openclaw-bin] Could not find openclaw binary. Checked:', candidates.join(', '),
    '— Set OPENCLAW_BIN env var to fix. Falling back to bare "openclaw" (requires PATH).');
  cached = 'openclaw';
  return cached;
}
