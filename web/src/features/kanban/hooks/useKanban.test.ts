/**
 * The board must default to hiding stale finished work so the columns don't
 * accumulate forever, but never lose it — a persisted toggle (same pattern
 * as SessionList's history filter) brings it back. Also covers the assignee
 * filter flowing through to the server query string.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { KanbanTask } from '../types';
import { useKanban } from './useKanban';

const HOUR = 3_600_000;

function task(partial: Partial<KanbanTask> & { id: string }): KanbanTask {
  return {
    title: partial.id, description: '', status: 'IN_PROGRESS', priority: 'normal',
    createdBy: 'operator', createdAt: 0, updatedAt: Date.now(), version: 1, labels: [],
    columnOrder: 0, feedback: [], ...partial,
  };
}

function mockTasks(items: KanbanTask[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (String(url).includes('/api/kanban/config')) {
      return { ok: false } as Response;
    }
    return {
      ok: true,
      json: async () => ({ items, total: items.length, limit: 200, offset: 0, hasMore: false }),
    } as Response;
  }));
}

describe('useKanban old-terminal filter', () => {
  beforeEach(() => {
    localStorage.removeItem('cc-kanban-show-old-terminal');
  });

  it('hides terminal tasks older than 24h by default and shows them via the toggle', async () => {
    const tasks = [
      task({ id: 'fresh', assignee: 'agent:alice', status: 'IN_PROGRESS', updatedAt: Date.now() }),
      task({ id: 'old-done', assignee: 'agent:bob', status: 'DONE', updatedAt: Date.now() - 25 * HOUR }),
    ];
    mockTasks(tasks);

    const { result } = renderHook(() => useKanban());
    await waitFor(() => expect(result.current.tasks.map(t => t.id)).toEqual(['fresh']));
    expect(result.current.oldTerminalHiddenCount).toBe(1);

    act(() => result.current.setShowOldTerminal(true));
    await waitFor(() => expect(result.current.tasks.map(t => t.id).sort())
      .toEqual(['fresh', 'old-done']));
  });

  it('never hides a terminal task within the last 24h', async () => {
    const tasks = [
      task({ id: 'recent-done', status: 'DONE', updatedAt: Date.now() - HOUR }),
    ];
    mockTasks(tasks);

    const { result } = renderHook(() => useKanban());
    await waitFor(() => expect(result.current.tasks.map(t => t.id)).toEqual(['recent-done']));
    expect(result.current.oldTerminalHiddenCount).toBe(0);
  });

  it('the assignee filter narrows the fetched task list via the query string', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes('/api/kanban/config')) return { ok: false } as Response;
      const isAlice = String(url).includes('assignee=agent%3Aalice');
      const items = isAlice ? [task({ id: 't1', assignee: 'agent:alice', updatedAt: Date.now() })] : [
        task({ id: 't1', assignee: 'agent:alice', updatedAt: Date.now() }),
        task({ id: 't2', assignee: 'agent:bob', updatedAt: Date.now() }),
      ];
      return { ok: true, json: async () => ({ items, total: items.length, limit: 200, offset: 0, hasMore: false }) } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useKanban());
    await waitFor(() => expect(result.current.tasks.length).toBe(2));

    act(() => result.current.setFilters({ ...result.current.filters, assignee: 'agent:alice' }));
    await waitFor(() => expect(result.current.tasks.map(t => t.id)).toEqual(['t1']));
  });
});
