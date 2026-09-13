import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CronsTab } from './CronsTab';

const baseCrons = () => ({
  jobs: [] as unknown[],
  engine: null as { running: boolean; reason?: string; tickSeconds?: number } | null,
  actions: {},
  isLoading: false,
  error: null as string | null,
  fetchJobs: vi.fn(),
  toggleJob: vi.fn(),
  runJob: vi.fn(),
  fetchRuns: vi.fn(),
  addJob: vi.fn(),
  updateJob: vi.fn(),
  deleteJob: vi.fn(),
  setEngineRunning: vi.fn(),
});

const mockUseCrons = vi.fn(baseCrons);

vi.mock('../hooks/useCrons', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../hooks/useCrons')>()),
  useCrons: () => mockUseCrons(),
}));

vi.mock('./CronDialog', () => ({
  CronDialog: () => null,
}));

vi.mock('@/contexts/SessionContext', () => ({
  useSessionContext: () => ({ refreshSessions: vi.fn() }),
}));

describe('CronsTab', () => {
  it('warns loudly when the engine is stopped but schedules are enabled', () => {
    mockUseCrons.mockReturnValue({
      ...baseCrons(),
      engine: { running: false, reason: 'not started' },
      jobs: [{
        id: 'mail-poll', name: 'Mail poll', enabled: true,
        scheduleKind: 'every', everyMs: 300000,
        payloadKind: 'systemEvent', actionKind: 'feed.poll',
      }] as never,
    });

    render(<CronsTab />);

    expect(screen.getByText(/heartbeat engine stopped/i)).toBeInTheDocument();
    expect(screen.getByText(/1 enabled schedule is dormant — nothing fires until the engine starts \(not started\)\./i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start heartbeat engine/i })).toBeInTheDocument();
  });

  it('renders a built-in run result as task links and labelled facts, never a JSON dump', async () => {
    const onOpenTask = vi.fn();
    mockUseCrons.mockReturnValue({
      ...baseCrons(),
      engine: { running: true, reason: 'running' },
      fetchRuns: vi.fn(async () => [{
        timestamp: '2026-09-13T06:30:00Z', status: 'ok', trigger: 'manual',
        result: { drift: true, task_ids: ['abcdef12-0000'], queued: 1, credentials: ['kilo'], backlog: { kilo: 3 } },
      }]),
      jobs: [{
        id: 'litellm-discovery', name: 'LiteLLM model autodiscovery', enabled: true,
        scheduleKind: 'cron', schedule: '30 6 * * *',
        payloadKind: 'systemEvent', actionKind: 'litellm.discovery',
      }] as never,
    });

    render(<CronsTab onOpenTask={onOpenTask} />);

    expect(screen.getByText('Built-in: LiteLLM model autodiscovery')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show history/i }));
    const link = await screen.findByRole('button', { name: /open task abcdef12-0000/i });
    fireEvent.click(link);
    expect(onOpenTask).toHaveBeenCalledWith('abcdef12-0000');
    await waitFor(() => expect(screen.getByText('backlog: kilo 3')).toBeInTheDocument());
    expect(screen.getByText('credentials: kilo')).toBeInTheDocument();
    expect(screen.getByText('manual')).toBeInTheDocument();
    expect(screen.queryByText(/"task_ids"/)).not.toBeInTheDocument();
  });

  it('shows a calm running state when the engine is on, with the tick interval', () => {
    mockUseCrons.mockReturnValue({
      ...baseCrons(),
      engine: { running: true, reason: 'running', tickSeconds: 15 },
    });

    render(<CronsTab />);

    expect(screen.getByText(/heartbeat engine running/i)).toBeInTheDocument();
    expect(screen.getByText(/checked every 15s/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /stop heartbeat engine/i })).toBeInTheDocument();
  });

  it('shows the fetch error directly when loading crons fails', () => {
    mockUseCrons.mockReturnValue({
      ...baseCrons(),
      error: 'Failed to fetch crons',
    });

    render(<CronsTab />);

    expect(screen.getByText('Failed to fetch crons')).toBeInTheDocument();
    expect(screen.queryByText(/no scheduled tasks yet/i)).not.toBeInTheDocument();
  });
});
