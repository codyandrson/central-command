import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TopBar } from './TopBar';

vi.mock('./NerveLogo', () => ({
  default: () => <div data-testid="nerve-logo" />,
}));

function renderTopBar(props: Partial<React.ComponentProps<typeof TopBar>> = {}) {
  return render(
    <TopBar
      onSettings={vi.fn()}
      agentLogEntries={[]}
      tokenData={null}
      logGlow={false}
      eventEntries={[]}
      eventsVisible={false}
      logVisible={false}
      viewMode="chat"
      onViewModeChange={vi.fn()}
      {...props}
    />,
  );
}

describe('TopBar', () => {
  it('shows the tasks view toggle by default', () => {
    renderTopBar();

    expect(screen.getByRole('button', { name: /switch to tasks view/i })).toBeInTheDocument();
  });

  it('hides the tasks view toggle when kanban visibility is disabled', () => {
    renderTopBar({ showKanbanView: false });

    expect(screen.queryByRole('button', { name: /switch to tasks view/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /switch to chat view/i })).toBeInTheDocument();
  });

  it('does not render a top-bar Commands trigger', () => {
    renderTopBar();

    expect(screen.queryByRole('button', { name: /open command palette/i })).not.toBeInTheDocument();
  });

  it('shows per-tab attention badges when counts are non-zero', () => {
    renderTopBar({ attention: { chat: 2, decisions: 3, tasks: 1 } });
    // Each badge renders inside its tab button, labeled "N awaiting".
    const decisions = screen.getByRole('button', { name: /switch to decisions inbox/i });
    expect(decisions).toHaveTextContent('3');
    const chat = screen.getByRole('button', { name: /switch to chat view/i });
    expect(chat).toHaveTextContent('2');
    const tasks = screen.getByRole('button', { name: /switch to tasks view/i });
    expect(tasks).toHaveTextContent('1');
  });

  it('renders no badge for a zero count', () => {
    renderTopBar({ attention: { chat: 0, decisions: 5, tasks: 0 } });
    const chat = screen.getByRole('button', { name: /switch to chat view/i });
    // The chat tab label is "Chat" — no stray digits when the count is 0.
    expect(chat.textContent).not.toMatch(/\d/);
    expect(screen.getByRole('button', { name: /switch to decisions inbox/i })).toHaveTextContent('5');
  });

  it('caps very large counts at 99+', () => {
    renderTopBar({ attention: { chat: 0, decisions: 150, tasks: 0 } });
    expect(screen.getByRole('button', { name: /switch to decisions inbox/i })).toHaveTextContent('99+');
  });
});
