import '@testing-library/jest-dom';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AgentMcpPanel } from './AgentMcpPanel';
import type { AgentMcp } from '../gates/useMcpTools';

// Field-for-field the shape tests/test_mcp_grants.py pins for GET /agents/{id}/mcp-overrides.
const DATA: AgentMcp = {
  agent_id: 'drafter',
  mcp_grants: ['mcp:echo-demo'],
  overrides: [
    { agent_id: 'drafter', server_id: 'echo-demo', tool_name: 'echo', gate: 'ungated',
      granted_by: 'operator', granted_at: '2026-11-10T09:30:00Z', revoked_at: null,
      default_gate: 'human approval' },
    { agent_id: 'drafter', server_id: 'echo-demo', tool_name: 'shout', gate: 'ungated',
      granted_by: 'operator', granted_at: '2026-11-01T09:30:00Z', revoked_at: '2026-11-02T09:30:00Z',
      default_gate: 'human approval' },
  ],
  servers: [{
    server_id: 'echo-demo', status: 'registered', retired_at: null,
    tools: [
      { tool_name: 'echo', description: 'Echo back the input text', default_gate: 'human approval',
        effective_gate: 'ungated', source: 'override' },
      { tool_name: 'shout', description: 'Upper-case it', default_gate: 'human approval',
        effective_gate: 'human approval', source: 'default' },
    ],
  }],
};

function setup(data: AgentMcp | null = DATA) {
  const onSetOverride = vi.fn().mockResolvedValue(undefined);
  const onRevoke = vi.fn().mockResolvedValue(undefined);
  render(<AgentMcpPanel agentId="drafter" data={data} error="" onSetOverride={onSetOverride} onRevoke={onRevoke} />);
  return { onSetOverride, onRevoke };
}

describe('AgentMcpPanel', () => {
  it('shows each tool with its effective gate and where it comes from', () => {
    setup();
    expect(screen.getByText('echo-demo')).toBeInTheDocument();
    expect(screen.getByText("from this agent's override")).toBeInTheDocument();
    expect(screen.getByText('from the default')).toBeInTheDocument();
    expect(screen.getByText('ungated')).toBeInTheDocument();
    expect(screen.getByText('human approval')).toBeInTheDocument();
  });

  it('says in one line that an agent without an mcp: grant has nothing to override', () => {
    setup({ agent_id: 'auditor', mcp_grants: [], overrides: [], servers: [] });
    expect(screen.getByText(/holds no MCP server grant/)).toBeInTheDocument();
  });

  it('revokes an override and narrows to human approval in one click each, with no dialog', async () => {
    const { onSetOverride, onRevoke } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Revoke override on echo' }));
    expect(onRevoke).toHaveBeenCalledWith('echo-demo', 'echo');
    await userEvent.click(screen.getByRole('button', { name: 'Require approval for echo for drafter' }));
    expect(onSetOverride).toHaveBeenCalledWith('echo-demo', 'echo', 'human approval');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('sets an override to ungated only after the acknowledgement', async () => {
    const { onSetOverride } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Set shout ungated for drafter' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByTestId('ungate-affected')).toHaveTextContent('drafter');
    expect(onSetOverride).not.toHaveBeenCalled();
    const confirm = within(dialog).getByRole('button', { name: 'Ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox'));
    await userEvent.click(confirm);
    expect(onSetOverride).toHaveBeenCalledWith('echo-demo', 'shout', 'ungated');
  });

  it('keeps revoked overrides to a collapsed count that expands', async () => {
    setup();
    const toggle = screen.getByRole('button', { name: /1 revoked override/ });
    expect(screen.queryByText(/revoked 2026-11-02 09:30/)).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText(/revoked 2026-11-02 09:30/)).toBeInTheDocument();
  });

  it('shows a server refusal inline', async () => {
    const { onRevoke } = setup();
    onRevoke.mockRejectedValue(new Error("'drafter' holds no active override for 'echo-demo'/'echo'"));
    await userEvent.click(screen.getByRole('button', { name: 'Revoke override on echo' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('holds no active override');
  });

  // A "human approval" override over an UNGATED default: revoking it returns the tool to ungated.
  const WIDENING: AgentMcp = {
    agent_id: 'drafter', mcp_grants: ['mcp:echo-demo'],
    overrides: [
      { agent_id: 'drafter', server_id: 'echo-demo', tool_name: 'echo', gate: 'human approval',
        granted_by: 'operator', granted_at: '2026-11-10T09:30:00Z', revoked_at: null, default_gate: 'ungated' },
      // Dormant: the agent holds no grant for this server, but the default is ungated.
      { agent_id: 'drafter', server_id: 'other-demo', tool_name: 'ping', gate: 'human approval',
        granted_by: 'operator', granted_at: '2026-11-10T09:30:00Z', revoked_at: null, default_gate: 'ungated' },
    ],
    servers: [{
      server_id: 'echo-demo', status: 'registered', retired_at: null,
      tools: [{ tool_name: 'echo', description: '', default_gate: 'ungated',
        effective_gate: 'human approval', source: 'override' }],
    }],
  };

  it('a revoke that widens opens the dialog and revokes only after the acknowledgement', async () => {
    const { onRevoke } = setup(WIDENING);
    await userEvent.click(screen.getByRole('button', { name: 'Revoke override on echo' }));
    const dialog = await screen.findByRole('dialog');
    expect(onRevoke).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/returns it to the tool's default, which is ungated/)).toBeInTheDocument();
    expect(within(dialog).getByTestId('ungate-affected')).toHaveTextContent('drafter');
    const confirm = within(dialog).getByRole('button', { name: 'Revoke and ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox'));
    await userEvent.click(confirm);
    expect(onRevoke).toHaveBeenCalledWith('echo-demo', 'echo');
  });

  it('a dormant override that would widen takes the dialog too', async () => {
    const { onRevoke } = setup(WIDENING);
    await userEvent.click(screen.getByRole('button', { name: 'Revoke override on other-demo / ping' }));
    const dialog = await screen.findByRole('dialog');
    expect(onRevoke).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole('checkbox'));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Revoke and ungate' }));
    expect(onRevoke).toHaveBeenCalledWith('other-demo', 'ping');
  });
});
