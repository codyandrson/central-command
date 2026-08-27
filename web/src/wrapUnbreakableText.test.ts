/**
 * A source walk, not a render test: every place the cockpit renders
 * agent-authored text with `whitespace-pre-wrap` must also carry
 * `cockpit-wrap` (overflow-wrap: anywhere), or an unbreakable token —
 * a box-drawing rule, a URL, a hash — overflows to the right and the
 * container's end padding puts the tail out of scroll reach.
 * Reported 2026-08-01 against the charter panel.
 *
 * `.msg-body` already sets the same two properties, so elements carrying that
 * class are exempt.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...tsxFiles(p));
    else if (e.name.endsWith('.tsx') && !e.name.includes('.test.')) out.push(p);
  }
  return out;
}

describe('unbreakable agent text wraps', () => {
  it('pairs whitespace-pre-wrap with cockpit-wrap (or .msg-body)', () => {
    const offenders: string[] = [];
    for (const file of tsxFiles(join(__dirname, 'features'))) {
      readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
        if (!line.includes('whitespace-pre-wrap')) return;
        if (line.includes('cockpit-wrap') || line.includes('msg-body')) return;
        if (line.includes('break-all') || line.includes('overflow-wrap:anywhere')) return;
        offenders.push(`${file}:${i + 1}`);
      });
    }
    expect(offenders).toEqual([]);
  });

  it('pairs truncate with min-w-0 inside flex-wrap disclosure-title rows', () => {
    // A `truncate` span nested in a `flex-wrap` row never actually clips: the
    // browser gives it its full intrinsic width before wrapping decides
    // anything, so the row just overflows instead. `min-w-0` (or the
    // `wrap-anywhere` swap used on these titles) is what lets it shrink.
    const offenders: string[] = [];
    for (const file of tsxFiles(join(__dirname, 'features'))) {
      const lines = readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        if (!line.includes('flex-wrap')) return;
        for (let j = i; j < Math.min(i + 8, lines.length); j++) {
          const l = lines[j];
          if (l.includes('truncate') && !l.includes('min-w-0')) {
            offenders.push(`${file}:${j + 1}`);
          }
          if (l.trim() === '</span>' && j > i) break;
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
