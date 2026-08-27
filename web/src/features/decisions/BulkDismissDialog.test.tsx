import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BulkDismissDialog } from './BulkDismissDialog';

function jsonResponse(ok: boolean, body: unknown) {
  return { ok, json: async () => body };
}

function renderDialog(onDismissed = vi.fn(), initialQuery = '') {
  const onOpenChange = vi.fn();
  render(
    <BulkDismissDialog
      open
      onOpenChange={onOpenChange}
      initialQuery={initialQuery}
      onDismissed={onDismissed}
    />,
  );
  return { onOpenChange };
}

describe('BulkDismissDialog', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('pre-fills the query from a sender card and leaves Execute disabled until preview succeeds', () => {
    renderDialog(vi.fn(), 'from:someone@example.com');

    expect(screen.getByLabelText('Bulk dismiss query')).toHaveValue('from:someone@example.com');
    expect(screen.getByRole('button', { name: /execute/i })).toBeDisabled();
  });

  it('fills the query from a preset without running it', async () => {
    const user = userEvent.setup({ delay: null });
    renderDialog();

    await user.click(screen.getByText('Unsubscribe, older than 2y'));

    expect(screen.getByLabelText('Bulk dismiss query')).toHaveValue('unsubscribe older_than:2y');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('enables Execute after a successful preview, then sends the digest through and reports success', async () => {
    const fetchMock = vi.mocked(global.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(true, {
      query: 'from:someone@example.com',
      provider_matches: 12,
      unprocessed: 9,
      samples: [{ from: 'someone@example.com', subject: 'Sale!', date: '2026-01-01' }],
      match_digest: 'abc123',
    }) as unknown as Response);
    fetchMock.mockResolvedValueOnce(jsonResponse(true, {
      provider_matches: 12,
      dismissed: 9,
    }) as unknown as Response);

    const onDismissed = vi.fn();
    const user = userEvent.setup({ delay: null });
    renderDialog(onDismissed, 'from:someone@example.com');

    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    await waitFor(() => expect(screen.getByTestId('bulk-dismiss-preview')).toBeInTheDocument());
    expect(screen.getByText(/12 matched in Gmail/)).toBeInTheDocument();

    const executeButton = screen.getByRole('button', { name: /execute/i });
    expect(executeButton).toBeEnabled();
    await user.click(executeButton);

    await waitFor(() => expect(onDismissed).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('bulk-dismiss-result')).toHaveTextContent('Dismissed 9 of 12 matched.');

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/work/bulk_dismiss', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ query: 'from:someone@example.com', match_digest: 'abc123' }),
    }));
  });

  it('renders a 400 detail verbatim and keeps Execute disabled', async () => {
    const fetchMock = vi.mocked(global.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(false, {
      detail: 'query matched too many messages for a single list (provider cap ~2,500 refs) — narrow it',
    }) as unknown as Response);

    const user = userEvent.setup({ delay: null });
    renderDialog(vi.fn(), 'category:promotions');

    await user.click(screen.getByRole('button', { name: /^preview$/i }));

    expect(await screen.findByText(/narrow it/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /execute/i })).toBeDisabled();
  });

  it('invalidates a successful preview when the query is edited afterward', async () => {
    const fetchMock = vi.mocked(global.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(true, {
      query: 'from:a@example.com',
      provider_matches: 1,
      unprocessed: 1,
      samples: [],
      match_digest: 'digest-1',
    }) as unknown as Response);

    const user = userEvent.setup({ delay: null });
    renderDialog(vi.fn(), 'from:a@example.com');

    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /execute/i })).toBeEnabled());

    await user.type(screen.getByLabelText('Bulk dismiss query'), 'x');

    expect(screen.getByRole('button', { name: /execute/i })).toBeDisabled();
  });
});
