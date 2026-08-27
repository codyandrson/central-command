import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SignalsPanel } from './AgentsView';
import type { CoachingSignal } from './types';

const SIGNALS: CoachingSignal[] = [
  { kind: 'rejection', event_id: 2, feedback: 'never propose past dates', at: '2026-08-15T10:00:00Z' },
  { kind: 'task-outcome', event_id: 1, feedback: 'closed without a region', intent: 'task_abc', author: 'agent' },
];

describe('SignalsPanel filter', () => {
  it('shows every signal with no filter text', () => {
    render(<SignalsPanel signals={SIGNALS} onCoach={vi.fn()} />);
    expect(screen.getByText('“never propose past dates”')).toBeInTheDocument();
    expect(screen.getByText('“closed without a region”')).toBeInTheDocument();
  });

  it('narrows the list by kind/intent/feedback, case-insensitively', async () => {
    render(<SignalsPanel signals={SIGNALS} onCoach={vi.fn()} />);
    await userEvent.type(screen.getByPlaceholderText(/filter signals/i), 'REJECTION');

    expect(screen.getByText('“never propose past dates”')).toBeInTheDocument();
    expect(screen.queryByText('“closed without a region”')).not.toBeInTheDocument();
  });
});
