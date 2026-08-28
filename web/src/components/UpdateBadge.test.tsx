import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdateBadge } from './UpdateBadge';

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
