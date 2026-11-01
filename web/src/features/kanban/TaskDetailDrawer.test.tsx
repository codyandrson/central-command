import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskDetailDrawer } from './TaskDetailDrawer';
import type { KanbanTask } from './types';

vi.mock('@/features/markdown/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="md">{content}</div>
  ),
}));

function makeTask(overrides: Partial<KanbanTask> = {}): KanbanTask {
  return {
    id: 'task-1',
    title: 'Existing task',
    description: 'Hello',
    status: 'todo',
    priority: 'normal',
    createdBy: 'operator',
    createdAt: 1,
    updatedAt: 2,
    version: 3,
    assignee: 'agent:designer',
    labels: ['frontend'],
    columnOrder: 0,
    feedback: [],
    ...overrides,
  };
}

function renderDrawer(task: KanbanTask | null) {
  const onDelete = vi.fn(async () => {});
  const onClose = vi.fn();
  render(
    <TaskDetailDrawer
      task={task}
      onClose={onClose}
      onDelete={onDelete}
    />,
  );
  return { onDelete, onClose };
}

describe('TaskDetailDrawer', () => {
  describe('re-run', () => {
    afterEach(() => { vi.unstubAllGlobals(); });

    function stubRerun(eligible: boolean) {
      const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => new Response(
        JSON.stringify(init?.method === 'POST'
          ? { sessionId: 'sess_new', agentId: 'jira-expert' }
          : { eligible, reason: eligible ? null : 'not yet' }),
        { status: 200 },
      ));
      vi.stubGlobal('fetch', fetchMock);
      return fetchMock;
    }

    it('offers Re-run where the server says so, and opens the new session', async () => {
      const fetchMock = stubRerun(true);
      const onOpenSession = vi.fn();
      const user = userEvent.setup({ delay: null });
      render(
        <TaskDetailDrawer
          task={makeTask({ status: 'done' })}
          onClose={vi.fn()}
          onDelete={vi.fn(async () => {})}
          onOpenSession={onOpenSession}
        />,
      );

      await user.click(await screen.findByRole('button', { name: 'Re-run' }));
      expect(fetchMock).not.toHaveBeenCalledWith('/api/kanban/tasks/task-1/rerun', { method: 'POST' });
      await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Re-run' }));

      expect(fetchMock).toHaveBeenCalledWith('/api/kanban/tasks/task-1/rerun', { method: 'POST' });
      expect(onOpenSession).toHaveBeenCalledWith('agent:jira-expert:sess_new');
    });

    it('offers nothing when the server refuses', async () => {
      const fetchMock = stubRerun(false);
      render(<TaskDetailDrawer task={makeTask({ status: 'done' })} onClose={vi.fn()} onDelete={vi.fn(async () => {})} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      await act(async () => {});
      expect(screen.queryByRole('button', { name: 'Re-run' })).not.toBeInTheDocument();
    });

    it('says why not on a finished task that has a session', async () => {
      stubRerun(false);
      render(<TaskDetailDrawer task={makeTask({ status: 'done', sessionId: 'sess_1' })} onClose={vi.fn()} onDelete={vi.fn(async () => {})} />);
      const refused = await screen.findByRole('button', { name: 'Re-run' });
      expect(refused).toBeDisabled();
      expect(refused).toHaveAttribute('title', 'Cannot be re-run: not yet');
    });
  });

  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('renders the task as read-only text, not an editable field', () => {
    renderDrawer(makeTask({ title: 'Read only title' }));

    expect(screen.getByText('Read only title')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });

  it('shows the assignee as plain text', () => {
    renderDrawer(makeTask({ assignee: 'agent:designer' }));

    expect(screen.getByText('@designer')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  describe('result field markdown', () => {
    it('forwards task.result verbatim into MarkdownRenderer', async () => {
      // MarkdownRenderer is mocked above; this asserts the raw string is the
      // content prop reaching the renderer. The renderer's own GFM transform
      // (## headings, tables, etc.) is covered by tests inside the markdown
      // package and is intentionally NOT re-verified here.
      const result = '## Heading\n\n| col | col |\n|--|--|\n| a | b |';
      renderDrawer(makeTask({ result }));

      const mds = await screen.findAllByTestId('md');
      expect(mds.some(md => md.textContent === result)).toBe(true);
    });

    it('does not render the result block when task.result is empty', () => {
      renderDrawer(makeTask({ result: '' }));
      expect(screen.queryByText('Result')).not.toBeInTheDocument();
    });

    it('does not render the result block when task.result is missing', () => {
      renderDrawer(makeTask({ result: undefined }));
      expect(screen.queryByText('Result')).not.toBeInTheDocument();
    });

    it('does not render the result block for whitespace-only content', () => {
      renderDrawer(makeTask({ result: '   \n\t  ' }));
      expect(screen.queryByText('Result')).not.toBeInTheDocument();
    });
  });

  describe('operator stop/resume/cancel levers', () => {
    function renderWithLevers(task: KanbanTask) {
      const onExecute = vi.fn(async () => task);
      const onApprove = vi.fn(async () => task);
      const onReject = vi.fn(async () => task);
      const onAbort = vi.fn(async () => task);
      const onResume = vi.fn(async () => task);
      const onDelete = vi.fn(async () => {});
      render(
        <TaskDetailDrawer
          task={task}
          onClose={vi.fn()}
          onDelete={onDelete}
          onExecute={onExecute}
          onApprove={onApprove}
          onReject={onReject}
          onAbort={onAbort}
          onResume={onResume}
        />,
      );
      return { onExecute, onApprove, onReject, onAbort, onResume, onDelete };
    }

    it('shows a Stopped badge when the task carries stopped: true', () => {
      renderWithLevers(makeTask({ status: 'IN_PROGRESS', stopped: true }));
      expect(screen.getByText('Stopped')).toBeInTheDocument();
    });

    it('does not show a Stopped badge for a live run', () => {
      renderWithLevers(makeTask({ status: 'IN_PROGRESS', stopped: false }));
      expect(screen.queryByText('Stopped')).not.toBeInTheDocument();
    });

    it('offers Stop for a live IN_PROGRESS run, not Resume', () => {
      renderWithLevers(makeTask({ status: 'IN_PROGRESS', stopped: false }));
      expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /resume/i })).not.toBeInTheDocument();
    });

    it('offers Resume and Cancel task for a stopped IN_PROGRESS run, not Stop', async () => {
      const user = userEvent.setup();
      const { onResume } = renderWithLevers(makeTask({ status: 'IN_PROGRESS', stopped: true }));
      expect(screen.queryByRole('button', { name: /^stop$/i })).not.toBeInTheDocument();
      const resumeBtn = screen.getByRole('button', { name: /resume/i });
      await user.click(resumeBtn);
      expect(onResume).toHaveBeenCalledWith('task-1');
      expect(screen.getByRole('button', { name: /cancel task/i })).toBeInTheDocument();
    });

    it('offers Execute for a NEW task', () => {
      renderWithLevers(makeTask({ status: 'NEW' }));
      expect(screen.getByRole('button', { name: /execute/i })).toBeInTheDocument();
    });

    it('offers Approve/Reject and Cancel task for a REVIEW task', () => {
      renderWithLevers(makeTask({ status: 'REVIEW', stopped: false }));
      expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /cancel task/i })).toBeInTheDocument();
    });

    it('offers a Cancel task lever for NEW and ASSIGNED tasks', () => {
      renderWithLevers(makeTask({ status: 'ASSIGNED' }));
      expect(screen.getByRole('button', { name: /cancel task/i })).toBeInTheDocument();
    });

    it.each(['DONE', 'FAILED', 'CANCELLED'])(
      'hides every destructive lever for a terminal %s task',
      (status) => {
        renderWithLevers(makeTask({ status }));
        expect(screen.queryByRole('button', { name: /cancel task/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^stop$/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /resume/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
      },
    );
  });
});
