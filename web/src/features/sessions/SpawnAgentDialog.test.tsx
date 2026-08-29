import type React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SpawnAgentDialog } from './SpawnAgentDialog';

/**
 * The chat "+" after the management split (The operator, 2026-07-25): it starts a
 * conversation and does nothing else.
 *
 * This file previously asserted the three-path dialog — hire, assign work,
 * assign a project. Those assertions were correct for a dialog that no longer
 * exists: hiring moved to the Agents page and tasking to the Tasks page, so
 * the tests are rewritten to the new contract rather than deleted. What is
 * worth guarding now is that the chat page cannot hire or file work, and that
 * it lists exactly the agents you can actually talk to.
 */

const mockRpc = vi.fn();
const mockRefreshSessions = vi.fn(async () => {});
const mockSetCurrentSession = vi.fn();

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={className}>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={className}>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/button', () => ({
  Button: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} />,
}));

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({ rpc: mockRpc }),
}));

vi.mock('@/contexts/SessionContext', () => ({
  useSessionContext: () => ({
    refreshSessions: mockRefreshSessions,
    setCurrentSession: mockSetCurrentSession,
  }),
}));

const AGENTS = {
  agents: [
    // `taskable` is deliberately false on one of these: chattability is no
    // longer tied to taskability, so a non-taskable agent must still appear.
    { id: 'jira-expert', name: 'Jira Expert', role: 'Jira hygiene', taskable: true, status: 'ACTIVE' },
    { id: 'inbox-triage', name: 'Inbox Triage', role: 'Triage email', taskable: false, status: 'ACTIVE' },
    { id: 'old-hand', name: 'Old Hand', taskable: true, status: 'RETIRED' },
  ],
};

function renderDialog(onOpenChange = vi.fn()) {
  return render(<SpawnAgentDialog open onOpenChange={onOpenChange} />);
}

describe('SpawnAgentDialog (chat only, after the management split)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
    mockRpc.mockImplementation(async (method: string) => {
      if (method === 'agents.list') return AGENTS;
      if (method === 'sessions.new') {
        return { sessionId: 'sess_abc123', sessionKey: 'agent:jira-expert:sess_abc123' };
      }
      throw new Error(`unexpected rpc ${method}`);
    });
  });

  it('offers only the conversation path', async () => {
    renderDialog();
    await waitFor(() => expect(mockRpc).toHaveBeenCalledWith('agents.list'));

    expect(screen.getByText('Start a conversation')).toBeInTheDocument();
    // The two jobs that moved must not be reachable from chat.
    expect(screen.queryByText('Hire an agent')).not.toBeInTheDocument();
    expect(screen.queryByText(/Assign work/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Assign a project/i)).not.toBeInTheDocument();
  });

  it('never asks the gateway to hire or to create a task', async () => {
    // The old assign path POSTed straight to /api/kanban/tasks, so a spy is
    // the assertion that matters — jsdom always provides `fetch`.
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    renderDialog();
    await waitFor(() => expect(mockRpc).toHaveBeenCalledWith('agents.list'));

    fireEvent.click(screen.getByText('Start conversation'));
    await waitFor(() => expect(mockRpc)
      .toHaveBeenCalledWith('sessions.new', { agentId: 'jira-expert' }));

    const methods = mockRpc.mock.calls.map((c) => c[0]);
    expect(methods).not.toContain('agents.hire');
    expect(methods).not.toContain('agents.templates');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('lists every ACTIVE agent, taskable or not, and excludes retired ones', async () => {
    renderDialog();
    await waitFor(() => expect(mockRpc).toHaveBeenCalledWith('agents.list'));

    // The trigger shows only the selection; open the listbox to see the rest.
    fireEvent.click(screen.getByLabelText('Agent to talk to'));

    // Non-taskable agents are chattable now — the old dialog filtered these out.
    await waitFor(() => expect(screen.getByText('Inbox Triage')).toBeInTheDocument());
    // Retired agents are not: reactivate from the Agents page first.
    expect(screen.queryByText('Old Hand')).not.toBeInTheDocument();
  });

  it('opens a parallel session and follows it', async () => {
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);
    await waitFor(() => expect(mockRpc).toHaveBeenCalledWith('agents.list'));

    fireEvent.click(screen.getByText('Start conversation'));

    await waitFor(() => expect(mockSetCurrentSession)
      .toHaveBeenCalledWith('agent:jira-expert:sess_abc123'));
    expect(mockRefreshSessions).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('surfaces a failure honestly and stays open', async () => {
    mockRpc.mockImplementation(async (method: string) => {
      if (method === 'agents.list') return AGENTS;
      throw new Error('agent is not on the active roster (retired or unknown)');
    });
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);
    await waitFor(() => expect(mockRpc).toHaveBeenCalledWith('agents.list'));

    fireEvent.click(screen.getByText('Start conversation'));

    await waitFor(() => expect(screen.getByText(/active roster/)).toBeInTheDocument());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
