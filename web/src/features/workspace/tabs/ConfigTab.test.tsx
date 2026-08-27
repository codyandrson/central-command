import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigTab } from './ConfigTab';

type FetchResponse = {
  ok: boolean;
  status?: number;
  json: () => Promise<unknown>;
};

function jsonResponse(data: unknown, init: { ok?: boolean; status?: number } = {}): FetchResponse {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => data,
  };
}

function charterPayload(agentId: string, content: string, version = 3) {
  return {
    ok: true,
    result: {
      current: {
        version,
        content,
        rationale: `${agentId} rationale`,
        created_by: 'operator',
        created_at: '2026-07-30T10:00:00Z',
      },
      versions: [{ version, content, status: 'CURRENT', rationale: null, created_by: 'operator', created_at: null }],
    },
  };
}

describe('ConfigTab', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    localStorage.clear();
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('shows the current charter and its version metadata', async () => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/cc/charter/alpha') {
        return Promise.resolve(jsonResponse(charterPayload('alpha', 'alpha charter body')));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    render(<ConfigTab agentId="alpha" />);

    expect(await screen.findByText('alpha charter body')).toBeInTheDocument();
    expect(screen.getByText(/Charter · v3/)).toBeInTheDocument();
    expect(screen.getByText(/by operator/)).toBeInTheDocument();
  });

  it('saves an edited charter through the charter endpoint', async () => {
    const user = userEvent.setup();
    let postBody: string | undefined;

    globalThis.fetch = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && url === '/api/cc/charter/alpha') {
        postBody = String(init.body);
        return Promise.resolve(jsonResponse({ ok: true, result: { version: 4, warnings: [] } }));
      }
      if (url === '/api/cc/charter/alpha') {
        return Promise.resolve(jsonResponse(charterPayload('alpha', 'alpha charter body')));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    render(<ConfigTab agentId="alpha" />);
    await screen.findByText('alpha charter body');

    await user.click(screen.getByTitle('Edit'));
    const editor = await screen.findByRole('textbox', { name: /charter content/i });
    await user.type(editor, ' plus');
    await user.type(screen.getByRole('textbox', { name: /rationale/i }), 'tighten scope');
    await user.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/Saved as v4/)).toBeInTheDocument();
    expect(postBody).toBe(JSON.stringify({
      content: 'alpha charter body plus',
      rationale: 'tighten scope',
    }));
  });

  it('preserves an unsaved charter draft per agent across top-level agent switches', async () => {
    const user = userEvent.setup();

    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/cc/charter/alpha') {
        return Promise.resolve(jsonResponse(charterPayload('alpha', 'alpha charter')));
      }
      if (url === '/api/cc/charter/bravo') {
        return Promise.resolve(jsonResponse(charterPayload('bravo', 'bravo charter')));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    const { rerender } = render(<ConfigTab agentId="alpha" />);
    await screen.findByText('alpha charter');

    await user.click(screen.getByTitle('Edit'));
    const editor = await screen.findByRole('textbox', { name: /charter content/i });
    await user.type(editor, ' draft');
    expect(editor).toHaveValue('alpha charter draft');

    rerender(<ConfigTab agentId="bravo" />);

    await waitFor(() => {
      expect(screen.getByText('bravo charter')).toBeInTheDocument();
    });
    expect(screen.queryByDisplayValue('alpha charter draft')).not.toBeInTheDocument();

    rerender(<ConfigTab agentId="alpha" />);

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: /charter content/i })).toHaveValue('alpha charter draft');
    });
  });
});
