/** DL-232: the Skills page shows the bundled docs a release changed under an edit, and each decision posts. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsView } from './SkillsView';
import type { ReleaseFlag } from './bundledRefresh';

const gw = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@/contexts/GatewayContext', () => ({ useGateway: () => gw.value }));

const flag = (over: Partial<ReleaseFlag> = {}): ReleaseFlag => ({
  skill_id: 'jira', doc_key: 'api', release: '2.130.0', change: 'changed',
  bundled_path: 'skills/jira/references/api.md', flagged_at: '2026-11-22T10:00:00Z',
  ours: 'a\nmine\nz\n', ours_hash: 'h-ours', theirs: 'a\nrelease\nz\n', ...over,
});

/** The RPC table the page talks to; `flags` is what the next read answers. */
function wire(flags: () => ReleaseFlag[] | Error, onResolve = vi.fn().mockResolvedValue({})) {
  const rpc = vi.fn(async (method: string, params?: unknown) => {
    if (method === 'skills.list') return { skills: [] };
    if (method === 'gaps.list') return { gaps: [] };
    if (method === 'agents.roster') return { agents: [] };
    if (method === 'skills.release_flags') {
      const r = flags();
      if (r instanceof Error) throw r;
      return { items: r };
    }
    if (method === 'skills.release_flag_resolve') return onResolve(params);
    throw new Error(`unexpected rpc ${method}`);
  });
  gw.value = { rpc, connectionState: 'connected', subscribe: vi.fn(() => () => {}) };
  return rpc;
}

const banner = () => screen.queryByRole('status', { name: /bundled skill docs kept for your decision/i });

async function openBanner() {
  const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
  await userEvent.click(within(b).getByRole('button', { name: /show docs/i }));
  return b;
}

describe('the Skills page flag banner (DL-232)', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('shows the count, and on expand each doc with its text beside the release\'s', async () => {
    wire(() => [flag(), flag({ skill_id: 'confluence', doc_key: 'macros', change: 'removed', theirs: null })]);
    render(<SkillsView />);
    const b = await openBanner();
    expect(within(b).getByText('2 bundled docs you edited were changed by a release — your text was kept')).toBeInTheDocument();
    const card = within(b).getByRole('listitem', { name: 'jira/api' });
    expect(within(card).getByText('changed here and in v2.130.0')).toBeInTheDocument();
    expect(card).toHaveTextContent('~mine');
    expect(card).toHaveTextContent('+release');
    const removed = within(b).getByRole('listitem', { name: 'confluence/macros' });
    expect(within(removed).getByText('v2.130.0 no longer ships it')).toBeInTheDocument();
    expect(within(removed).getByRole('button', { name: /retire it/i })).toBeInTheDocument();
  });

  it('is absent when nothing is flagged', async () => {
    const rpc = wire(() => []);
    render(<SkillsView />);
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('skills.release_flags'));
    expect(banner()).toBeNull();
  });

  it('posts Take the release text with the shown hash, then clears once decided', async () => {
    let open = [flag()];
    const onResolve = vi.fn(async () => { open = []; return {}; });
    const rpc = wire(() => open, onResolve);
    render(<SkillsView />);
    const b = await openBanner();
    await userEvent.click(within(b).getByRole('button', { name: 'Take the release text' }));
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('skills.release_flag_resolve', {
      skill_id: 'jira', doc_key: 'api', decision: 'take_release', ours_hash: 'h-ours',
    }));
    await waitFor(() => expect(banner()).toBeNull());
  });

  it('posts Keep mine', async () => {
    const onResolve = vi.fn().mockResolvedValue({});
    wire(() => [flag()], onResolve);
    render(<SkillsView />);
    const b = await openBanner();
    await userEvent.click(within(b).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(expect.objectContaining({ decision: 'keep_mine' })));
  });

  it('posts an edit with the text typed, starting from the library\'s', async () => {
    const onResolve = vi.fn().mockResolvedValue({});
    wire(() => [flag()], onResolve);
    render(<SkillsView />);
    const b = await openBanner();
    await userEvent.click(within(b).getByRole('button', { name: 'Edit' }));
    const box = within(b).getByRole('textbox', { name: 'Edited text for jira/api' });
    expect(box).toHaveValue('a\nmine\nz\n');
    await userEvent.clear(box);
    await userEvent.type(box, 'both');
    await userEvent.click(within(b).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(expect.objectContaining({
      decision: 'edit', content: 'both',
    })));
  });

  it('shows the reason on the card when a decision is refused', async () => {
    wire(() => [flag()], vi.fn().mockRejectedValue(new Error('the library text of jira/api changed while this ran')));
    render(<SkillsView />);
    const b = await openBanner();
    await userEvent.click(within(b).getByRole('button', { name: 'Keep mine' }));
    expect(await within(b).findByRole('alert')).toHaveTextContent(/changed while this ran/);
  });

  it('keeps showing the banner when a later read fails', async () => {
    let fail = false;
    const rpc = wire(() => (fail ? new Error('gateway hiccup') : [flag()]));
    let push: (msg: { event: string }) => void = () => {};
    (gw.value as { subscribe: unknown }).subscribe = vi.fn((h: typeof push) => { push = h; return () => {}; });
    render(<SkillsView />);
    const b = await waitFor(() => { expect(banner()).not.toBeNull(); return banner()!; });
    const reads = () => rpc.mock.calls.filter(([m]) => m === 'skills.release_flags').length;
    const before = reads();

    fail = true;
    push({ event: 'cc.skill.review_computed' });            // a skill event triggers a re-read
    await waitFor(() => expect(reads()).toBe(before + 1));
    expect(banner()).toBe(b);                               // the failed read changed nothing
  });
});
