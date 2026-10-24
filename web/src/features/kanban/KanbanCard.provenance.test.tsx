// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { KanbanCard } from './KanbanCard';
import type { KanbanTask, TaskActor } from './types';

const base: KanbanTask = {
  id: 't', title: 'A task', status: 'NEW', priority: 'normal', createdBy: 'operator',
  createdAt: 0, updatedAt: 0, version: 1, labels: [], columnOrder: 0, feedback: [],
};

function render(task: KanbanTask): string {
  return renderToStaticMarkup(<KanbanCard task={task} onClick={() => {}} isOverlay />);
}

describe('KanbanCard provenance and refusal mark', () => {
  it('admits a heartbeat creator in the actor type', () => {
    const actor: TaskActor = 'heartbeat:nightly';
    expect(render({ ...base, createdBy: actor })).toContain('nightly');
  });

  it('shows the agent creator, the approver and the parent link', () => {
    const html = render({
      ...base, createdBy: 'agent:jira-expert', approvedBy: 'human:lee',
      parentTaskId: 'p1', parentTaskTitle: 'Parent work',
    });
    expect(html).toContain('@jira-expert');
    expect(html).toContain('approved by lee');
    expect(html).toContain('data-parent-task="p1"');
    expect(html).toContain('Parent work');
  });

  it('leaves an operator task unmarked', () => {
    const html = render(base);
    expect(html).not.toContain('approved by');
    expect(html).not.toContain('refused');
  });

  it('marks a task whose creation was refused', () => {
    const html = render({ ...base, refusals: { count: 2, latestReason: 'depth' } });
    expect(html).toContain('2 refused');
    expect(html).toContain('depth');
  });
});
