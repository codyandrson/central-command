/**
 * Selection stability: what the operator has open NEVER closes by itself.
 *
 * The system resolves items server-side all the time (the auditor
 * auto-confirms concurred dismissals ~30s–3min after they appear, chat lanes
 * conclude asks, a task cancel withdraws proposals) — and every cc.* push
 * triggers a silent list refresh. The pane must ride those refreshes: keep
 * rendering the record, update it in place, swap the action row for a
 * resolved notice. Blanking the pane mid-read is the bug this suite pins
 * (2026-08-15, live: three auditor auto-confirms in one 15-minute review
 * session, each one evicting whatever was selected).
 *
 * Real useDecisions + DecisionsView; the gateway is mocked at the context
 * seam — the existing DecisionsView tests mock the hook itself, which is
 * exactly why this interaction had no coverage.
 */
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecisionsData, ProposalDetail } from './types';

type Handler = (msg: { event?: string }) => void;
let handlers: Handler[] = [];

const now = () => new Date().toISOString();

let listData: DecisionsData;
let detailById: Record<string, ProposalDetail>;

const rpc = vi.fn(async (method: string, params?: Record<string, unknown>) => {
  if (method === 'decisions.list') return JSON.parse(JSON.stringify(listData));
  if (method === 'proposals.get') return detailById[params?.id as string];
  throw new Error(`unexpected rpc ${method}`);
});

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    rpc,
    connectionState: 'connected',
    subscribe: (h: Handler) => { handlers.push(h); return () => {}; },
  }),
}));

const { DecisionsView } = await import('./DecisionsView');

/** Fire a cc.* push and let the 400ms debounced silent refresh land. */
async function pushRefresh() {
  act(() => { handlers.forEach((h) => h({ event: 'cc.proposal.created' })); });
  await act(async () => { await new Promise((r) => setTimeout(r, 600)); });
}

const proposalA = {
  id: 'p-a', session_id: 's-a', agent_id: 'triage',
  intent: 'Proposal A intent', status: 'AWAITING_HUMAN', created_at: now(),
};

const dismissalD = {
  id: 'wi-d', subject: 'Promo blast', state: 'DISMISS_PENDING', enrolled_at: now(),
  payload: { from: 'shop@example.com', text: 'Buy now', dismissal_rationale: 'Pure promotion, no ask.' },
};

const askQ = {
  id: 'oi-q', kind: 'question' as const, session_id: 's-q', agent_id: 'triage',
  body: 'Is this appointment still relevant?', status: 'OPEN' as const, created_at: now(),
};

beforeEach(() => {
  handlers = [];
  rpc.mockClear();
  listData = { proposals: [proposalA], dismissals: [dismissalD], operatorItems: [askQ] };
  detailById = {
    'p-a': { ...proposalA, actions: [], evidence: [] },
  };
});

describe('selection survives the world changing underneath it', () => {
  it('keeps the reviewed proposal selected when another proposal arrives', async () => {
    const user = userEvent.setup();
    render(<DecisionsView />);
    await user.click(await screen.findByText('Proposal A intent'));
    expect(await screen.findByText('Approve')).toBeTruthy();

    listData.proposals = [
      { ...proposalA, id: 'p-b', intent: 'Proposal B intent' },
      ...listData.proposals,
    ];
    await pushRefresh();

    expect(screen.getByText('Proposal B intent')).toBeTruthy();
    expect(screen.getByText('Approve')).toBeTruthy();
    expect(screen.queryByText('Select an item to review its full record.')).toBeNull();
  });

  it('keeps a dismissal on screen when the auditor confirms it away, minus the action row', async () => {
    const user = userEvent.setup();
    render(<DecisionsView />);
    await user.click(await screen.findByText('Promo blast'));
    expect(await screen.findByText('Pure promotion, no ask.')).toBeTruthy();
    expect(screen.getByText('Confirm dismissal')).toBeTruthy();

    listData.dismissals = []; // auditor auto-confirmed it server-side
    await pushRefresh();

    expect(screen.getByText('Pure promotion, no ask.')).toBeTruthy();
    expect(screen.getByTestId('resolved-notice')).toBeTruthy();
    expect(screen.queryByText('Confirm dismissal')).toBeNull();
  });

  it('keeps an agent ask on screen when it is closed elsewhere, minus the answer box', async () => {
    const user = userEvent.setup();
    render(<DecisionsView />);
    await user.click(await screen.findByText('Is this appointment still relevant?'));
    expect(await screen.findByText('Send answer')).toBeTruthy();

    listData.operatorItems = []; // answered via the chat lane / task resolved
    await pushRefresh();

    // The body renders in both the row and the pane; after the refresh the
    // row is gone, so the pane copy must still be there.
    expect(screen.getByText("triage's question")).toBeTruthy();
    expect(screen.getByTestId('resolved-notice')).toBeTruthy();
    expect(screen.queryByText('Send answer')).toBeNull();
  });

  it('re-loads a proposal decided out from under the operator instead of blanking', async () => {
    const user = userEvent.setup();
    render(<DecisionsView />);
    await user.click(await screen.findByText('Proposal A intent'));
    expect(await screen.findByText('Approve')).toBeTruthy();

    // Decided elsewhere (e.g. its task was cancelled → WITHDRAWN).
    listData.proposals = [];
    detailById['p-a'] = {
      ...detailById['p-a'], status: 'WITHDRAWN', decided_at: now(),
    };
    await pushRefresh();

    expect(await screen.findByText('WITHDRAWN')).toBeTruthy();
    expect(screen.getByText('Proposal A intent')).toBeTruthy();
    expect(screen.queryByText('Approve')).toBeNull();
  });
});
