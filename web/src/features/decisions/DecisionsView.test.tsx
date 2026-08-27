/**
 * The Decisions Inbox renders the aging chip on rows that have actually been
 * waiting, and on nothing else. `useDecisions` is mocked at the hook seam so
 * the test drives the projection directly.
 */
import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const state = {
  proposals: [] as unknown[],
  dismissals: [] as unknown[],
  operatorItems: [] as unknown[],
  detail: {} as Record<string, unknown>,
  dismiss: vi.fn(() => Promise.resolve()),
  reject: vi.fn(() => Promise.resolve()),
  approve: vi.fn(() => Promise.resolve()),
  answerOperatorItem: vi.fn(() => Promise.resolve()),
  reopenDismissal: vi.fn(() => Promise.resolve()),
  // History rows: fixture data a test populates directly. Visibility is
  // driven by real local state below (`useState`), same as the real hook —
  // otherwise clicking the toggle in a test would have nothing to re-render.
  history: [] as unknown[],
  historyHasMore: false,
  historyLoading: false,
  toggleHistory: vi.fn(),
  loadMoreHistory: vi.fn(() => Promise.resolve()),
};

vi.mock('./useDecisions', () => {
  // getProposal must keep ONE identity across renders, same as the real
  // useCallback-memoized hook — a fresh arrow function here re-triggers
  // ProposalPane's `useEffect(..., [id, getProposal])` on every keystroke,
  // which resets its feedback state and unmounts the textarea mid-type.
  const getProposal = () => Promise.resolve(state.detail);
  return {
    useDecisions: () => {
      const [showHistory, setShowHistory] = useState(false);
      return {
        ...state,
        showHistory,
        loading: false,
        error: '',
        refresh: () => {},
        getProposal,
        approve: state.approve,
        reject: state.reject,
        dismiss: state.dismiss,
        confirmDismissal: () => Promise.resolve(),
        confirmAllDismissals: () => Promise.resolve(),
        reopenDismissal: state.reopenDismissal,
        answerOperatorItem: state.answerOperatorItem,
        toggleHistory: () => {
          state.toggleHistory();
          setShowHistory((v) => !v);
        },
        loadMoreHistory: state.loadMoreHistory,
      };
    },
  };
});

const { DecisionsView } = await import('./DecisionsView');

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();

beforeEach(() => {
  state.proposals = [];
  state.dismissals = [];
  state.operatorItems = [];
  state.detail = {};
  state.dismiss = vi.fn(() => Promise.resolve());
  state.reject = vi.fn(() => Promise.resolve());
  state.approve = vi.fn(() => Promise.resolve());
  state.answerOperatorItem = vi.fn(() => Promise.resolve());
  state.reopenDismissal = vi.fn(() => Promise.resolve());
  state.history = [];
  state.historyHasMore = false;
  state.historyLoading = false;
  state.toggleHistory = vi.fn();
  state.loadMoreHistory = vi.fn(() => Promise.resolve());
});

describe('DecisionsView aging chips', () => {
  it('marks a four-day-old proposal as stale and names it in the section label', () => {
    state.proposals = [
      { id: 'p1', agent_id: 'jira-expert', intent: 'Create JIRA-9', created_at: hoursAgo(96) },
    ];
    render(<DecisionsView />);

    const chip = screen.getByTestId('decision-age-chip');
    expect(chip).toHaveAttribute('data-level', 'stale');
    expect(chip).toHaveTextContent('4d');
    expect(screen.getByTestId('decision-section-oldest')).toHaveTextContent('oldest: 4d');
  });

  it('shows NO chip for a ten-minute-old proposal', () => {
    state.proposals = [
      { id: 'p1', agent_id: 'jira-expert', intent: 'Create JIRA-9', created_at: hoursAgo(1 / 6) },
    ];
    render(<DecisionsView />);

    expect(screen.queryByTestId('decision-age-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('decision-section-oldest')).not.toBeInTheDocument();
  });

  it('ages an agent ask too — a question nobody answered is a blocked agent', () => {
    state.operatorItems = [
      {
        id: 'i1', agent_id: 'coach', kind: 'question', body: 'Which board?',
        created_at: hoursAgo(30),
      },
    ];
    render(<DecisionsView />);

    const chip = screen.getByTestId('decision-age-chip');
    expect(chip).toHaveAttribute('data-level', 'aging');
  });
});


describe('DecisionsView operator item pane', () => {
  it('lets the operator answer a discussion-linked question here too', async () => {
    // The lane agent can fail to conclude (it did, live, with a charter-v2 EA):
    // a disabled box left the operator with no way to deliver the answer at all.
    state.operatorItems = [
      {
        id: 'i1', agent_id: 'ea', kind: 'question', body: 'Which board?',
        created_at: hoursAgo(1), discussion_session_id: 'sess_lane',
      },
    ];
    render(<DecisionsView />);
    await userEvent.click(screen.getByText('Which board?'));

    const box = await screen.findByPlaceholderText(/Answer here/);
    expect(box).not.toBeDisabled();
    // …and the lane context stays, so answering here is not mistaken for the
    // only route.
    expect(screen.getByText(/opened a discussion for this/)).toBeInTheDocument();
  });
});


/**
 * The reviewer's pane says what it is looking at. `kind` reaches it over an
 * untyped RPC payload, so the backend wire-shape test is the primary guard —
 * this one holds the rendering rule that consumes it.
 */
describe('DecisionsView proposal pane', () => {
  const openProposal = async () => {
    state.proposals = [
      { id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', created_at: hoursAgo(1) },
    ];
    render(<DecisionsView />);
    await userEvent.click(screen.getByText('Record it'));
  };

  it('calls the source a document when the work item says so', async () => {
    state.detail = {
      id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
      source_emails: [{ id: 'wi1', subject: 'Platform decision', kind: 'document' }],
    };
    await openProposal();
    await waitFor(() => expect(screen.getByText(/Source document/)).toBeInTheDocument());
    expect(screen.queryByText(/Source email/)).not.toBeInTheDocument();
  });

  it('still says email for an unset kind — the column default', async () => {
    state.detail = {
      id: 'p1', agent_id: 'inbox-triage', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
      source_emails: [{ id: 'wi1', subject: 'Deadline' }],
    };
    await openProposal();
    await waitFor(() => expect(screen.getByText(/Source email/)).toBeInTheDocument());
  });

  it('renders the agent stated confidence as a badge', async () => {
    state.detail = {
      id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
      confidence: { level: 'low', rationale: 'the relationship is inferred' },
    };
    await openProposal();
    await waitFor(() => expect(screen.getByText('low confidence')).toBeInTheDocument());
    expect(screen.getByText('the relationship is inferred')).toBeInTheDocument();
  });

  it('shows no badge when the agent stated none — absent is not low', async () => {
    state.detail = {
      id: 'p1', agent_id: 'inbox-triage', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
    };
    await openProposal();
    // 'Evidence' is unconditional in the pane — proof the pane loaded at all.
    await waitFor(() => expect(screen.getByText('Evidence')).toBeInTheDocument());
    expect(screen.queryByText(/confidence$/)).not.toBeInTheDocument();
  });

  it('offers Dismiss enabled with an empty feedback box, and calls dismiss with the id', async () => {
    state.detail = {
      id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
    };
    await openProposal();

    const dismissButton = await screen.findByRole('button', { name: /Dismiss/ });
    // Unlike Reject, no feedback text is required.
    expect(dismissButton).not.toBeDisabled();

    await userEvent.click(dismissButton);
    expect(state.dismiss).toHaveBeenCalledWith('p1', '');
  });

  it('Enter in the feedback box rejects (not approves) once text is typed; Shift+Enter does not', async () => {
    state.detail = {
      id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
    };
    await openProposal();

    const box = await screen.findByPlaceholderText(/Rejection feedback/);
    await userEvent.type(box, 'not right{Shift>}{Enter}{/Shift}');
    expect(state.reject).not.toHaveBeenCalled();
    expect(state.approve).not.toHaveBeenCalled();

    await userEvent.type(box, '{Enter}');
    expect(state.reject).toHaveBeenCalledWith('p1', 'not right');
    expect(state.approve).not.toHaveBeenCalled();
  });

  it('Enter in the empty feedback box is a no-op', async () => {
    state.detail = {
      id: 'p1', agent_id: 'knowledge-steward', intent: 'Record it', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
    };
    await openProposal();

    const box = await screen.findByPlaceholderText(/Rejection feedback/);
    await userEvent.type(box, '{Enter}');
    expect(state.reject).not.toHaveBeenCalled();
  });
});

describe('DecisionsView operator item Enter-to-submit', () => {
  it('Enter sends the answer once text is typed; Shift+Enter inserts a newline instead', async () => {
    state.operatorItems = [
      { id: 'i1', agent_id: 'ea', kind: 'question', body: 'Which board?', created_at: hoursAgo(1) },
    ];
    render(<DecisionsView />);
    await userEvent.click(screen.getByText('Which board?'));

    const box = await screen.findByPlaceholderText(/Your answer/);
    await userEvent.type(box, 'CS board{Shift>}{Enter}{/Shift}');
    expect(state.answerOperatorItem).not.toHaveBeenCalled();

    await userEvent.type(box, '{Enter}');
    expect(state.answerOperatorItem).toHaveBeenCalledWith('i1', 'CS board');
  });
});

describe('DecisionsView dismissal Enter-to-submit', () => {
  it('Enter reopens once a note is typed; empty note is a no-op', async () => {
    state.dismissals = [
      { id: 'd1', subject: 'Newsletter', enrolled_at: hoursAgo(1) },
    ];
    render(<DecisionsView />);
    await userEvent.click(screen.getByText('Newsletter'));

    const box = await screen.findByPlaceholderText(/Reopen note/);
    await userEvent.type(box, '{Enter}');
    expect(state.reopenDismissal).not.toHaveBeenCalled();

    await userEvent.type(box, 'check this again');
    await userEvent.type(box, '{Enter}');
    expect(state.reopenDismissal).toHaveBeenCalledWith('d1', 'check this again');
  });
});

/**
 * A decision in flight belongs to the item it was made on and to nothing else.
 * Both halves were broken until 2026-08-16: the panes hold per-item `acting`
 * state and were unkeyed, so the previous item's in-flight action left the NEXT
 * item's buttons disabled and reading "Sending…"; and the late `onDone` cleared
 * whatever was selected by the time the rpc returned, evicting the operator
 * from a record they had since opened.
 */
describe('DecisionsView — one item acting never touches another', () => {
  it('a pending reject on one proposal leaves the next one fully actionable, and does not clear it', async () => {
    let settle: () => void = () => {};
    state.reject = vi.fn(() => new Promise<void>((res) => { settle = res; }));
    state.proposals = [
      { id: 'p1', agent_id: 'inbox-triage', intent: 'First', created_at: hoursAgo(1) },
      { id: 'p2', agent_id: 'inbox-triage', intent: 'Second', created_at: hoursAgo(1) },
    ];
    state.detail = {
      id: 'p1', agent_id: 'inbox-triage', intent: 'First', status: 'AWAITING_HUMAN',
      created_at: hoursAgo(1), actions: [], evidence: [],
    };
    render(<DecisionsView />);

    await userEvent.click(screen.getByText('First'));
    await userEvent.type(await screen.findByPlaceholderText(/Rejection feedback/), 'no');
    await userEvent.click(screen.getByRole('button', { name: /Reject/ }));
    expect(state.reject).toHaveBeenCalledWith('p1', 'no');

    // Move on while p1's reject is still in flight.
    await userEvent.click(screen.getByText('Second'));
    const reject = await screen.findByRole('button', { name: /Reject/ });
    expect(reject).toHaveTextContent('Reject');   // not "Sending…"
    expect(screen.getByRole('button', { name: /Approve/ })).not.toBeDisabled();

    settle();
    // p1's action resolving must not blank the pane p2 is showing.
    await waitFor(() => expect(screen.getByText('Evidence')).toBeInTheDocument());
    expect(screen.queryByText(/Select an item to review/)).not.toBeInTheDocument();
  });

  it('does the same for agent asks', async () => {
    let settle: () => void = () => {};
    state.answerOperatorItem = vi.fn(() => new Promise<void>((res) => { settle = res; }));
    state.operatorItems = [
      { id: 'o1', agent_id: 'inbox-triage', kind: 'question', body: 'Ask one', created_at: hoursAgo(1) },
      { id: 'o2', agent_id: 'inbox-triage', kind: 'question', body: 'Ask two', created_at: hoursAgo(1) },
    ];
    render(<DecisionsView />);

    await userEvent.click(screen.getByText('Ask one'));
    await userEvent.type(screen.getByPlaceholderText(/Your answer/), 'yes');
    await userEvent.click(screen.getByRole('button', { name: /Send answer/ }));
    expect(state.answerOperatorItem).toHaveBeenCalledWith('o1', 'yes');

    await userEvent.click(screen.getByText('Ask two'));
    // Fresh pane: the draft is o1's and must not follow, so the button is
    // disabled on an EMPTY box — never on someone else's action in flight.
    expect(screen.getByPlaceholderText(/Your answer/)).toHaveValue('');
    expect(screen.getByRole('button', { name: /Send answer/ })).toHaveTextContent('Send answer');

    settle();
    await waitFor(() => expect(screen.getAllByText('Ask two').length).toBeGreaterThan(1));
    expect(screen.queryByText(/Select an item to review/)).not.toBeInTheDocument();
  });
});

describe('DecisionsView history', () => {
  it('is hidden by default — no History rows and no fetch', () => {
    state.history = [
      { id: 'h1', agent_id: 'ea', intent: 'reply to thread', status: 'EXECUTED', decided_at: hoursAgo(2) },
    ];
    render(<DecisionsView />);
    expect(screen.queryByTestId('history-row')).not.toBeInTheDocument();
    expect(screen.queryByText('reply to thread')).not.toBeInTheDocument();
  });

  it('toggling the History button shows decided rows', async () => {
    state.history = [
      { id: 'h1', agent_id: 'ea', intent: 'reply to thread', status: 'EXECUTED', decided_at: hoursAgo(2) },
      { id: 'h2', agent_id: 'jira-expert', intent: 'Create JIRA-9', status: 'REJECTED', decided_at: hoursAgo(5) },
    ];
    render(<DecisionsView />);

    await userEvent.click(screen.getByLabelText('Show decision history'));

    expect(state.toggleHistory).toHaveBeenCalled();
    expect(screen.getAllByTestId('history-row')).toHaveLength(2);
    expect(screen.getByText('reply to thread')).toBeInTheDocument();
    expect(screen.getByText('EXECUTED')).toBeInTheDocument();
    expect(screen.getByText('REJECTED')).toBeInTheDocument();
  });

  it('Load more calls loadMoreHistory when there is another page', async () => {
    state.history = [
      { id: 'h1', agent_id: 'ea', intent: 'reply to thread', status: 'EXECUTED', decided_at: hoursAgo(2) },
    ];
    state.historyHasMore = true;
    render(<DecisionsView />);

    await userEvent.click(screen.getByLabelText('Show decision history'));
    await userEvent.click(screen.getByText('Load more'));

    expect(state.loadMoreHistory).toHaveBeenCalled();
  });

  it('a history row offers no approve/reject/dismiss affordance', async () => {
    state.history = [
      { id: 'h1', agent_id: 'ea', intent: 'reply to thread', status: 'EXECUTED', decided_at: hoursAgo(2) },
    ];
    state.detail = {
      id: 'h1', agent_id: 'ea', intent: 'reply to thread', status: 'EXECUTED',
      created_at: hoursAgo(3), decided_at: hoursAgo(2), actions: [], evidence: [],
    };
    render(<DecisionsView />);

    await userEvent.click(screen.getByLabelText('Show decision history'));
    await userEvent.click(screen.getByText('reply to thread'));

    await waitFor(() => expect(screen.getAllByText('EXECUTED').length).toBeGreaterThan(0));
    expect(screen.queryByRole('button', { name: /^Approve$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reject$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Dismiss$/ })).not.toBeInTheDocument();
  });
});
