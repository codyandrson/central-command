import '@testing-library/jest-dom';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  AgentNote, CharterHistory, EpisodesPayload, LearnedPayload, NotesPayload,
} from '../hooks/useAgentLearned';
import { MemoryTab } from './MemoryTab';

type GatewayHandler = (msg: { event?: string }) => void;
const handlers = new Set<GatewayHandler>();

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    subscribe: (h: GatewayHandler) => { handlers.add(h); return () => { handlers.delete(h); }; },
  }),
}));

function note(over: Partial<AgentNote> = {}): AgentNote {
  return {
    id: 'note_aaaaaaaaaaaa', agent_id: 'jira-expert', scope: 'private',
    text: 'Jane Doe files her invoices as DOE-INV tickets.', origin: 'agent',
    proposal_id: 'prop_000000000001', source_session_id: 'sess_000000000001',
    source_quote: 'Jane always files invoices as DOE-INV', created_at: '2026-11-10T09:00:00Z',
    retired_at: null, retired_by: null, ...over,
  };
}

function notesPayload(over: Partial<NotesPayload> = {}): NotesPayload {
  return {
    agent_id: 'jira-expert', rendered: true, private: [note()],
    operator: [note({
      id: 'note_bbbbbbbbbbbb', agent_id: 'orchestrator', scope: 'operator',
      text: 'The operator prefers ISO dates.', proposal_id: 'prop_000000000002',
      source_session_id: 'sess_000000000002', source_quote: 'always use ISO dates',
    })],
    budgets: { private: 2000, operator: 1500 }, usage: { private: 1474, operator: 31 },
    ...over,
  };
}

const learnedPayload: LearnedPayload = {
  agent_id: 'jira-expert',
  skill_docs: [{
    id: 'doc_000000000001', skill_id: 'jira-hygiene', skill_title: 'Jira hygiene',
    skill_status: 'ACTIVE', doc_key: 'guidance', kind: 'guidance', title: 'Guidance',
    version: 2, is_current: true, describes: null, created_at: '2026-11-11T10:00:00Z', retired_at: null,
  }],
  pending: [{
    id: 'prop_000000000003', session_id: 'sess_000000000003', intent: 'Keep a note about Rivers',
    status: 'AWAITING_HUMAN', created_at: '2026-11-14T08:00:00Z',
    actions: [{ capability: 'memory.note_add', label: 'private note: Rivers Co pays net 45.' }],
  }],
};

const episodesPayload: EpisodesPayload = {
  episodes: [{
    uuid: 'ep-1', name: 'Doe onboarding', content: 'Jane Doe joined the Rivers account team.',
    created_at: '2026-11-09T12:00:00Z',
  }],
  total: 1, has_more: false,
};

const charterPayload: CharterHistory = {
  current: { version: 3, created_by: 'operator', rationale: 'tighten scope', created_at: '2026-11-01T00:00:00Z' },
  versions: [
    { version: 3, created_by: 'operator', rationale: 'tighten scope', created_at: '2026-11-01T00:00:00Z' },
    { version: 2, created_by: 'coach', rationale: 'add Jira rules', created_at: '2026-10-20T00:00:00Z' },
  ],
};

type Routes = Record<string, () => Response | Promise<Response>>;

function envelope(result: unknown, status = 200): Response {
  return new Response(JSON.stringify({ ok: true, result }), {
    status, headers: { 'content-type': 'application/json' },
  });
}

function failure(error: string, status = 502): Response {
  return new Response(JSON.stringify({ ok: false, error }), {
    status, headers: { 'content-type': 'application/json' },
  });
}

function stubFetch(over: Routes = {}) {
  const routes: Routes = {
    '/api/cc/notes/jira-expert': () => envelope(notesPayload()),
    '/api/cc/learned/jira-expert': () => envelope(learnedPayload),
    '/api/graph/episodes': () => envelope(episodesPayload),
    '/api/cc/charter/jira-expert': () => envelope(charterPayload),
    ...over,
  };
  const mock = vi.fn(async (input: string | URL | Request) => {
    const url = new URL(String(input), 'http://cockpit.test');
    const handler = routes[url.pathname];
    if (!handler) throw new Error(`Unexpected fetch: ${url.pathname}`);
    return handler();
  });
  globalThis.fetch = mock as unknown as typeof fetch;
  return mock;
}

describe('MemoryTab', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => { handlers.clear(); });
  afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks(); });

  it('reads each section from its own endpoint, scoped to the agent', async () => {
    const mock = stubFetch();
    render(<MemoryTab agentId="jira-expert" />);

    await screen.findByText('Doe onboarding');
    const urls = mock.mock.calls.map((c) => new URL(String(c[0]), 'http://cockpit.test'));
    expect(urls.map((u) => u.pathname).sort()).toEqual([
      '/api/cc/charter/jira-expert', '/api/cc/learned/jira-expert',
      '/api/cc/notes/jira-expert', '/api/graph/episodes',
    ]);
    const episodes = urls.find((u) => u.pathname === '/api/graph/episodes')!;
    expect(episodes.searchParams.get('agent_id')).toBe('jira-expert');
    expect(episodes.searchParams.get('scope')).toBe('private');
  });

  it('shows a loading line in every section before the first answers', () => {
    globalThis.fetch = vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch;
    render(<MemoryTab agentId="jira-expert" />);

    expect(screen.getAllByText('Loading…').length).toBeGreaterThanOrEqual(4);
  });

  it('renders the populated tab: notes with budget meters, episodes, skill documents, charter versions', async () => {
    stubFetch();
    render(<MemoryTab agentId="jira-expert" />);

    // Notes: both groups, each with its meter against its budget.
    const operator = await screen.findByTestId('notes-group-operator');
    const own = screen.getByTestId('notes-group-private');
    expect(within(operator).getByText('Operator preferences')).toBeInTheDocument();
    expect(within(operator).getByText('31 of 1,500 characters')).toBeInTheDocument();
    expect(within(operator).getByText('The operator prefers ISO dates.')).toBeInTheDocument();
    expect(within(operator).getByText('proposed by orchestrator')).toBeInTheDocument();
    expect(within(own).getByText("This agent's notes")).toBeInTheDocument();
    expect(within(own).getByText('1,474 of 2,000 characters')).toBeInTheDocument();
    expect(within(own).getByText('Jane Doe files her invoices as DOE-INV tickets.')).toBeInTheDocument();
    const meter = within(own).getByRole('meter', { name: /this agent's notes usage/i });
    expect(meter).toHaveAttribute('aria-valuenow', '1474');
    expect(meter).toHaveAttribute('aria-valuemax', '2000');
    // The quote it was grounded in is secondary, behind a disclosure.
    expect(within(own).getByText('Jane always files invoices as DOE-INV')).not.toBeVisible();
    expect(within(own).getByText('2026-11-10')).toBeInTheDocument();
    expect(within(own).getByText('prop_000000000001')).toBeInTheDocument();

    // Graph episodes, skill documents, charter versions.
    expect(screen.getByText('Doe onboarding')).toBeInTheDocument();
    expect(screen.getByText(/Private graph episodes \(1\)/)).toBeInTheDocument();
    expect(screen.getByText('Jira hygiene')).toBeInTheDocument();
    const skillRow = screen.getByTestId('skill-doc-row');
    expect(within(skillRow).getByText('v2')).toBeInTheDocument();
    expect(within(skillRow).getByText('guidance')).toBeInTheDocument();
    expect(screen.getByText(/Charter versions \(2\)/)).toBeInTheDocument();
    const rows = screen.getAllByTestId('charter-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('current')).toBeInTheDocument();
    expect(within(rows[1]).getByText('add Jira rules')).toBeInTheDocument();
  });

  it('lists pending proposals near the top, with a count', async () => {
    stubFetch();
    render(<MemoryTab agentId="jira-expert" />);

    const heading = await screen.findByText(/Waiting for your decision \(1\)/);
    const rows = screen.getAllByTestId('pending-row');
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByText('private note: Rivers Co pays net 45.')).toBeInTheDocument();
    expect(within(rows[0]).getByText('awaiting you')).toBeInTheDocument();
    // Pending comes before the notes in the document.
    const notesHeading = screen.getByText(/^Notes$/);
    expect(heading.compareDocumentPosition(notesHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('omits the pending section when nothing is pending', async () => {
    stubFetch({ '/api/cc/learned/jira-expert': () => envelope({ ...learnedPayload, pending: [] }) });
    render(<MemoryTab agentId="jira-expert" />);

    await screen.findByText('Jira hygiene');
    expect(screen.queryByText(/Waiting for your decision/)).not.toBeInTheDocument();
  });

  it('shows honest empty states for an agent that has learned nothing', async () => {
    stubFetch({
      '/api/cc/notes/jira-expert': () => envelope(notesPayload({
        private: [], operator: [], usage: { private: 0, operator: 0 },
      })),
      '/api/cc/learned/jira-expert': () => envelope({ agent_id: 'jira-expert', skill_docs: [], pending: [] }),
      '/api/graph/episodes': () => envelope({ episodes: [], total: 0, has_more: false }),
      '/api/cc/charter/jira-expert': () => envelope({
        current: { version: 0, created_by: 'built-in', rationale: 'hardcoded default', created_at: null },
        versions: [],
      }),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText(/No operator preferences yet/)).toBeInTheDocument();
    expect(screen.getByText('0 of 1,500 characters')).toBeInTheDocument();
    expect(screen.getByText('0 of 2,000 characters')).toBeInTheDocument();
    expect(screen.getByText(/This agent has no notes of its own yet/)).toBeInTheDocument();
    expect(screen.getByText(/No approved episodes in this agent's private partition/)).toBeInTheDocument();
    expect(screen.getByText(/No approved skill documents from this agent yet/)).toBeInTheDocument();
    expect(screen.getByText(/runs on its built-in charter \(v0\)/)).toBeInTheDocument();
  });

  it('keeps retired notes in a collapsed Retired list', async () => {
    stubFetch({
      '/api/cc/notes/jira-expert': () => envelope(notesPayload({
        private: [
          note(),
          note({
            id: 'note_cccccccccccc', text: 'Rivers Co pays net 30.', retired_at: '2026-11-12T00:00:00Z',
            retired_by: 'prop_000000000009',
          }),
        ],
      })),
    });
    render(<MemoryTab agentId="jira-expert" />);

    const retired = await screen.findByTestId('notes-retired');
    expect(within(retired).getByText('Retired (1)')).toBeInTheDocument();
    expect(retired).not.toHaveAttribute('open');
    // Collapsed: the retired text is in the DOM but not visible, and not listed with the active notes.
    expect(within(retired).getByText('Rivers Co pays net 30.')).not.toBeVisible();
    expect(within(screen.getByTestId('notes-group-private')).queryByText('Rivers Co pays net 30.')).not.toBeInTheDocument();

    await userEvent.setup().click(within(retired).getByText('Retired (1)'));
    expect(within(retired).getByText('Rivers Co pays net 30.')).toBeVisible();
    expect(within(retired).getByText('retired by prop_000000000009')).toBeInTheDocument();
  });

  it('says plainly that an auditor is shown no notes, and lists no note groups', async () => {
    stubFetch({
      '/api/cc/notes/jira-expert': () => envelope(notesPayload({ rendered: false, private: [], operator: [] })),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText(/This agent is shown no notes/)).toBeInTheDocument();
    expect(screen.queryByTestId('notes-group-operator')).not.toBeInTheDocument();
    expect(screen.queryByTestId('notes-group-private')).not.toBeInTheDocument();
  });

  it('turns a meter over budget into a warning without overflowing the bar', async () => {
    stubFetch({
      '/api/cc/notes/jira-expert': () => envelope(notesPayload({ usage: { private: 2500, operator: 31 } })),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText('2,500 of 2,000 characters')).toBeInTheDocument();
  });

  it('shows one failing section as its own error and keeps the others', async () => {
    stubFetch({ '/api/cc/notes/jira-expert': () => failure('Central Command HTTP 500') });
    render(<MemoryTab agentId="jira-expert" />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Central Command HTTP 500');
    // Every other section still rendered.
    expect(screen.getByText('Doe onboarding')).toBeInTheDocument();
    expect(screen.getByText('Jira hygiene')).toBeInTheDocument();
    expect(screen.getByText('add Jira rules')).toBeInTheDocument();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('retries a failed section on request', async () => {
    let calls = 0;
    stubFetch({
      '/api/graph/episodes': () => (++calls === 1 ? failure('graph unreachable') : envelope(episodesPayload)),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText('graph unreachable')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Doe onboarding')).toBeInTheDocument();
    expect(screen.queryByText('graph unreachable')).not.toBeInTheDocument();
  });

  it('shows the failure of the shared learned read in both the pending and the skills slots', async () => {
    stubFetch({ '/api/cc/learned/jira-expert': () => failure('learned unavailable') });
    render(<MemoryTab agentId="jira-expert" />);

    await waitFor(() => expect(screen.getAllByText('learned unavailable')).toHaveLength(2));
    expect(screen.getByText('The operator prefers ISO dates.')).toBeInTheDocument();
  });

  it('pages the private episodes: shows N of TOTAL and loads more', async () => {
    const mock = stubFetch({
      '/api/graph/episodes': () => envelope({ ...episodesPayload, total: 45, has_more: true }),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText('Showing 1 of 45')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Load more' }));

    await waitFor(() => {
      const limits = mock.mock.calls
        .map((c) => new URL(String(c[0]), 'http://cockpit.test'))
        .filter((u) => u.pathname === '/api/graph/episodes')
        .map((u) => u.searchParams.get('limit'));
      expect(limits).toEqual(['20', '40']);
    });
  });

  it('is read-only: no add, edit, retire or delete control, and a line on how notes change', async () => {
    stubFetch();
    render(<MemoryTab agentId="jira-expert" />);

    await screen.findByText('Doe onboarding');
    for (const name of [/add/i, /edit/i, /retire/i, /delete/i, /forget/i, /remove/i]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    expect(screen.getByText(/tell the agent in chat/i)).toHaveTextContent(/remember that I always/);
    expect(screen.getByText(/tell the agent in chat/i)).toHaveTextContent(/forget/);
    expect(screen.getByText(/approve it in the Decisions Inbox/)).toBeInTheDocument();
  });

  it('links each section to the screen that owns its detail, and notes to their source session', async () => {
    stubFetch();
    const onNavigate = vi.fn();
    const onOpenSession = vi.fn();
    const user = userEvent.setup();
    render(<MemoryTab agentId="jira-expert" onNavigate={onNavigate} onOpenSession={onOpenSession} />);

    await screen.findByText('Doe onboarding');
    await user.click(screen.getByRole('button', { name: 'Open Decisions Inbox' }));
    await user.click(screen.getByRole('button', { name: 'Open graph' }));
    await user.click(screen.getByRole('button', { name: 'Open Skills' }));
    await user.click(screen.getByRole('button', { name: 'Open Agents' }));
    expect(onNavigate.mock.calls.map((c) => c[0])).toEqual(['inbox', 'graph', 'skills', 'agents']);

    const links = screen.getAllByRole('button', { name: 'Open source session' });
    expect(links).toHaveLength(2);
    await user.click(links[0]);
    // The operator-scope note comes first; its session belongs to the agent that drafted it.
    expect(onOpenSession).toHaveBeenCalledWith('sess_000000000002', 'orchestrator');
    await user.click(links[1]);
    expect(onOpenSession).toHaveBeenLastCalledWith('sess_000000000001', 'jira-expert');
  });

  it('shows no links when the tab is given nowhere to navigate', async () => {
    stubFetch();
    render(<MemoryTab agentId="jira-expert" />);

    await screen.findByText('Doe onboarding');
    expect(screen.queryByRole('button', { name: /^Open / })).not.toBeInTheDocument();
  });

  it('shows the earlier charter versions behind a disclosure past the first five', async () => {
    const versions = Array.from({ length: 7 }, (_, i) => ({
      version: 7 - i, created_by: 'operator', rationale: `change ${7 - i}`, created_at: '2026-11-01T00:00:00Z',
    }));
    stubFetch({
      '/api/cc/charter/jira-expert': () => envelope({ current: versions[0], versions }),
    });
    render(<MemoryTab agentId="jira-expert" />);

    expect(await screen.findByText('Earlier versions (2)')).toBeInTheDocument();
    expect(screen.getByText('change 2')).not.toBeVisible();
    expect(screen.getByText('change 7')).toBeVisible();
  });

  it('refetches silently when a matching gateway event arrives, and ignores others', async () => {
    const mock = stubFetch();
    render(<MemoryTab agentId="jira-expert" />);
    await screen.findByText('Doe onboarding');
    mock.mockClear();

    handlers.forEach((h) => h({ event: 'cc.session.started' }));
    await new Promise((r) => setTimeout(r, 650));
    expect(mock).not.toHaveBeenCalled();

    handlers.forEach((h) => h({ event: 'cc.note.added' }));
    await waitFor(() => {
      expect(mock.mock.calls.map((c) => new URL(String(c[0]), 'http://cockpit.test').pathname))
        .toContain('/api/cc/notes/jira-expert');
    });
    // Only the notes section follows note events.
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it('drops the previous agent\'s sections at once on an agent switch', async () => {
    stubFetch({
      '/api/cc/notes/bravo': () => envelope(notesPayload({
        agent_id: 'bravo', private: [note({ id: 'note_dddddddddddd', agent_id: 'bravo', text: 'Bravo keeps its own note.' })],
        operator: [],
      })),
      '/api/cc/learned/bravo': () => envelope({ agent_id: 'bravo', skill_docs: [], pending: [] }),
      '/api/cc/charter/bravo': () => envelope({ ...charterPayload }),
    });
    const { rerender } = render(<MemoryTab agentId="jira-expert" />);
    await screen.findByText('Jane Doe files her invoices as DOE-INV tickets.');

    rerender(<MemoryTab agentId="bravo" />);

    expect(screen.queryByText('Jane Doe files her invoices as DOE-INV tickets.')).not.toBeInTheDocument();
    expect(await screen.findByText('Bravo keeps its own note.')).toBeInTheDocument();
  });
});
