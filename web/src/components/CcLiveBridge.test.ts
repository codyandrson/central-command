/**
 * Every push that means "this session's transcript grew or closed" must reload
 * the watched pane. The post-decision resume emits ONLY conversation.continued
 * (no session.step — `durable.resume` drives `agent.run()` directly), so the
 * first live rescope run (2026-09-02) landed 8 moves and a verified summary
 * that the cockpit never showed: the pane stopped at the proposal turn and
 * read as stuck. Source-level: the allowlist is a string comparison chain, and
 * a render test would only prove its own fixture.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const src = readFileSync(join(__dirname, 'CcLiveBridge.tsx'), 'utf8');

describe('CcLiveBridge reload triggers', () => {
  it.each([
    'cc.session.step',
    'cc.session.completed',
    'cc.conversation.ended',
    'cc.conversation.continued',
  ])('reloads on %s', (evt) => {
    expect(src).toContain(`msg.event !== '${evt}'`);
  });
});
