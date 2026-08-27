import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Session } from '@/types';
import { SessionList } from './SessionList';

vi.mock('@/components/skeletons', () => ({
  SessionSkeletonGroup: ({ count = 4 }: { count?: number }) => (
    <div data-testid="session-skeleton-group">Loading {count}</div>
  ),
}));

function renderSessionList(props: Partial<React.ComponentProps<typeof SessionList>> = {}) {
  return render(
    <SessionList
      sessions={[]}
      currentSession=""
      busyState={{}}
      onSelect={() => {}}
      onRefresh={() => {}}
      {...props}
    />,
  );
}

describe('SessionList empty state', () => {
  it('shows the empty state when all sessions are filtered out of the agent sidebar', () => {
    const sessions: Session[] = [
      { sessionKey: 'discord:sean', label: 'Discord Root' },
      { sessionKey: 'whatsapp:sean', label: 'WhatsApp Root' },
    ];

    renderSessionList({ sessions });

    expect(screen.getByText('No active sessions')).toBeInTheDocument();
  });

  it('shows orphaned agent descendants instead of the empty state when cleanup removed the root row', () => {
    const sessions: Session[] = [
      { sessionKey: 'agent:main:telegram:direct:123', displayName: 'Telegram DM' },
      { sessionKey: 'agent:reviewer:subagent:abc123', label: 'Worker' },
      { sessionKey: 'discord:sean', label: 'Discord Root' },
    ];

    renderSessionList({ sessions });

    expect(screen.getByText('Telegram DM')).toBeInTheDocument();
    expect(screen.getByText('Worker')).toBeInTheDocument();
    expect(screen.queryByText('No active sessions')).not.toBeInTheDocument();
  });

  it('shows the loading skeleton when loading and all sessions are filtered out', () => {
    const sessions: Session[] = [
      { sessionKey: 'discord:sean', label: 'Discord Root' },
    ];

    renderSessionList({ sessions, isLoading: true });

    expect(screen.getByTestId('session-skeleton-group')).toBeInTheDocument();
    expect(screen.queryByText('No active sessions')).not.toBeInTheDocument();
  });
});

describe('SessionList history filter', () => {
  beforeEach(() => {
    localStorage.removeItem('cc-sessions-show-history');
  });

  const sessions: Session[] = [
    { sessionKey: 'agent:litellm-manager:main', label: 'LiteLLM Manager' },
    {
      sessionKey: 'agent:litellm-manager:sess_open',
      parentSessionKey: 'agent:litellm-manager:main',
      label: 'Open convo',
      mode: 'conversation',
      status: 'AWAITING_OPERATOR',
    },
    {
      sessionKey: 'agent:litellm-manager:sess_done',
      parentSessionKey: 'agent:litellm-manager:main',
      label: 'Closed convo',
      mode: 'conversation',
      status: 'DONE',
    },
  ];

  it('hides terminal sessions by default and shows them via the History toggle', () => {
    renderSessionList({ sessions });

    expect(screen.getByText('Open convo')).toBeInTheDocument();
    expect(screen.queryByText('Closed convo')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /show closed sessions/i }));

    expect(screen.getByText('Closed convo')).toBeInTheDocument();
    expect(screen.getByText('Open convo')).toBeInTheDocument();
  });

  it('never hides the currently selected session, even when terminal', () => {
    renderSessionList({ sessions, currentSession: 'agent:litellm-manager:sess_done' });

    expect(screen.getByText('Closed convo')).toBeInTheDocument();
  });

  it('does hide the selected session once chat view is no longer active', () => {
    renderSessionList({
      sessions,
      currentSession: 'agent:litellm-manager:sess_done',
      isViewingChat: false,
    });

    expect(screen.queryByText('Closed convo')).not.toBeInTheDocument();
  });

  // 2026-08-11 activity-coverage: this sidebar switches CONVERSATIONS. Closed
  // `oneshot` runs were 565 of 601 rows on the Pi and made the toggle useless;
  // they live on Activity → Runs now.
  const withRun: Session[] = [
    ...sessions,
    {
      sessionKey: 'agent:litellm-manager:sess_run',
      parentSessionKey: 'agent:litellm-manager:main',
      label: 'Closed machine run',
      mode: 'oneshot',
      status: 'DONE',
    },
  ];

  it('keeps closed machine runs out of the sidebar even with History on', () => {
    renderSessionList({ sessions: withRun });
    fireEvent.click(screen.getByRole('button', { name: /show closed sessions/i }));

    expect(screen.getByText('Closed convo')).toBeInTheDocument();
    expect(screen.queryByText('Closed machine run')).not.toBeInTheDocument();
  });

  it('counts only closed conversations in the History tooltip', () => {
    renderSessionList({ sessions: withRun });

    // One closed conversation, one closed run — the count must not advertise
    // rows the toggle will never reveal.
    expect(screen.getByRole('button', { name: /show closed sessions/i }))
      .toHaveAttribute('title', expect.stringContaining('(1)'));
  });

  it('still reveals a closed session that declares no mode', () => {
    // An unknown shape must not vanish silently — that is the exact failure
    // this record exists to end. Retired agent roots arrive this way.
    renderSessionList({
      sessions: [
        ...sessions,
        {
          sessionKey: 'agent:litellm-manager:sess_nomode',
          parentSessionKey: 'agent:litellm-manager:main',
          label: 'Closed, mode unknown',
          status: 'DONE',
        },
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: /show closed sessions/i }));

    expect(screen.getByText('Closed, mode unknown')).toBeInTheDocument();
  });
});

describe('SessionList tier-3 stall chip', () => {
  const SESSION_ID = 'sess_abc123';
  const KEY = `agent:jira-expert:${SESSION_ID}`;
  const HOUR = 3_600_000;

  // Real rows carry an explicit parentSessionKey: Central Command's
  // "agent:<id>:<session_id>" keys match none of Nerve's key-shape
  // conventions, so the sidebar's lineage filter drops them without it
  // (nerve_gateway._session_row_dict says so in as many words).
  function row(updatedAt: string | undefined) {
    return {
      sessionKey: KEY,
      parentSessionKey: 'agent:jira-expert:main',
      label: 'Clarification',
      status: 'AWAITING_OPERATOR',
      updatedAt,
    } as unknown as Session;
  }

  function rowWith(updatedAt: string | undefined, stalledAt: number) {
    return renderSessionList({
      sessions: [row(updatedAt)],
      stalledDiscussions: { [SESSION_ID]: { taskTitle: 'Ship the invoice fix', stalledAt } },
    });
  }

  it('names the task a stalled discussion is holding', () => {
    const stalledAt = Date.now() - 50 * HOUR;
    rowWith(new Date(stalledAt).toISOString(), stalledAt);

    expect(screen.getByTestId('session-stall-chip')).toHaveTextContent(
      'blocks Ship the invoice fix · waiting 2d');
  });

  it('SUPPRESSES the chip once the lane has moved since the flag', () => {
    // The operator answered after the sweep flagged it. The stamp is current
    // state, not history — an overtaken flag must stop accusing them.
    const stalledAt = Date.now() - 50 * HOUR;
    rowWith(new Date(Date.now() - HOUR).toISOString(), stalledAt);

    expect(screen.queryByTestId('session-stall-chip')).not.toBeInTheDocument();
  });

  it('reports hours below a day rather than rounding up to "1d"', () => {
    // CC_DISCUSSION_STALL_HOURS is configurable; a 6h stall must not claim a day.
    const stalledAt = Date.now() - 6 * HOUR;
    rowWith(new Date(stalledAt).toISOString(), stalledAt);

    expect(screen.getByTestId('session-stall-chip')).toHaveTextContent('waiting 6h');
  });

  it('shows no chip for a session the sweep has not flagged', () => {
    renderSessionList({
      sessions: [row(new Date().toISOString())],
      stalledDiscussions: {},
    });

    expect(screen.queryByTestId('session-stall-chip')).not.toBeInTheDocument();
  });
});

describe('SessionList agent-initiated lanes', () => {
  const base: Session[] = [
    { sessionKey: 'agent:coach:main', label: 'Coach' },
  ];

  it('shows an "asked you" chip on a lane the AGENT opened', () => {
    // Without it, a conversation the agent started because it is BLOCKED is
    // pixel-identical to one the operator opened and left resting.
    renderSessionList({
      sessions: [
        ...base,
        {
          sessionKey: 'agent:coach:sess_a1b2c3d4e5f6',
          parentSessionKey: 'agent:coach:main',
          label: 'Which board?',
          agentId: 'coach',
          mode: 'conversation',
          status: 'AWAITING_OPERATOR',
          openedBy: 'agent',
          blockedTaskTitle: 'Sprint tidy',
        },
      ],
    });

    const chip = screen.getByTestId('session-asked-chip');
    expect(chip).toHaveTextContent('asked you');
    expect(chip).toHaveAttribute('title', expect.stringContaining('Sprint tidy'));
  });

  it('shows NO chip on an equally-resting lane the OPERATOR opened', () => {
    renderSessionList({
      sessions: [
        ...base,
        {
          sessionKey: 'agent:coach:sess_b1b2c3d4e5f6',
          parentSessionKey: 'agent:coach:main',
          label: 'My question',
          agentId: 'coach',
          mode: 'conversation',
          status: 'AWAITING_OPERATOR',
          openedBy: 'operator',
        },
      ],
    });

    expect(screen.queryByTestId('session-asked-chip')).not.toBeInTheDocument();
  });

  it('drops the chip once the lane is no longer awaiting the operator', () => {
    renderSessionList({
      sessions: [
        ...base,
        {
          sessionKey: 'agent:coach:sess_c1b2c3d4e5f6',
          parentSessionKey: 'agent:coach:main',
          label: 'Answered',
          agentId: 'coach',
          mode: 'conversation',
          status: 'RUNNING',
          openedBy: 'agent',
        },
      ],
    });

    expect(screen.queryByTestId('session-asked-chip')).not.toBeInTheDocument();
  });
});
