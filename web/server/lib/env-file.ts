/**
 * Safe .env upsert helper with per-file mutex serialization.
 *
 * Prevents concurrent read-modify-write races and preserves existing line endings.
 */

import fs from 'node:fs';
import path from 'node:path';
import { withMutex } from './mutex.js';

const DEFAULT_ENV_PATH = path.resolve(process.cwd(), '.env');
const ENV_KEY_RE = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/;

function detectLineEnding(content: string): '\n' | '\r\n' {
  return content.includes('\r\n') ? '\r\n' : '\n';
}

/** Pure content upsert helper (used by file writer + tests). */
export function upsertEnvContent(content: string, key: string, value: string): string {
  const envKey = key.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(envKey)) {
    throw new Error(`Invalid env key: ${key}`);
  }
  if (/[\r\n]/.test(value)) {
    throw new Error(`Invalid env value for ${envKey}: multi-line values are not supported`);
  }

  const eol = detectLineEnding(content);
  const lines = content.length > 0 ? content.split(/\r?\n/) : [];

  // split(/\r?\n/) leaves a trailing empty entry when file ends with newline.
  if (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }

  let found = false;
  const updated = lines.map((line) => {
    const match = line.match(ENV_KEY_RE);
    if (!match) return line;

    if (match[1] === envKey) {
      found = true;
      return `${envKey}=${value}`;
    }

    return line;
  });

  if (!found) {
    updated.push(`${envKey}=${value}`);
  }

  return `${updated.join(eol)}${eol}`;
}

/**
 * Upsert a key/value into an env file with a keyed mutex for race-free writes.
 */
export async function writeEnvKey(
  key: string,
  value: string,
  envPath = DEFAULT_ENV_PATH,
): Promise<void> {
  const mutexKey = `env-file:${envPath}`;

  await withMutex(mutexKey, async () => {
    let content = '';
    try {
      content = fs.readFileSync(envPath, 'utf8');
    } catch {
      // File may not exist yet — we'll create it.
    }

    const next = upsertEnvContent(content, key, value);
    fs.mkdirSync(path.dirname(envPath), { recursive: true });
    fs.writeFileSync(envPath, next, 'utf8');
  });
}
