/**
 * The priority filter was removed (2026-08-07, hardcoded 'normal' server-side
 * — filtering by it never narrowed anything) and replaced with an agent
 * filter. This covers: the pills are gone, and picking an agent from the
 * filter row updates filters.assignee.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { KanbanHeader } from './KanbanHeader';
import type { KanbanFilters } from './hooks/useKanban';

const EMPTY_FILTERS: KanbanFilters = { q: '', assignee: '', labels: [] };

function renderHeader(props: Partial<React.ComponentProps<typeof KanbanHeader>> = {}) {
  const onFiltersChange = vi.fn();
  render(
    <KanbanHeader
      filters={EMPTY_FILTERS}
      onFiltersChange={onFiltersChange}
      statusCounts={{}}
      onCreateTask={() => {}}
      {...props}
    />,
  );
  return { onFiltersChange };
}

describe('KanbanHeader filters', () => {
  it('has no priority filter pills', () => {
    renderHeader();
    fireEvent.click(screen.getByTitle('Toggle filters'));
    expect(screen.queryByText('Priority')).not.toBeInTheDocument();
    expect(screen.queryByText('Critical')).not.toBeInTheDocument();
  });

  it('narrows by agent via the filter row select', () => {
    const { onFiltersChange } = renderHeader({
      agentOptions: [
        { value: 'agent:alice', label: 'Alice' },
        { value: 'agent:bob', label: 'Bob' },
      ],
    });
    fireEvent.click(screen.getByTitle('Toggle filters'));

    fireEvent.change(screen.getByDisplayValue('All agents'), { target: { value: 'agent:bob' } });

    expect(onFiltersChange).toHaveBeenCalledWith({ ...EMPTY_FILTERS, assignee: 'agent:bob' });
  });

  it('shows the old-terminal history toggle and hidden count when wired', () => {
    renderHeader({ showOldTerminal: false, onToggleOldTerminal: vi.fn(), oldTerminalHiddenCount: 3 });
    expect(screen.getByTitle(
      'Done/failed/cancelled tasks that finished more than 24h ago are hidden (3 hidden) — click to show them',
    )).toBeInTheDocument();
  });
});
