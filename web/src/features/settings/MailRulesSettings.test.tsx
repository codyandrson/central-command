import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MailRulesSettings } from './MailRulesSettings';

const RULE = {
  id: 'rule-1', position: 0, action: 'dismiss' as const,
  criteria: { from_domain: 'newsletters.example.com' },
  exceptions: {},
  description: 'dismisses mail from newsletters.example.com',
  reason: 'noise', created_by: 'operator', created_at: '2026-09-01T00:00:00Z',
  revoked_at: null, revoked_reason: null, matched_count: 4, last_matched_at: null, reopened_count: 0,
};

function ok(result: unknown) {
  return new Response(JSON.stringify({ ok: true, result }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

describe('MailRulesSettings', () => {
  const originalFetch = global.fetch;
  afterEach(() => { global.fetch = originalFetch; vi.restoreAllMocks(); });

  it('renders the active rule list from a mocked fetch', async () => {
    global.fetch = vi.fn<typeof fetch>(async () => ok({ rules: [RULE] }));
    render(<MailRulesSettings />);

    await screen.findByText(RULE.description);
    expect(screen.getByText(/noise/)).toBeInTheDocument();
    expect(screen.getByText(/matched 4/)).toBeInTheDocument();
  });

  it('enables Create only after a successful preview', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (url, init) => {
      const u = String(url);
      if (u.includes('/preview')) {
        return ok({ queued_matches: 2, description: 'dismisses mail from example.com', samples: [
          { from: 'a@example.com', subject: 'Hi', date: '2026-09-20' },
        ] });
      }
      if (init?.method === 'POST') return ok({ ok: true, rule: RULE, swept: 2 });
      return ok({ rules: [] });
    });
    global.fetch = fetchMock;
    const user = userEvent.setup();
    render(<MailRulesSettings />);

    await screen.findByText(/No active rules/);

    const createButton = screen.getByRole('button', { name: /^create$/i });
    expect(createButton).toBeDisabled();

    await user.type(document.getElementById('rule-criteria-from_domain')!, 'example.com');
    await user.type(screen.getByLabelText(/^reason$/i), 'cut noise');
    await user.click(screen.getByRole('button', { name: /^preview$/i }));

    await screen.findByText('dismisses mail from example.com');
    expect(screen.getByText(/queued matches: 2/)).toBeInTheDocument();
    expect(screen.getByText(/a@example.com/)).toBeInTheDocument();

    expect(createButton).toBeEnabled();
    await user.click(createButton);

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([u]) => String(u) === '/api/cc/mail-rules');
      expect(createCall).toBeTruthy();
    });
  });

  it('posts to the revoke endpoint for the given rule id', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (url) => {
      const u = String(url);
      if (u.includes('/revoke')) return ok({ ok: true });
      return ok({ rules: [RULE] });
    });
    global.fetch = fetchMock;
    const user = userEvent.setup();
    render(<MailRulesSettings />);

    await screen.findByText(RULE.description);
    await user.click(screen.getByRole('button', { name: /^revoke$/i }));
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => {
      const revokeCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/revoke'));
      expect(revokeCall).toBeTruthy();
      expect(revokeCall?.[0]).toBe('/api/cc/mail-rules/rule-1/revoke');
    });
  });
});
