import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryItem } from './MemoryItem';
import type { Memory } from '@/types';

describe('MemoryItem', () => {
  it('shows a private badge for scope:private items', () => {
    const memory: Memory = { type: 'item', text: 'a private fact', scope: 'private' };
    render(<MemoryItem memory={memory} onDelete={vi.fn()} />);
    expect(screen.getByText('private')).toBeInTheDocument();
  });

  it('shows no badge for scope:shared or unscoped items', () => {
    const shared: Memory = { type: 'item', text: 'a shared fact', scope: 'shared' };
    const { rerender } = render(<MemoryItem memory={shared} onDelete={vi.fn()} />);
    expect(screen.queryByText('private')).not.toBeInTheDocument();

    const unscoped: Memory = { type: 'item', text: 'an unscoped fact' };
    rerender(<MemoryItem memory={unscoped} onDelete={vi.fn()} />);
    expect(screen.queryByText('private')).not.toBeInTheDocument();
  });
});
