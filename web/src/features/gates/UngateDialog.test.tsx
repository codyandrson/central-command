import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { UngateDialog } from './UngateDialog';

function open(over: Partial<Parameters<typeof UngateDialog>[0]> = {}) {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  const onOpenChange = vi.fn();
  render(
    <UngateDialog
      open onOpenChange={onOpenChange} serverId="echo-demo" toolName="echo"
      scope="default" agents={['drafter', 'rivers-agent']} onConfirm={onConfirm} {...over}
    />,
  );
  return { onConfirm, onOpenChange };
}

describe('UngateDialog', () => {
  it('names the server, tool and affected agents and states what ungated means', () => {
    open();
    expect(screen.getByText('Ungate echo?')).toBeInTheDocument();
    expect(screen.getAllByText('echo-demo').length).toBeGreaterThan(0);
    const affected = screen.getByTestId('ungate-affected');
    expect(affected).toHaveTextContent('drafter');
    expect(affected).toHaveTextContent('rivers-agent');
    expect(screen.getByTestId('ungate-meaning')).toHaveTextContent(
      'Ungated means the agent calls this tool directly, with no proposal and no approval.');
  });

  it('keeps Ungate disabled until the acknowledgement is ticked, then confirms once', async () => {
    const { onConfirm, onOpenChange } = open();
    const confirm = screen.getByRole('button', { name: 'Ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('checkbox'));
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('shows the server refusal inline and stays open', async () => {
    const { onOpenChange } = open({
      onConfirm: vi.fn().mockRejectedValue(new Error("no mcp_tool 'echo-demo'/'echo'")),
    });
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Ungate' }));
    expect(await screen.findByText("no mcp_tool 'echo-demo'/'echo'")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('says so when no agent holds the server yet', () => {
    open({ agents: [] });
    expect(screen.getByTestId('ungate-affected')).toHaveTextContent('No agent holds this server yet');
  });

  it('names the one agent for an override', () => {
    open({ scope: 'override', agents: ['drafter'] });
    expect(screen.getByTestId('ungate-affected')).toHaveTextContent('drafter');
    expect(screen.queryByText(/every agent that holds this server/)).not.toBeInTheDocument();
  });

  it('the revoke scope says it returns the agent to the ungated default and confirms as "Revoke and ungate"', async () => {
    const { onConfirm } = open({ scope: 'revoke', agents: ['drafter'] });
    expect(screen.getByText(/returns it to the tool's default, which is ungated/)).toBeInTheDocument();
    expect(screen.getByTestId('ungate-meaning')).toBeInTheDocument();
    const confirm = screen.getByRole('button', { name: 'Revoke and ungate' });
    expect(confirm).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
