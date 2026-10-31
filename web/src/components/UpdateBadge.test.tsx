import '@testing-library/jest-dom';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdateBadge } from './UpdateBadge';
import { _resetVersionCheck } from '@/lib/version-check';
import type { Review, ReviewItem } from '@/features/skills/skillReview';

function createMockResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  });
}

describe('UpdateBadge', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    _resetVersionCheck();
    global.fetch = vi.fn<typeof fetch>(async () => createMockResponse({
      current: '1.5.2',
      latest: '1.5.3',
      updateAvailable: true,
      projectDir: '/tmp/nerve repo',
    }));
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('shows the update.sh pipeline with the project directory', async () => {
    const user = userEvent.setup();
    render(<UpdateBadge />);

    await user.click(await screen.findByRole('button', { name: /update available: version 1.5.3/i }));

    await waitFor(() => {
      expect(screen.getByText('Project directory')).toBeInTheDocument();
    });

    expect(screen.getByText('/tmp/nerve repo')).toBeInTheDocument();
    // The external-updater one-command path (2026-08-28), never an in-app apply.
    expect(screen.getByText(/update\.sh ~\/Downloads\/central-command-1\.5\.3\.zip/)).toBeInTheDocument();
  });

  it('does not read the previous run\'s success record as this update\'s completion', async () => {
    // The durable status record still says the LAST update (to the version now
    // installed) succeeded. That is not "v1.5.3 is running": the dialog must
    // offer the apply, not hide it behind "Update Complete" (2026-09-18).
    global.fetch = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/api/update/status')) {
        return createMockResponse({
          mode: 'systemd', pending: false, inFlight: false,
          status: { state: 'success', phase: 'done', current: '1.5.1', target: '1.5.2' },
          stage: { pending: false, inFlight: false,
                   status: { state: 'success', phase: 'staged', current: '1.5.2', target: '1.5.3' } },
        });
      }
      if (url.includes('/api/update/hold')) {
        return createMockResponse({ active: false, target: '', since: null, running: [] });
      }
      return createMockResponse({
        current: '1.5.2', latest: '1.5.3', updateAvailable: true, projectDir: '/tmp/nerve repo',
      });
    });
    const user = userEvent.setup();
    render(<UpdateBadge />);
    await user.click(await screen.findByRole('button', { name: /update available: version 1.5.3/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /apply update now/i })).toBeInTheDocument();
    });
    expect(screen.getByText('Update Available')).toBeInTheDocument();
    expect(screen.queryByText('Update Complete')).not.toBeInTheDocument();
    expect(screen.queryByText(/Update complete — the system is healthy/)).not.toBeInTheDocument();
  });

  it('does not render when the server omits the project directory', async () => {
    global.fetch = vi.fn<typeof fetch>(async () => createMockResponse({
      current: '1.5.2',
      latest: '1.5.3',
      updateAvailable: true,
      projectDir: '',
    }));

    render(<UpdateBadge />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    expect(screen.queryByRole('button', { name: /update available: version 1.5.3/i })).not.toBeInTheDocument();
  });
});

// --- the bundled-skill review section (epic-skills-refresh-on-update R4) ------

const H = (c: string) => c.repeat(64);

function reviewItem(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    skill_id: 'alpha', doc_key: 'one', status: 'modified', bundled_path: 'skills/alpha/references/one.md',
    base_origin: 'library',
    base: 'line one\nline 10\nline eleven\n',
    ours: 'line one\nmine ten\nline eleven\n',
    theirs: 'line one\nrelease ten\nline eleven\nnew tail\n',
    merged: 'line one\n<<<<<<< mine (library)\nmine ten\n||||||| base (v0)\nline 10\n=======\nrelease ten\n>>>>>>> release (v1)\nline eleven\nnew tail\n',
    conflicts: 1, ours_hash: H('a'), theirs_hash: H('b'), needs_verdict: true,
    verdict: null, resolved_content: null, resolved_at: null, applied_at: null, apply_error: null,
    ...over,
  };
}

function reviewOf(items: ReviewItem[], extra: Partial<Review['counts']> = {}): Review {
  const count = (st: string) => items.filter((i) => i.status === st).length;
  return {
    target: '1.5.3', ref: 'v1.5.3', computed_at: '2026-11-01T00:00:00Z',
    counts: {
      unmodified: count('unmodified'), new: count('new'), modified: count('modified'),
      unresolved: items.filter((i) => i.needs_verdict && i.verdict === null).length,
      retired_upstream: count('retired-upstream'), retired_locally: count('retired-locally'),
      skill_retired: count('skill-retired'), ...extra,
    },
    items,
  };
}

interface Call { url: string; method: string; body: unknown }

/** A fake server for the dialog: staged, with the review routes scripted. */
function serve(script: {
  get: () => Response | Promise<Response>;
  post?: () => Response | Promise<Response>;
  resolve?: (call: Call) => Response | Promise<Response>;
  applyStatus?: () => Response;
}) {
  const calls: Call[] = [];
  global.fetch = vi.fn<typeof fetch>(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? 'GET';
    const call = { url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (url.includes('/api/update/status')) {
      return createMockResponse({
        mode: 'systemd', pending: false, inFlight: false, status: null,
        stage: { pending: false, inFlight: false,
                 status: { state: 'success', phase: 'staged', current: '1.5.2', target: '1.5.3' } },
      });
    }
    if (url.includes('/api/update/hold')) {
      return createMockResponse({ active: false, target: '', since: null, running: [] });
    }
    if (url.includes('/api/update/apply')) return script.applyStatus?.() ?? createMockResponse({}, 202);
    if (url.endsWith('/resolve')) return script.resolve!(call);
    if (url.endsWith('/api/skills/update-review')) return script.post!();
    if (url.includes('/api/skills/update-review/')) return script.get();
    return createMockResponse({
      current: '1.5.2', latest: '1.5.3', updateAvailable: true, projectDir: '/tmp/nerve repo',
    });
  });
  return calls;
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/api/skills/update-review'));
const resolves = (calls: Call[]) => calls.filter((c) => c.url.endsWith('/resolve'));

async function openDialog() {
  const user = userEvent.setup();
  render(<UpdateBadge />);
  await user.click(await screen.findByRole('button', { name: /update available: version 1.5.3/i }));
  return user;
}

const applyButton = () => screen.getByRole('button', { name: /apply update now/i });

describe('UpdateBadge bundled skill review', () => {
  const originalFetch = global.fetch;
  beforeEach(() => { _resetVersionCheck(); });
  afterEach(() => { global.fetch = originalFetch; vi.restoreAllMocks(); });

  it('posts the compute once on a 404, keeps Apply disabled meanwhile, then shows the review', async () => {
    let release!: (r: Response) => void;
    const pending = new Promise<Response>((r) => { release = r; });
    let stored: Review | null = null;
    const calls = serve({
      get: () => stored ? createMockResponse(stored) : createMockResponse({ detail: 'no review' }, 404),
      post: () => pending,
    });
    await openDialog();

    expect(await screen.findAllByText('Reviewing bundled skill docs…')).not.toHaveLength(0);
    expect(applyButton()).toBeDisabled();
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ target: '1.5.3', ref: 'v1.5.3' });
    expect(posts(calls)[0].url).toBe('/api/skills/update-review');

    stored = reviewOf([reviewItem()]);
    release(createMockResponse(stored));
    expect(await screen.findByRole('region', { name: 'Bundled skill docs' })).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(1);
  });

  it('computes again when the read answers 409, once', async () => {
    let stale = true;
    const calls = serve({
      get: () => stale ? createMockResponse({ detail: 'the library text changed' }, 409) : createMockResponse(reviewOf([reviewItem()])),
      post: () => { stale = false; return createMockResponse(reviewOf([reviewItem()])); },
    });
    await openDialog();
    expect(await screen.findByRole('region', { name: 'Bundled skill docs' })).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(1);
  });

  it('shows a failed compute\'s reason with Apply disabled, and Retry posts again', async () => {
    let ok = false;
    const calls = serve({
      get: () => createMockResponse({ detail: 'no review' }, 404),
      post: () => ok ? createMockResponse(reviewOf([reviewItem()])) : createMockResponse({ detail: 'v1.5.3 carries no bundled skills' }, 422),
    });
    const user = await openDialog();
    expect(await screen.findByText('v1.5.3 carries no bundled skills')).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
    expect(posts(calls)).toHaveLength(1);

    ok = true;
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('region', { name: 'Bundled skill docs' })).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(2);
  });

  it('uses the upstream ref on the single-node profile', async () => {
    const calls = serve({
      get: () => createMockResponse({ detail: 'no review' }, 404),
      post: () => createMockResponse(reviewOf([reviewItem()])),
    });
    const base = global.fetch;
    global.fetch = vi.fn<typeof fetch>(async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/api/update/status')) {
        return createMockResponse({
          mode: 'local', pending: false, inFlight: false, status: null,
          stage: { pending: false, inFlight: false, status: { state: 'success', phase: 'staged', target: '1.5.3' } },
        });
      }
      return base(input, init);
    });
    await openDialog();
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ target: '1.5.3', ref: 'upstream' });
  });

  it('omits the zero terms of the summary line', async () => {
    serve({
      get: () => createMockResponse(reviewOf([
        reviewItem({ doc_key: 'a', status: 'unmodified', needs_verdict: false, merged: null, conflicts: 0 }),
        reviewItem({ doc_key: 'b', status: 'unmodified', needs_verdict: false, merged: null, conflicts: 0 }),
      ])),
    });
    await openDialog();
    expect(await screen.findByText('2 docs follow the release unchanged')).toBeInTheDocument();
    expect(applyButton()).toBeEnabled();
  });

  it('prints every term, and one grey line per retired skill', async () => {
    serve({
      get: () => createMockResponse(reviewOf([
        reviewItem({ doc_key: 'a', status: 'unmodified', needs_verdict: false, merged: null, conflicts: 0 }),
        reviewItem({ doc_key: 'n', status: 'new', needs_verdict: false, merged: null, conflicts: 0, ours: null, ours_hash: null }),
        reviewItem(),
        reviewItem({ skill_id: 'old', doc_key: '*', status: 'skill-retired', needs_verdict: false, merged: null, conflicts: 0 }),
      ])),
    });
    await openDialog();
    expect(await screen.findByText('1 doc follows the release unchanged · 1 new · 1 needs your decision')).toBeInTheDocument();
    expect(screen.getByText('old: retired here — not refreshed')).toBeInTheDocument();
  });

  it('renders a card with an amber conflict region, labelled blocks and gutter marks', async () => {
    serve({ get: () => createMockResponse(reviewOf([reviewItem()])) });
    await openDialog();
    const card = await screen.findByRole('region', { name: 'alpha/one' });
    expect(within(card).getByText('1 conflict region')).toBeInTheDocument();
    const region = within(card).getByRole('group', { name: 'conflict region' });
    expect(region.className).toMatch(/amber/);
    for (const label of ['mine (library)', 'base', 'release']) {
      expect(within(region).getByText(label)).toBeInTheDocument();
    }
    expect(within(region).getByText('mine ten')).toBeInTheDocument();
    expect(within(region).getByText('release ten')).toBeInTheDocument();
    // Not by colour alone: the release's addition carries a + gutter mark.
    expect(within(card).getByText('new tail').parentElement).toHaveTextContent('+');
    expect(card).not.toHaveTextContent('compared against the installed copy');
  });

  it('says a doc with no recorded provenance was compared against the installed copy', async () => {
    serve({ get: () => createMockResponse(reviewOf([reviewItem({ base_origin: 'installed-tree' })])) });
    await openDialog();
    expect(await screen.findByText(/compared against the installed copy \(no provenance recorded before this release\)/)).toBeInTheDocument();
  });

  it('keeps Apply disabled with the count while docs are unresolved', async () => {
    serve({ get: () => createMockResponse(reviewOf([reviewItem(), reviewItem({ doc_key: 'two' })])) });
    await openDialog();
    expect(await screen.findByText('2 bundled skill docs need a decision above')).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
  });

  it('posts Take release with the item\'s hashes, then enables Apply', async () => {
    let resolved = false;
    const calls = serve({
      get: () => createMockResponse(reviewOf([reviewItem(resolved ? { verdict: 'take_release', resolved_at: 'now' } : {})])),
      resolve: () => {
        resolved = true;
        return createMockResponse({ target: '1.5.3', skill_id: 'alpha', doc_key: 'one', verdict: 'take_release', resolved_at: 'now', counts: reviewOf([]).counts });
      },
    });
    const user = await openDialog();
    expect(await screen.findByText('1 bundled skill doc needs a decision above')).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();

    await user.click(within(screen.getByRole('region', { name: 'alpha/one' })).getByRole('button', { name: 'Take release' }));

    await waitFor(() => expect(applyButton()).toBeEnabled());
    const [call] = resolves(calls);
    expect(call.url).toBe('/api/skills/update-review/1.5.3/items/alpha/one/resolve');
    expect(call.body).toEqual({ verdict: 'take_release', ours_hash: H('a'), theirs_hash: H('b') });
    // Collapsed to one line with its verdict and a Change link.
    expect(screen.getByText(/— Take release/)).toHaveTextContent('alpha/one — Take release');
    expect(screen.getByRole('button', { name: 'Change' })).toBeInTheDocument();
  });

  it('posts Keep mine, and the retired-doc verdicts under their own names', async () => {
    const calls = serve({
      get: () => createMockResponse(reviewOf([
        reviewItem(),
        reviewItem({ doc_key: 'up', status: 'retired-upstream', merged: null, conflicts: 0, theirs: null, theirs_hash: null }),
        reviewItem({ doc_key: 'loc', status: 'retired-locally', merged: null, conflicts: 0, ours: null, ours_hash: null }),
      ])),
      resolve: () => createMockResponse({ verdict: 'keep_mine', resolved_at: 'now', counts: reviewOf([reviewItem()]).counts }),
    });
    const user = await openDialog();
    const one = await screen.findByRole('region', { name: 'alpha/one' });
    const up = screen.getByRole('region', { name: 'alpha/up' });
    const loc = screen.getByRole('region', { name: 'alpha/loc' });
    expect(within(one).getByRole('button', { name: 'Edit merged' })).toBeInTheDocument();
    expect(within(up).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(within(loc).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(within(loc).getByRole('button', { name: 'Reactivate with release text' })).toBeInTheDocument();

    await user.click(within(one).getByRole('button', { name: 'Keep mine' }));
    await user.click(within(up).getByRole('button', { name: 'Retire (take release)' }));
    await user.click(within(loc).getByRole('button', { name: 'Keep retired' }));

    await waitFor(() => expect(resolves(calls)).toHaveLength(3));
    expect(resolves(calls).map((c) => [c.url.split('/items/')[1], (c.body as { verdict: string }).verdict])).toEqual([
      ['alpha/one/resolve', 'keep_mine'], ['alpha/up/resolve', 'take_release'], ['alpha/loc/resolve', 'keep_mine'],
    ]);
    // A null side is posted as null, not dropped.
    expect(resolves(calls)[1].body).toEqual({ verdict: 'take_release', ours_hash: H('a'), theirs_hash: null });
    expect(resolves(calls)[2].body).toEqual({ verdict: 'keep_mine', ours_hash: null, theirs_hash: H('b') });
  });

  it('keeps Save disabled while conflict markers remain, then posts the edit with its content', async () => {
    const calls = serve({
      get: () => createMockResponse(reviewOf([reviewItem()])),
      resolve: () => createMockResponse({ verdict: 'edit', resolved_at: 'now', counts: reviewOf([reviewItem()]).counts }),
    });
    const user = await openDialog();
    const card = await screen.findByRole('region', { name: 'alpha/one' });
    await user.click(within(card).getByRole('button', { name: 'Edit merged' }));

    const box = within(card).getByRole('textbox') as HTMLTextAreaElement;
    expect(box.value).toContain('<<<<<<< mine (library)');          // seeded from merged
    expect(within(card).getByText('4 conflict markers remain')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Save' })).toBeDisabled();

    const cleaned = 'line one\nrelease ten\nline eleven\nnew tail\n';
    await user.clear(box);
    await user.click(box);
    await user.paste(cleaned);
    expect(within(card).getByRole('button', { name: 'Save' })).toBeEnabled();
    await user.click(within(card).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(resolves(calls)).toHaveLength(1));
    expect(resolves(calls)[0].body).toEqual({
      verdict: 'edit', content: cleaned, ours_hash: H('a'), theirs_hash: H('b'),
    });
  });

  it('shows a 422\'s detail on the card', async () => {
    serve({
      get: () => createMockResponse(reviewOf([reviewItem()])),
      resolve: () => createMockResponse({ detail: 'the edited text still holds git conflict markers' }, 422),
    });
    const user = await openDialog();
    const card = await screen.findByRole('region', { name: 'alpha/one' });
    await user.click(within(card).getByRole('button', { name: 'Keep mine' }));
    expect(await within(card).findByRole('alert')).toHaveTextContent('still holds git conflict markers');
    expect(applyButton()).toBeDisabled();
  });

  it('recomputes and says the doc changed when a verdict answers 409', async () => {
    const calls = serve({
      get: () => createMockResponse(reviewOf([reviewItem()])),
      post: () => createMockResponse(reviewOf([reviewItem({ ours_hash: H('c') })])),
      resolve: () => createMockResponse({ detail: 'the doc changed since this review was computed' }, 409),
    });
    const user = await openDialog();
    const card = await screen.findByRole('region', { name: 'alpha/one' });
    await user.click(within(card).getByRole('button', { name: 'Keep mine' }));

    expect(await screen.findByText(/changed underneath you/)).toBeInTheDocument();
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ target: '1.5.3', ref: 'v1.5.3' });
  });

  it('reloads the review when the apply route reports skills-unresolved', async () => {
    const calls = serve({
      get: () => createMockResponse(reviewOf([])),
      applyStatus: () => createMockResponse({ error: 'skills-unresolved', unresolved: 1 }, 409),
    });
    const user = await openDialog();
    await waitFor(() => expect(applyButton()).toBeEnabled());
    const before = calls.filter((c) => c.url.includes('/api/skills/update-review/')).length;
    await user.click(applyButton());
    expect(await screen.findByText(/bundled skill docs need a decision; the review above has been reloaded/)).toBeInTheDocument();
    await waitFor(() => expect(
      calls.filter((c) => c.url.includes('/api/skills/update-review/')).length,
    ).toBeGreaterThan(before));
  });
});
