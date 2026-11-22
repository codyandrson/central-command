import { describe, expect, it } from 'vitest';
import { formatPlanAddToChat, formatWorkspacePathAddToChat, mergeAddToChatText } from './addToChat';

describe('addToChat helpers', () => {
  it('formats plan context with source, title, and path', () => {
    expect(formatPlanAddToChat({
      source: 'Gambit OpenClaw Nerve',
      title: 'Nerve mobile polish',
      path: '.plans/2026-03-15-mobile-plans-back-button-and-add-to-chat.md',
    })).toBe('Plan context:\n- Source: Gambit OpenClaw Nerve\n- Title: Nerve mobile polish\n- Path: .plans/2026-03-15-mobile-plans-back-button-and-add-to-chat.md');
  });

  it('omits blank source lines when no source is available', () => {
    expect(formatPlanAddToChat({
      source: '   ',
      title: 'Implement Add to Chat',
      path: '.plans/add-to-chat.md',
    })).toBe('Plan context:\n- Title: Implement Add to Chat\n- Path: .plans/add-to-chat.md');
  });

  it('formats workspace path context for directories', () => {
    expect(formatWorkspacePathAddToChat({
      source: 'Workspace',
      kind: 'directory',
      path: 'src/features/chat',
    })).toBe('Workspace context:\n- Source: Workspace\n- Kind: directory\n- Path: src/features/chat');
  });

  it('appends add-to-chat context after an existing draft', () => {
    expect(mergeAddToChatText('Please help with this next.', 'Plan context:\n- Title: Test\n- Path: .plans/test.md'))
      .toBe('Please help with this next.\n\nPlan context:\n- Title: Test\n- Path: .plans/test.md');
  });
});
