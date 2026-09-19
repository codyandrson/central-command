import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdateBadge } from './UpdateBadge';
import { _resetVersionCheck } from '@/lib/version-check';

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
