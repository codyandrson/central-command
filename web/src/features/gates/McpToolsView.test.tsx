import '@testing-library/jest-dom';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { McpToolsView } from './McpToolsView';

// Field-for-field the shape tests/test_mcp_grants.py pins for GET /mcp/servers.
const tool = (o: object) => ({
  server_id: 'echo-demo', tool_name: 'echo', default_gate: 'human approval',
  description: 'Echo back the input text', overrides: [], ...o,
});
const server = (o: object) => ({
  id: 'echo-demo', status: 'registered', retired_at: null, holders: ['drafter', 'rivers-agent'],
  tools: [tool({})], ...o,
});

let servers: unknown[];
let calls: Array<{ url: string; method: string; body?: string }>;

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

beforeEach(() => {
  calls = [];
  servers = [server({})];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body as string | undefined });
    if (init?.method === 'PATCH' || init?.method === 'DELETE') return respond({ ok: true, result: {} });
    return respond({ ok: true, result: { servers } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('McpToolsView', () => {
  it('renders the server, its tool, the default badge and the active overrides', async () => {
    servers = [server({
      status: 'registered', retired_at: '2026-11-01T10:00:00Z',
      tools: [
        tool({ overrides: [{ agent_id: 'drafter', gate: 'ungated', granted_at: '2026-11-10T09:30:00Z' }] }),
        tool({ tool_name: 'shout', default_gate: 'ungated', description: 'Upper-case it' }),
      ],
    })];
    render(<McpToolsView />);
    expect(await screen.findByText('echo-demo')).toBeInTheDocument();
    expect(screen.getByText('retired')).toBeInTheDocument();
    expect(screen.getByText('Echo back the input text')).toBeInTheDocument();
    expect(screen.getByText('default: human approval')).toBeInTheDocument();
    expect(screen.getByText('default: ungated')).toBeInTheDocument();
    expect(screen.getByText('drafter')).toBeInTheDocument();
    expect(screen.getByText('since 2026-11-10 09:30')).toBeInTheDocument();
    expect(screen.getByText('No agent overrides.')).toBeInTheDocument();
  });

  it('shows one plain sentence when no server is registered', async () => {
    servers = [];
    render(<McpToolsView />);
    expect(await screen.findByText(
      /MCP servers appear here once one is registered through the MCP pipeline; nothing is gated differently until then\./,
    )).toBeInTheDocument();
  });

  it('revokes an override through the proxy in one click and re-reads', async () => {
    servers = [server({
      tools: [tool({ overrides: [{ agent_id: 'drafter', gate: 'ungated', granted_at: '2026-11-10T09:30:00Z' }] })],
    })];
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke drafter override on echo' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    const del = calls.find((c) => c.method === 'DELETE')!;
    expect(del.url).toBe('/api/cc/mcp-overrides/drafter?server_id=echo-demo&tool_name=echo');
    // No dialog for a revoke, and the list is read again afterwards.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET').length).toBe(2));
  });

  it('makes a default ungated only after the acknowledgement, listing holders without an override', async () => {
    servers = [server({
      tools: [tool({ overrides: [{ agent_id: 'rivers-agent', gate: 'human approval', granted_at: null }] })],
    })];
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Make echo ungated by default' }));
    const dialog = await screen.findByRole('dialog');
    // rivers-agent has its own override, so only drafter is reached by the default.
    const affected = within(dialog).getByTestId('ungate-affected');
    expect(affected).toHaveTextContent('drafter');
    expect(affected).not.toHaveTextContent('rivers-agent');
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);

    const confirm = within(dialog).getByRole('button', { name: 'Ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox'));
    await userEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH')!;
    expect(patch.url).toBe('/api/cc/mcp/servers/echo-demo/tools/echo');
    expect(JSON.parse(patch.body!)).toEqual({ default_gate: 'ungated' });
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET').length).toBe(2));
  });

  it('narrows a default back to human approval with one click and no dialog', async () => {
    servers = [server({ tools: [tool({ default_gate: 'ungated' })] })];
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Require approval by default for echo' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === 'PATCH')!.body!)).toEqual({ default_gate: 'human approval' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('surfaces a server refusal inline', async () => {
    servers = [server({ tools: [tool({ overrides: [{ agent_id: 'drafter', gate: 'ungated', granted_at: null }] })] })];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return respond({ ok: false, error: "'drafter' holds no active override for 'echo-demo'/'echo'" }, 409);
      }
      return respond({ ok: true, result: { servers } });
    });
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke drafter override on echo' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("holds no active override");
  });

  it('a revoke that widens opens the dialog and revokes only after the acknowledgement', async () => {
    servers = [server({
      tools: [tool({
        default_gate: 'ungated',
        overrides: [{ agent_id: 'drafter', gate: 'human approval', granted_at: '2026-11-10T09:30:00Z' }],
      })],
    })];
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke drafter override on echo' }));
    const dialog = await screen.findByRole('dialog');
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(within(dialog).getByTestId('ungate-affected')).toHaveTextContent('drafter');
    const confirm = within(dialog).getByRole('button', { name: 'Revoke and ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox'));
    await userEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(calls.find((c) => c.method === 'DELETE')!.url)
      .toBe('/api/cc/mcp-overrides/drafter?server_id=echo-demo&tool_name=echo');
  });

  it('a "human approval" override over a gated default revokes in one click (does not widen)', async () => {
    servers = [server({
      tools: [tool({ overrides: [{ agent_id: 'drafter', gate: 'human approval', granted_at: null }] })],
    })];
    render(<McpToolsView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke drafter override on echo' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
