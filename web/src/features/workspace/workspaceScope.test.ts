import { describe, expect, it } from 'vitest';
import {
  getWorkspaceAgentId,
  isPlaceholderWorkspaceAgent,
  getWorkspaceRootSessionKey,
  getWorkspaceStorageKey,
} from './workspaceScope';

describe('workspaceScope', () => {
  it('treats the no-session fallback id as a placeholder, never a real agent', () => {
    expect(isPlaceholderWorkspaceAgent(getWorkspaceAgentId(''))).toBe(true);
    expect(isPlaceholderWorkspaceAgent(getWorkspaceAgentId('weird-session-key'))).toBe(true);
    expect(isPlaceholderWorkspaceAgent(' ')).toBe(true);
    expect(isPlaceholderWorkspaceAgent(getWorkspaceAgentId('agent:jira-expert:sess_0123abcd'))).toBe(false);
    expect(isPlaceholderWorkspaceAgent('test')).toBe(false);
  });

  it('uses the top-level agent for a root session', () => {
    expect(getWorkspaceAgentId('agent:test:main')).toBe('test');
    expect(getWorkspaceRootSessionKey('agent:test:main')).toBe('agent:test:main');
  });

  it('uses the owning top-level agent for a subagent session', () => {
    expect(getWorkspaceAgentId('agent:test:subagent:abc')).toBe('test');
    expect(getWorkspaceRootSessionKey('agent:test:subagent:abc')).toBe('agent:test:main');
  });

  it('uses the owning top-level agent for a cron run session', () => {
    expect(getWorkspaceAgentId('agent:test:cron:daily:run:xyz')).toBe('test');
    expect(getWorkspaceRootSessionKey('agent:test:cron:daily:run:xyz')).toBe('agent:test:main');
  });

  it('uses the owning agent for direct message sessions', () => {
    expect(getWorkspaceAgentId('agent:test:telegram:direct:123')).toBe('test');
    expect(getWorkspaceRootSessionKey('agent:test:telegram:direct:123')).toBe('agent:test:main');
  });

  it('uses the owning agent for a Central Command conversation lane', () => {
    expect(getWorkspaceAgentId('agent:test:sess_0a1b2c3d')).toBe('test');
    expect(getWorkspaceRootSessionKey('agent:test:sess_0a1b2c3d')).toBe('agent:test:main');
  });

  it('falls back to main for unknown session keys', () => {
    expect(getWorkspaceAgentId('weird-session-key')).toBe('main');
    expect(getWorkspaceRootSessionKey('weird-session-key')).toBe('agent:main:main');
  });

  it('namespaces storage keys per agent', () => {
    expect(getWorkspaceStorageKey('open-files', 'test')).toBe('nerve:workspace:test:open-files');
  });
});
