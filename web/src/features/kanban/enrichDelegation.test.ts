import { describe, it, expect } from 'vitest';
import { boardColumnFor, enrichDelegation, type KanbanTask } from './types';

function task(partial: Partial<KanbanTask> & { id: string }): KanbanTask {
  return {
    title: partial.id, description: '', status: 'IN_PROGRESS', priority: 'normal',
    createdBy: 'operator', createdAt: 0, updatedAt: 0, version: 1, labels: [],
    columnOrder: 0, feedback: [], ...partial,
  };
}

describe('enrichDelegation (D7 phase 2 project ↔ sub-task linkage)', () => {
  it('marks a project with its sub-task roster + settled progress', () => {
    const tasks = [
      task({ id: 'proj', title: 'Board hygiene', assignee: 'agent:orchestrator', sessionId: 'sess_orch' }),
      task({ id: 'c1', title: 'Review TASKS-12', assignee: 'agent:jira-expert', status: 'DONE', parentSessionId: 'sess_orch' }),
      task({ id: 'c2', title: 'Record findings', assignee: 'agent:jira-expert', status: 'REVIEW', parentSessionId: 'sess_orch' }),
    ];
    const enriched = enrichDelegation(tasks);
    const proj = enriched.find((t) => t.id === 'proj')!;
    expect(proj.delegation?.role).toBe('project');
    expect(proj.delegation?.childCount).toBe(2);
    expect(proj.delegation?.childDone).toBe(1); // only c1 is terminal
    expect(proj.delegation?.children?.map((c) => c.id)).toEqual(['c1', 'c2']);
  });

  it('marks a sub-task with the project it belongs to', () => {
    const tasks = [
      task({ id: 'proj', title: 'Board hygiene', sessionId: 'sess_orch' }),
      task({ id: 'c1', assignee: 'agent:jira-expert', parentSessionId: 'sess_orch' }),
    ];
    const child = enrichDelegation(tasks).find((t) => t.id === 'c1')!;
    expect(child.delegation?.role).toBe('subtask');
    expect(child.delegation?.parentId).toBe('proj');
    expect(child.delegation?.parentTitle).toBe('Board hygiene');
  });

  it('leaves ordinary tasks (no children, no parent) undelegated', () => {
    const enriched = enrichDelegation([task({ id: 'solo', sessionId: 'sess_x' })]);
    expect(enriched[0].delegation).toBeUndefined();
  });

  it('tolerates a sub-task whose parent is not in the current page', () => {
    const child = enrichDelegation([
      task({ id: 'orphan', assignee: 'agent:jira-expert', parentSessionId: 'sess_missing' }),
    ])[0];
    expect(child.delegation?.role).toBe('subtask');
    expect(child.delegation?.parentTitle).toBeUndefined();
  });
});

describe('boardColumnFor (operator stop/resume/cancel — 2026-08-02)', () => {
  it('folds a CANCELLED task into the Done lane', () => {
    expect(boardColumnFor(task({ id: 'x', status: 'CANCELLED' }))).toBe('DONE');
  });

  it('leaves every other status on its own lane, including a stopped run', () => {
    for (const status of ['NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'DONE', 'FAILED']) {
      expect(boardColumnFor(task({ id: 'x', status, stopped: status === 'IN_PROGRESS' })))
        .toBe(status);
    }
  });
});
