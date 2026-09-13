import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ReadOnlySessionNotice } from './ReadOnlySessionNotice';
import type { ChatComposerState } from '@/types';

/**
 * The notice that stands where the message box would be. What it owes the
 * operator is the three facts the 2026-07-25 incident left unconnected: what
 * this transcript is, what is holding it, and where they can actually act.
 */
const pausedRun: ChatComposerState = {
  enabled: false,
  code: 'not_a_conversation',
  message: 'This is a task run, paused — jira-expert is waiting on your answer.',
  refusal: 'session sess_1 is not a conversation — …',
  session: { id: 'sess_1', mode: 'oneshot', status: 'AWAITING_HUMAN', agentId: 'jira-expert' },
  kind: 'task run',
  task: { id: 'task_1', title: 'Epic restructure', status: 'REVIEW' },
  blocking: {
    itemId: 'item_1',
    kind: 'question',
    body: 'Which epic would you like restructured?',
    discussionSessionId: null,
  },
};

describe('ReadOnlySessionNotice', () => {
  it('says what the run is, shows the question, and offers no input', () => {
    render(<ReadOnlySessionNotice composer={pausedRun} />);

    expect(screen.getByText(/task run, paused/)).toBeInTheDocument();
    expect(screen.getByText(/Which epic would you like restructured\?/)).toBeInTheDocument();
    // The whole point: nothing here invites a message the server would refuse.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('routes the operator to the ask and to the work it blocks', async () => {
    const onOpenDecisions = vi.fn();
    const onOpenTask = vi.fn();
    const user = userEvent.setup({ delay: null });
    render(
      <ReadOnlySessionNotice
        composer={pausedRun}
        onOpenDecisions={onOpenDecisions}
        onOpenTask={onOpenTask}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Answer it in the Decisions Inbox/ }));
    // The ITEM, not just the screen — the operator arrived from the run it
    // blocks, so landing on an unselected inbox would lose what they came for.
    expect(onOpenDecisions).toHaveBeenCalledWith('item_1');

    await user.click(screen.getByRole('button', { name: /Epic restructure/ }));
    expect(onOpenTask).toHaveBeenCalledWith('task_1');
  });

  it('sends a tier-3 ask to the discussion instead of offering an answer', () => {
    render(
      <ReadOnlySessionNotice
        composer={{
          ...pausedRun,
          blocking: { ...pausedRun.blocking!, discussionSessionId: 'sess_disc' },
        }}
        onOpenDecisions={vi.fn()}
      />,
    );
    // Answering happens in the discussion the agent opened; the inbox item is
    // where you SEE it, not where you answer it (tier 3's inbox rule).
    expect(screen.getByRole('button', { name: /See it in the Decisions Inbox/ })).toBeInTheDocument();
  });

  it('falls back to the server’s own words for a non-run refusal', () => {
    render(
      <ReadOnlySessionNotice
        composer={{
          enabled: false,
          code: 'pending_proposal',
          message: 'conversation sess_2 is AWAITING_HUMAN — resolve the pending decision in the Decisions Inbox',
          session: { id: 'sess_2', mode: 'conversation', status: 'AWAITING_HUMAN', agentId: 'auditor' },
        }}
        onOpenDecisions={vi.fn()}
      />,
    );
    expect(screen.getByText(/resolve the pending decision/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open the Decisions Inbox/ })).toBeInTheDocument();
    // A run's explanation must not be pasted over a conversation's problem.
    expect(screen.queryByText(/Runs are the record of work/)).not.toBeInTheDocument();
  });

  describe('operator-stopped session (2026-08-02)', () => {
    const stopped: ChatComposerState = {
      enabled: false,
      code: 'stopped',
      message: 'This is a task run, stopped by you — resume it to carry on, or cancel the task.',
      session: { id: 'sess_3', mode: 'oneshot', status: 'STOPPED', agentId: 'jira-expert' },
    };

    it('offers a Resume lever and calls it with the composer session id', async () => {
      const onResume = vi.fn(async () => {});
      const user = userEvent.setup({ delay: null });
      render(<ReadOnlySessionNotice composer={stopped} onResume={onResume} />);

      await user.click(screen.getByRole('button', { name: /resume/i }));
      expect(onResume).toHaveBeenCalledWith('sess_3');
    });

    it('shows no Resume lever when the caller offers none', () => {
      render(<ReadOnlySessionNotice composer={stopped} />);
      expect(screen.queryByRole('button', { name: /resume/i })).not.toBeInTheDocument();
    });

    it('surfaces a resume failure instead of hanging silent', async () => {
      const onResume = vi.fn(async () => { throw new Error('session already RUNNING'); });
      const user = userEvent.setup({ delay: null });
      render(<ReadOnlySessionNotice composer={stopped} onResume={onResume} />);

      await user.click(screen.getByRole('button', { name: /resume/i }));
      expect(await screen.findByText(/session already RUNNING/)).toBeInTheDocument();
    });
  });
});

describe('ReadOnlySessionNotice — the task run levers (2026-09-13)', () => {
  const parkedOnOutage: ChatComposerState = {
    ...pausedRun,
    message: 'This is a task run, paused — a dependency outage interrupted its last turn; the control plane retries it.',
    session: { id: 'sess_2', mode: 'oneshot', status: 'AWAITING_RESUME', agentId: 'litellm-manager' },
    task: { id: 'task_2', title: 'LiteLLM autodiscovery: Kilo.ai batch 12/377', status: 'REVIEW' },
    blocking: null,
  };

  it('offers Cancel for a parked run and cancels only after the operator confirms', async () => {
    const onCancelTask = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup({ delay: null });
    render(<ReadOnlySessionNotice composer={parkedOnOutage} onCancelTask={onCancelTask} />);

    expect(screen.queryByRole('button', { name: /Stop run/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Cancel task/ }));
    expect(onCancelTask).not.toHaveBeenCalled(); // terminal: the dialog stands between
    await user.click(screen.getByRole('button', { name: /Keep it/ }));
    expect(onCancelTask).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Cancel task/ }));
    expect(screen.getByText(/Cancel this task\?/)).toBeInTheDocument();
    // The dialog's confirm is the LAST "Cancel task" button on screen.
    await user.click(screen.getAllByRole('button', { name: /Cancel task/ }).at(-1)!);
    expect(onCancelTask).toHaveBeenCalledWith('task_2');
  });

  it('offers Stop, never Cancel, while the run is live', async () => {
    const onStopTask = vi.fn().mockResolvedValue(undefined);
    const onCancelTask = vi.fn();
    const user = userEvent.setup({ delay: null });
    render(
      <ReadOnlySessionNotice
        composer={{ ...parkedOnOutage, session: { ...parkedOnOutage.session!, status: 'RUNNING' } }}
        onStopTask={onStopTask}
        onCancelTask={onCancelTask}
      />,
    );
    expect(screen.queryByRole('button', { name: /Cancel task/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Stop run/ }));
    expect(onStopTask).toHaveBeenCalledWith('task_2');
  });

  it('offers neither once the task is terminal, and surfaces a failed action', async () => {
    const { rerender } = render(
      <ReadOnlySessionNotice
        composer={{ ...parkedOnOutage, task: { ...parkedOnOutage.task!, status: 'CANCELLED' } }}
        onStopTask={vi.fn()}
        onCancelTask={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /Cancel task|Stop run/ })).not.toBeInTheDocument();

    const failing = vi.fn().mockRejectedValue(new Error('task task_2 has a live run — stop it first'));
    const user = userEvent.setup({ delay: null });
    rerender(<ReadOnlySessionNotice composer={parkedOnOutage} onCancelTask={failing} />);
    await user.click(screen.getByRole('button', { name: /Cancel task/ }));
    await user.click(screen.getAllByRole('button', { name: /Cancel task/ }).at(-1)!);
    expect(await screen.findByText(/stop it first/)).toBeInTheDocument();
  });
});
