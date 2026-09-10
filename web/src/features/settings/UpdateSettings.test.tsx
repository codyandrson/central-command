import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

  it('stages an uploaded zip and opens the apply dialog — the air-gapped path', async () => {
    // The check fails (no route / no network) — Update from file must still work.
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/version/check')) return new Response('nope', { status: 404 });
      if (url === '/api/update/upload') {
        return ok({ target: '1.0.6', current: '1.0.5', updateAvailable: true, projectDir: '/srv/cc' });
      }
      return ok({ mode: 'local', pending: false, inFlight: false, status: null, stage: { pending: false, inFlight: false, status: { state: 'success', phase: 'staged', target: '1.0.6' } } });
    });
    global.fetch = fetchMock;
    render(<UpdateSettings />);

    await screen.findByText(/check failed/i);
    expect(screen.getByRole('button', { name: /update from file/i })).toBeEnabled();

    const file = new File(['PK'], 'central-command-1.0.6.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('Release zip file'), { target: { files: [file] } });

    await screen.findByText('Update Available');
    expect(fetchMock).toHaveBeenCalledWith('/api/update/upload', expect.objectContaining({ method: 'POST' }));
    await screen.findByText('Apply update now');
  });

  it('reports a zip that is not newer instead of opening the dialog', async () => {
    global.fetch = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === '/api/update/upload') return ok({ target: '1.0.5', current: '1.0.5', updateAvailable: false });
      return new Response('nope', { status: 404 });
    });
    render(<UpdateSettings />);

    const file = new File(['PK'], 'central-command-1.0.5.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('Release zip file'), { target: { files: [file] } });

    await screen.findByText(/not newer than the running v1\.0\.5/i);
    expect(screen.queryByText('Update Available')).not.toBeInTheDocument();
  });
});
