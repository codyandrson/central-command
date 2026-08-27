/**
 * A container query resolves against an ANCESTOR container, never the element's
 * own — so `@container` and `@<bp>:` on the SAME className is always a bug, and
 * a silent one: the element's own variants never apply while its CHILDREN's do.
 *
 * That asymmetry is what made it survive. AgentsView, SkillsView and
 * DecisionsView all carried `@container flex … flex-col @3xl:flex-row` on one
 * div, so the sidebar took its `@3xl:w-[340px]` and stayed STACKED anyway. With
 * a short list the detail pane merely sat below and still had height; once the
 * roster reached 13 agents the aside overflowed the column and `flex-1` gave the
 * pane height 0 at top 1360 — a blank panel, no error anywhere (2026-08-10).
 *
 * jsdom has no layout engine, so no render test can catch this. The source is
 * the only place it is visible, which is why this guard reads the source.
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

/** `@container[/name]` and a breakpoint variant like `@3xl:` in one class list. */
const SELF_QUERY = /className=\{?["'`][^"'`]*@container[^"'`]*\s@[0-9a-z]+(?:\/[a-z-]+)?:[^"'`]*["'`]/;

describe('container queries', () => {
  it('never declares @container on the element that also uses @<bp>: variants', () => {
    const offenders = sourceFiles(join(__dirname, '..'))
      .flatMap((file) =>
        readFileSync(file, 'utf8')
          .split('\n')
          .map((line, i) => ({ file, line: i + 1, text: line.trim() }))
          .filter(({ text }) => SELF_QUERY.test(text)),
      )
      .map(({ file, line, text }) => `${file}:${line}\n    ${text}`);

    expect(offenders, `put @container on a PARENT of these elements:\n${offenders.join('\n')}`)
      .toEqual([]);
  });
});
