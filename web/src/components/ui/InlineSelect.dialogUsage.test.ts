import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

/**
 * InlineSelect inside a Radix Dialog MUST pass `inline`.
 *
 * Without it the listbox renders through a sibling portal whose clicks the
 * dialog intercepts: the menu opens, the options are visible and look
 * perfectly normal, and no option can ever be chosen. It typechecks, it
 * renders, and it silently does nothing — which is how it reached the
 * operator's hands on the Agents page hire dialog (2026-07-25).
 *
 * A source walk rather than a render test on purpose, and rather than a count:
 * the failure mode is a NEW dialog forgetting the prop, so the guard has to
 * fail in the same commit that adds one. Same shape as the runtime→gateway
 * import ban and the proposal-creation seam walk on the Python side.
 */

const SRC = join(__dirname, '..', '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === 'node_modules') continue;
      walk(full, out);
    } else if (entry.endsWith('.tsx') && !entry.includes('.test.')) {
      out.push(full);
    }
  }
  return out;
}

/** Element opens only — `<InlineSelectOption[]>` is a generic, not a usage. */
const USAGE = /<InlineSelect(?![A-Za-z])/g;

describe('InlineSelect inside dialogs', () => {
  it('always passes `inline` when the file renders a Dialog', () => {
    const offenders: string[] = [];

    for (const file of walk(SRC)) {
      const source = readFileSync(file, 'utf8');
      if (!source.includes('components/ui/dialog')) continue;

      const usages = source.match(USAGE)?.length ?? 0;
      if (usages === 0) continue;

      // Count `inline` appearing as a bare JSX boolean prop on its own line —
      // how every existing call site writes it.
      const inlineProps = source.match(/^\s*inline\s*$/gm)?.length ?? 0;

      if (inlineProps < usages) {
        offenders.push(
          `${relative(SRC, file)}: ${usages} <InlineSelect> but ${inlineProps} \`inline\` prop(s)`,
        );
      }
    }

    expect(
      offenders,
      'InlineSelect inside a Dialog needs `inline`, or its options render but '
      + 'cannot be selected:\n' + offenders.join('\n'),
    ).toEqual([]);
  });

  it('finds the call sites it is meant to be guarding', () => {
    // A walk that matches nothing passes forever. Assert it actually sees the
    // dialogs that use the component.
    const seen = walk(SRC).filter((f) => {
      const s = readFileSync(f, 'utf8');
      return s.includes('components/ui/dialog') && USAGE.test(s);
    });
    expect(seen.length).toBeGreaterThanOrEqual(2);
  });
});
