/**
 * The cockpit must never display an assumed context-window limit. A missing
 * `contextTokens` used to fall back to a hardcoded 200000/200_000 in several
 * components — a fabricated number the operator would read as real. The fix
 * is to treat a missing/zero limit as unknown ("<used> / ?") everywhere.
 *
 * This guards the regression: a hardcoded context-limit fallback (`200000`
 * or `200_000` used as a `|| ` / `??` denominator) reappearing anywhere in
 * web/src.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** A hardcoded 200000/200_000 used as a fallback limit, e.g. `x || 200000` or `x ?? 200_000`. */
const HARDCODED_LIMIT_FALLBACK = /(\|\||\?\?)\s*200_?000\b/;

describe('context limit fallback', () => {
  it('never falls back to a hardcoded 200000/200_000 context limit', () => {
    const offenders = sourceFiles(join(__dirname, '..'))
      .flatMap((file) =>
        readFileSync(file, 'utf8')
          .split('\n')
          .map((line, i) => ({ file, line: i + 1, text: line.trim() }))
          .filter(({ text }) => HARDCODED_LIMIT_FALLBACK.test(text)),
      )
      .map(({ file, line, text }) => `${file}:${line}\n    ${text}`);

    expect(
      offenders,
      `an unknown context limit must render as unknown ("<used> / ?"), never a fabricated number:\n${offenders.join('\n')}`,
    ).toEqual([]);
  });
});
