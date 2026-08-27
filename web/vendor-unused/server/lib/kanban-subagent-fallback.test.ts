/**
 * Tests for Kanban subagent launch helper.
 * @module
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  buildKanbanFallbackRunKey,
  launchKanbanFallbackSubagentViaRpc,
  resolveKanbanFallbackParentSessionKey,
} from './kanban-subagent-fallback.js';
import * as gatewayRpc from './gateway-rpc.js';

describe('launchKanbanFallbackSubagentViaRpc', () => {
  let calls: Array<{ method: string; params: Record<string, unknown> }>;

  beforeEach(() => {
    calls = [];
    vi.spyOn(gatewayRpc, 'gatewayRpcCall').mockImplementation(async (method, params) => {
      calls.push({ method, params });

      if (method === 'sessions.create') {
        return {
          ok: true,
          key: String(params.key),
          entry: {
            label: params.label,
            parentSessionKey: params.parentSessionKey,
          },
        };
      }
      if (method === 'sessions.send') {
        return { ok: true, runId: 'mock-run-id-12345' };
      }
      return {};
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates a real child session before sending the task', async () => {
    await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    });

    expect(calls.length).toBeGreaterThanOrEqual(2);
    expect(calls[0].method).toBe('sessions.create');
    expect(calls[1].method).toBe('sessions.send');
  });

  it('fails when the parent session is not a top-level root', async () => {
    await expect(launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:subagent:existing-child',
    })).rejects.toThrow('Parent agent session must be a top-level root');

    expect(calls).toHaveLength(0);
  });

  it('surfaces sessions.create failures from the gateway', async () => {
    vi.spyOn(gatewayRpc, 'gatewayRpcCall').mockImplementation(async (method, params) => {
      calls.push({ method, params });
      if (method === 'sessions.create') {
        throw new Error('Parent session not found: agent:reviewer:main');
      }
      return {};
    });

    await expect(launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    })).rejects.toThrow('Parent session not found');

    expect(calls.some((call) => call.method === 'sessions.send')).toBe(false);
  });

  it('creates the worker session under the requested parent root', async () => {
    await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    });

    const createCall = calls.find((c) => c.method === 'sessions.create');
    expect(createCall?.params.parentSessionKey).toBe('agent:reviewer:main');
    expect(createCall?.params.label).toBe('test-kanban-run');
    expect(createCall?.params.key).toMatch(/^agent:reviewer:subagent:/);
  });

  it('sends the raw task to the created child session with model/thinking preserved', async () => {
    await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
      model: 'openai-codex/gpt-5.4',
      thinking: 'high',
    });

    const createCall = calls.find((c) => c.method === 'sessions.create');
    const sendCall = calls.find((c) => c.method === 'sessions.send');

    expect(createCall?.params.model).toBe('openai-codex/gpt-5.4');
    expect(sendCall?.params.message).toBe('Execute kanban task');
    expect(sendCall?.params.thinking).toBe('high');
    expect(sendCall?.params.key).toBe(createCall?.params.key);
    expect(String(sendCall?.params.message)).not.toContain('[spawn-subagent]');
  });

  it('deletes the created child session when sessions.send fails after sessions.create', async () => {
    vi.spyOn(gatewayRpc, 'gatewayRpcCall').mockImplementation(async (method, params) => {
      calls.push({ method, params });

      if (method === 'sessions.create') {
        return {
          ok: true,
          key: String(params.key),
        };
      }
      if (method === 'sessions.send') {
        throw new Error('send failed');
      }
      if (method === 'sessions.delete') {
        return { ok: true };
      }
      return {};
    });

    await expect(launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    })).rejects.toThrow('send failed');

    const createCall = calls.find((c) => c.method === 'sessions.create');
    const deleteCall = calls.find((c) => c.method === 'sessions.delete');

    expect(createCall).toBeDefined();
    expect(deleteCall?.params).toEqual({
      key: createCall?.params.key,
      deleteTranscript: true,
    });
  });

  it('returns the deterministic run correlation key, child session key, and runId', async () => {
    const result = await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    });

    expect(result.sessionKey).toBe('kanban-root:test-kanban-run');
    expect(result.parentSessionKey).toBe('agent:reviewer:main');
    expect(result.childSessionKey).toMatch(/^agent:reviewer:subagent:/);
    expect(result.runId).toBe('mock-run-id-12345');
  });

  it('returns a compatibility snapshot containing the parent key', async () => {
    const result = await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    });

    expect(result.knownSessionKeysBefore).toEqual(['agent:reviewer:main']);
  });

  it('generates an idempotency key for sessions.send', async () => {
    await launchKanbanFallbackSubagentViaRpc({
      label: 'test-kanban-run',
      task: 'Execute kanban task',
      parentSessionKey: 'agent:reviewer:main',
    });

    const sendCall = calls.find((c) => c.method === 'sessions.send');
    expect(sendCall?.params.idempotencyKey).toBeDefined();
    expect(typeof sendCall?.params.idempotencyKey).toBe('string');
    expect((sendCall?.params.idempotencyKey as string).length).toBeGreaterThan(0);
  });
});

describe('buildKanbanFallbackRunKey', () => {
  it('returns a deterministic run correlation key derived from label', () => {
    expect(buildKanbanFallbackRunKey('test-kanban-run')).toBe('kanban-root:test-kanban-run');
  });
});

describe('resolveKanbanFallbackParentSessionKey', () => {
  it('maps an assignee agent id to its top-level root session', () => {
    expect(resolveKanbanFallbackParentSessionKey('agent:reviewer')).toBe('agent:reviewer:main');
  });

  it('normalizes full agent-flavored values back to the owning top-level root', () => {
    expect(resolveKanbanFallbackParentSessionKey('agent:reviewer:main')).toBe('agent:reviewer:main');
    expect(resolveKanbanFallbackParentSessionKey('agent:reviewer:subagent:child')).toBe('agent:reviewer:main');
  });

  it('rejects operator, unset, and @main assignees for macOS fallback execution', () => {
    expect(resolveKanbanFallbackParentSessionKey('operator')).toBeNull();
    expect(resolveKanbanFallbackParentSessionKey(undefined)).toBeNull();
    expect(resolveKanbanFallbackParentSessionKey('agent:main')).toBeNull();
  });
});
