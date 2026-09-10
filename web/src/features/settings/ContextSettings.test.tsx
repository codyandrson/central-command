import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ContextSettings } from './ContextSettings';

const SETTINGS = {
  drop_thinking: true, clear_tool_results: true, tool_results_keep_turns: 3,
  pressure_warning: true, output_headroom: true, output_headroom_tokens: 8192,
  summarize: true, summarize_threshold: 0.85,
};

function ok(result: unknown) {
  return new Response(JSON.stringify({ ok: true, result }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

describe('ContextSettings', () => {
  const originalFetch = global.fetch;
  afterEach(() => { global.fetch = originalFetch; vi.restoreAllMocks(); });

  it('loads the toggles from the API and saves a flipped one back as a full PUT', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (_url, init) => {
      if (init?.method === 'PUT') return ok({ ...SETTINGS, ...JSON.parse(String(init.body)) });
      return ok(SETTINGS);
    });
    global.fetch = fetchMock;
    const user = userEvent.setup();
    render(<ContextSettings />);

    const drop = await screen.findByLabelText(/drop old reasoning/i);
    await waitFor(() => expect(drop).toBeEnabled());
    expect(drop).toBeChecked();

    await user.click(drop);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe('/api/context/settings');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toMatchObject({ drop_thinking: false, summarize: true });
    await screen.findByText(/saved/i);
  });

  it('shows the gateway error instead of a silently enabled form', async () => {
    global.fetch = vi.fn<typeof fetch>(async () => new Response(
      JSON.stringify({ ok: false, error: 'Central Command HTTP 502' }), { status: 502 },
    ));
    render(<ContextSettings />);
    await screen.findByText(/HTTP 502/);
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });
});
