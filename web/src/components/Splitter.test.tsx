import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ListDetailSplit, SplitGroup, SplitSeparator, Panel } from './Splitter';

describe('SplitGroup', () => {
  it('renders a draggable separator between its panels', () => {
    render(
      <SplitGroup id="test-group">
        <Panel id="a">left</Panel>
        <SplitSeparator />
        <Panel id="b">right</Panel>
      </SplitGroup>,
    );

    expect(screen.getByText('left')).toBeInTheDocument();
    expect(screen.getByText('right')).toBeInTheDocument();
    expect(screen.getByRole('separator', { name: 'Resize panels' })).toBeInTheDocument();
  });
});

describe('ListDetailSplit', () => {
  // jsdom has no layout engine, so the measured width is 0 — i.e. the narrow
  // case, which must stay the plain stacked column with no splitter at all
  // (percentages in a column would be dividing HEIGHT).
  it('stacks without a separator below the side-by-side breakpoint', () => {
    render(
      <ListDetailSplit id="test-list-detail" aside={<p>list</p>}>
        <div>detail</div>
      </ListDetailSplit>,
    );

    expect(screen.getByText('list')).toBeInTheDocument();
    expect(screen.getByText('detail')).toBeInTheDocument();
    expect(screen.queryByRole('separator')).toBeNull();
  });
});
