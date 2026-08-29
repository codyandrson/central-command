import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdateSettings } from './UpdateSettings';
import { _resetVersionCheck } from '@/lib/version-check';

function ok(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('UpdateSettings', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    _resetVersionCheck();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('reports up to date, and a manual check bypasses the hourly cache', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => ok({
      current: '1.0.5', latest: '1.0.5', updateAvailable: false, projectDir: '/srv/cc', checkedAt: 1,
    }));
    global.fetch = fetchMock;
    const user = userEvent.setup();
    render(<UpdateSettings />);

    await screen.findByText(/up to date/i);
    expect(fetchMock).toHaveBeenLastCalledWith('/api/version/check');

    await user.click(screen.getByRole('button', { name: /check for updates/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith('/api/version/check?force=1');
    expect(screen.queryByRole('button', { name: /update to v/i })).not.toBeInTheDocument();
  });

  it('offers the apply dialog when a newer version is published', async () => {
    global.fetch = vi.fn<typeof fetch>(async () => ok({
      current: '1.0.5', latest: '1.0.6', updateAvailable: true, projectDir: '/srv/cc', checkedAt: 1,
    }));
    const user = userEvent.setup();
    render(<UpdateSettings />);

    await user.click(await screen.findByRole('button', { name: /update to v1\.0\.6/i }));
    await screen.findByText('Update Available');
    expect(screen.getByText('Apply update now')).toBeInTheDocument();
  });
});
