import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ContextSettings } from './ContextSettings';

const SETTINGS = {
  drop_thinking: true, clear_tool_results: true, tool_results_keep_turns: 3,
  pressure_warning: true, output_headroom: true, output_headroom_tokens: 8192,
  summarize: true, summarize_threshold: 0.85,
  note_budget_private_chars: 2000, note_budget_operator_chars: 1500,
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

  it('shows the two note budgets and saves an edited one inside the full PUT', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (_url, init) => {
      if (init?.method === 'PUT') return ok({ ...SETTINGS, ...JSON.parse(String(init.body)) });
      return ok(SETTINGS);
    });
    global.fetch = fetchMock;
    const user = userEvent.setup();
    render(<ContextSettings />);

    expect(screen.getByText('Agent notes')).toBeInTheDocument();
    const priv = await screen.findByLabelText(/private notes per agent/i);
    const operator = screen.getByLabelText(/operator preferences, all agents/i);
    await waitFor(() => expect(priv).toBeEnabled());
    expect(priv).toHaveValue(2000);
    expect(operator).toHaveValue(1500);

    await user.clear(operator);
    await user.type(operator, '3000');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(body).toMatchObject({ note_budget_private_chars: 2000, note_budget_operator_chars: 3000 });
    await screen.findByText(/saved/i);
    expect(operator).toHaveValue(3000);
  });

  it('shows the backend refusal of an out-of-range budget', async () => {
    global.fetch = vi.fn<typeof fetch>(async (_url, init) => {
      if (init?.method === 'PUT') {
        return new Response(JSON.stringify({
          ok: false, error: 'Input should be greater than or equal to 1',
        }), { status: 502 });
      }
      return ok(SETTINGS);
    });
    const user = userEvent.setup();
    render(<ContextSettings />);

    const priv = await screen.findByLabelText(/private notes per agent/i);
    await waitFor(() => expect(priv).toBeEnabled());
    await user.clear(priv);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await screen.findByText(/greater than or equal to 1/);
    expect(screen.queryByText(/saved/i)).not.toBeInTheDocument();
  });
});
