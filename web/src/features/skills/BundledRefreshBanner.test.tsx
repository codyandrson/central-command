/** Story 2.8: the Skills page shows a refresh that did not fully apply, and Retry posts the apply. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsView } from './SkillsView';
import type { LatestItem, LatestReview } from './bundledRefresh';

const gw = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@/contexts/GatewayContext', () => ({ useGateway: () => gw.value }));

const item = (over: Partial<LatestItem>): LatestItem => ({
  skill_id: 'jira', doc_key: 'api', status: 'unmodified', verdict: null, resolved_at: null,
  applied_at: '2026-10-31T10:00:00Z', apply_error: null, pending_apply: false, moved: false, ...over,
});

const review = (items: LatestItem[]): LatestReview => ({
  target: '2.77.0', ref: 'v2.77.0', computed_at: '2026-10-31T09:00:00Z', installed: '2.77.0', finished: true,
  counts: { not_refreshed: items.filter((i) => i.pending_apply).length }, items,
});

const failedRow = item({
  skill_id: 'jira', doc_key: 'api', applied_at: null, pending_apply: true,
  apply_error: 'the database was unreachable',
});
const movedRow = item({
  skill_id: 'confluence', doc_key: 'macros', applied_at: null, pending_apply: true, moved: true,
  apply_error: 'the library text changed since the review (ours_hash)',
});

/** The RPC table the page talks to; `latest` is what the next read answers. */
function wire(latest: () => LatestReview | Error, onApply = vi.fn().mockResolvedValue({ applied: 1, failed: 0 })) {
  const rpc = vi.fn(async (method: string) => {
    if (method === 'skills.list') return { skills: [] };
    if (method === 'gaps.list') return { gaps: [] };
    if (method === 'agents.roster') return { agents: [] };
    if (method === 'skills.update_review_latest') {
      const r = latest();
      if (r instanceof Error) throw r;
      return r;
    }
    if (method === 'skills.update_review_apply') return onApply();
    throw new Error(`unexpected rpc ${method}`);
  });
  gw.value = { rpc, connectionState: 'connected', subscribe: vi.fn(() => () => {}) };
  return rpc;
}

const NO_REVIEW = new Error('no bundled-skill review is stored');
const banner = () => screen.queryByRole('status', { name: /bundled skill docs not refreshed/i });

describe('the Skills page retry banner (story 2.8)', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('shows the count and target, with the per-row reasons on expand', async () => {
    wire(() => review([item({}), failedRow, movedRow]));
    render(<SkillsView />);

    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
    expect(within(b).getByText('2 bundled docs not refreshed for v2.77.0 — retry')).toBeInTheDocument();
    // The applied row is not part of it, and reasons wait behind the expander.
    expect(within(b).queryByText(/database was unreachable/)).toBeNull();

    await userEvent.click(within(b).getByRole('button', { name: /show reasons/i }));
    expect(within(b).getByText('jira/api')).toBeInTheDocument();
    expect(within(b).getByText(/the database was unreachable/)).toBeInTheDocument();
    expect(within(b).getByText('confluence/macros')).toBeInTheDocument();
    expect(within(b).queryByText('jira/other')).toBeNull();
  });

  it('says a plain retry cannot fix a row whose text moved, and points to the Update dialog', async () => {
    wire(() => review([failedRow, movedRow]));
    render(<SkillsView />);
    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
    await userEvent.click(within(b).getByRole('button', { name: /show reasons/i }));
    const hints = within(b).getAllByText(/plain retry cannot fix this/i);
    expect(hints).toHaveLength(1);                         // only the moved row carries it
    expect(hints[0]).toHaveTextContent(/Update dialog/);
    expect(hints[0].closest('li')).toHaveTextContent('confluence/macros');
  });

  it('is absent when every row is applied, and when no review is stored', async () => {
    wire(() => review([item({}), item({ doc_key: 'two' })]));
    const first = render(<SkillsView />);
    await waitFor(() => expect((gw.value.rpc as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('skills.update_review_latest'));
    await waitFor(() => expect(screen.getByLabelText('Refresh skills catalog')).toBeInTheDocument());
    expect(banner()).toBeNull();
    first.unmount();

    wire(() => NO_REVIEW);
    render(<SkillsView />);
    await waitFor(() => expect((gw.value.rpc as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('skills.update_review_latest'));
    expect(banner()).toBeNull();
  });

  it('Retry posts the apply as the updater for the review\'s target, then clears the banner once applied', async () => {
    let state: LatestReview = review([failedRow]);
    const rpc = wire(() => state, vi.fn(async () => {
      state = review([item({ skill_id: 'jira', doc_key: 'api' })]);   // the retry succeeded
      return { applied: 1, failed: 0 };
    }));
    render(<SkillsView />);
    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });

    await userEvent.click(within(b).getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(rpc).toHaveBeenCalledWith(
      'skills.update_review_apply', { target: '2.77.0', actor: 'updater' },
    ));
    await waitFor(() => expect(banner()).toBeNull());       // re-read: every row has applied_at
  });

  it('keeps the banner and shows the reason when the retry itself fails', async () => {
    wire(() => review([failedRow]), vi.fn().mockRejectedValue(new Error('1 docs still need a verdict; decide them first')));
    render(<SkillsView />);
    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
    await userEvent.click(within(b).getByRole('button', { name: 'Retry' }));
    expect(await within(b).findByRole('alert')).toHaveTextContent(/still need a verdict/);
    expect(banner()).not.toBeNull();
    expect(within(b).getByRole('button', { name: 'Retry' })).toBeEnabled();
  });

  it('keeps showing the banner when a later read fails', async () => {
    let fail = false;
    const rpc = wire(() => (fail ? new Error('gateway hiccup') : review([failedRow])));
    let push: (msg: { event: string }) => void = () => {};
    (gw.value as { subscribe: unknown }).subscribe = vi.fn((h: typeof push) => { push = h; return () => {}; });
    render(<SkillsView />);
    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
    const reads = () => rpc.mock.calls.filter(([m]) => m === 'skills.update_review_latest').length;
    const before = reads();

    fail = true;
    push({ event: 'cc.skill.doc_added' });                  // a skill event triggers a re-read
    await waitFor(() => expect(reads()).toBe(before + 1));
    expect(banner()).toBe(b);                               // the failed read changed nothing
  });
});
