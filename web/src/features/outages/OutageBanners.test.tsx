/**
 * The outage banners: one per DOWN dependency, one per dependency with work
 * failing against a healthy one, Retry now on each, and a `dependency.up` push
 * that takes a banner away. The gateway is mocked at the context seam (the
 * `useAttention` precedent): a stable `subscribe`, an `rpc` the test scripts.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GatewayEvent } from '@/types';
import type { OutageDependency } from './types';

type Handler = (msg: GatewayEvent) => void;
const handlers = new Set<Handler>();
const subscribe = (h: Handler) => { handlers.add(h); return () => { handlers.delete(h); }; };
const rpc = vi.fn();

vi.mock('@/contexts/GatewayContext', () => ({
  // STABLE identity for `subscribe` — a fresh closure per render would tear the
  // subscription down and re-add it on every render.
  useGateway: () => ({ rpc, connectionState: 'connected', subscribe }),
}));

const { OutageBanners } = await import('./OutageBanners');

const none = { sessions: 0, proposals: 0, tasks: 0, work_items: 0, ingest_jobs: 0 };
const dep = (name: string, over: Partial<OutageDependency> = {}): OutageDependency => ({
  name, state: 'up', checked_at: null, down_since: null, waiting: { ...none },
  interrupted: 0, failing: [], total: 0, ...over,
});
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

let list: OutageDependency[];

beforeEach(() => {
  handlers.clear();
  rpc.mockReset();
  list = [
    dep('graph', {
      state: 'down', down_since: minutesAgo(125), total: 7,
      waiting: { sessions: 2, proposals: 1, tasks: 1, work_items: 2, ingest_jobs: 1 },
      interrupted: 1,
    }),
    dep('jira', { state: 'down', down_since: minutesAgo(14), total: 3,
      waiting: { ...none, proposals: 3 } }),
    dep('embedder', { failing: [
      { kind: 'task', id: 't1', attempts: 3, last_error: 'HTTP 403 forbidden' },
      { kind: 'task', id: 't2', attempts: 4, last_error: 'other' },
    ] }),
    dep('local', { state: 'n/a' }),
  ];
  rpc.mockImplementation(async (method: string) => {
    if (method === 'outages.list') return { dependencies: list };
    if (method === 'outages.retry') return { probe: 'up', driven: { ...none, sessions: 2, tasks: 3 } };
    return {};
  });
});

async function push(event: string) {
  await act(async () => {
    for (const h of handlers) h({ type: 'event', event, payload: {} });
    await new Promise((r) => setTimeout(r, 550));   // the 400ms debounce
  });
}

describe('OutageBanners', () => {
  it('renders one banner per down dependency with its duration and total, plus one failing banner', async () => {
    render(<OutageBanners />);

    const graph = await screen.findByTestId('outage-banner-graph');
    expect(graph).toHaveTextContent('graph down for 2h 5m');
    expect(graph).toHaveTextContent('7 items waiting');
    const jira = screen.getByTestId('outage-banner-jira');
    expect(jira).toHaveTextContent('jira down for 14m');
    expect(jira).toHaveTextContent('3 items waiting');
    expect(screen.getAllByTestId(/^outage-banner-/)).toHaveLength(2);

    const failing = screen.getByTestId('failing-banner-embedder');
    expect(failing).toHaveTextContent('2 items keep failing against a healthy embedder: HTTP 403 forbidden');
    expect(screen.queryByTestId('outage-banner-local')).not.toBeInTheDocument();
  });

  it('shows the per-kind breakdown and the interrupted count on expand', async () => {
    render(<OutageBanners />);
    await screen.findByTestId('outage-banner-graph');
    expect(screen.queryByTestId('outage-breakdown-graph')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /7 items waiting/ }));

    const breakdown = screen.getByTestId('outage-breakdown-graph');
    expect(breakdown).toHaveTextContent('2 sessions');
    expect(breakdown).toHaveTextContent('2 mail items');
    expect(breakdown).toHaveTextContent('1 graph ingest jobs');
    expect(breakdown).toHaveTextContent('1 interrupted by a restart');
  });

  it('calls outages.retry with the dependency and shows the answer inline', async () => {
    render(<OutageBanners />);
    const graph = await screen.findByTestId('outage-banner-graph');

    await userEvent.click(within(graph).getByRole('button', { name: 'Retry now: graph' }));

    await waitFor(() => expect(rpc).toHaveBeenCalledWith(
      'outages.retry', { dependency: 'graph', includeInterrupted: false }));
    expect(await screen.findByTestId('outage-result-graph')).toHaveTextContent('drove 5');
    // The others are untouched.
    expect(screen.queryByTestId('outage-result-jira')).not.toBeInTheDocument();
  });

  it('says so when the probe is still down', async () => {
    rpc.mockImplementation(async (method: string) => (
      method === 'outages.list' ? { dependencies: list }
        : { probe: 'down', driven: { ...none } }));
    render(<OutageBanners />);
    await userEvent.click(await screen.findByRole('button', { name: 'Retry now: jira' }));
    expect(await screen.findByTestId('outage-result-jira')).toHaveTextContent('probe still down');
  });

  it('retries a failing-against-a-healthy-dependency banner too', async () => {
    render(<OutageBanners />);
    const failing = await screen.findByTestId('failing-banner-embedder');
    await userEvent.click(within(failing).getByRole('button', { name: 'Retry now: embedder' }));
    await waitFor(() => expect(rpc).toHaveBeenCalledWith(
      'outages.retry', { dependency: 'embedder', includeInterrupted: false }));
  });

  it('takes a dependency\'s banner away on dependency.up and leaves the others', async () => {
    render(<OutageBanners />);
    await screen.findByTestId('outage-banner-graph');

    list = list.map((d) => (d.name === 'graph'
      ? dep('graph', { checked_at: minutesAgo(0) }) : d));
    await push('cc.dependency.up');

    await waitFor(() => expect(screen.queryByTestId('outage-banner-graph')).not.toBeInTheDocument());
    expect(screen.getByTestId('outage-banner-jira')).toBeInTheDocument();
    expect(screen.getByTestId('failing-banner-embedder')).toBeInTheDocument();
  });

  it.each([
    'cc.dependency.down', 'cc.dependency.up', 'cc.session.resume_parked',
    'cc.proposal.execution_parked', 'cc.proposal.execution_interrupted',
    'cc.task.run_parked', 'cc.work.released', 'cc.retry.failing_despite_probe',
    'cc.session.resume_retried', 'cc.proposal.execution_retried',
    'cc.proposal.execution_dismissed', 'cc.task.run_retried', 'cc.outage.retry_requested',
  ])('refetches on the %s push', async (event) => {
    render(<OutageBanners />);
    await screen.findByTestId('outage-banner-graph');
    rpc.mockClear();
    await push(event);
    expect(rpc).toHaveBeenCalledWith('outages.list');
  });

  it('ignores unrelated pushes', async () => {
    render(<OutageBanners />);
    await screen.findByTestId('outage-banner-graph');
    rpc.mockClear();
    await push('cc.session.step');
    expect(rpc).not.toHaveBeenCalled();
  });

  it('renders nothing when every dependency is up and nothing is failing', async () => {
    list = [dep('graph'), dep('local', { state: 'n/a' })];
    const { container } = render(<OutageBanners />);
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('outages.list'));
    expect(container).toBeEmptyDOMElement();
  });
});
