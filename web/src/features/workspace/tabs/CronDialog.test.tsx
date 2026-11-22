import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CronDialog } from './CronDialog';

vi.mock('@/contexts/SessionContext', () => ({ useSessionContext: () => ({ agentName: 'Kim' }) }));
vi.mock('@/hooks/useGatewayModelCatalog', () => ({ useGatewayModelCatalog: () => ({ models: [], error: null }) }));

describe('CronDialog', () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = vi.fn();
    HTMLDialogElement.prototype.close = vi.fn();
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response)) as typeof fetch;
  });

  it('offers no delivery-to-a-channel control and fetches only the taskable agents', () => {
    render(<CronDialog open onClose={vi.fn()} onSubmit={vi.fn()} mode="create" />);

    expect(screen.getByLabelText('Assign to agent')).toBeInTheDocument();
    expect(screen.queryByText(/delivery/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/delivery/i)).not.toBeInTheDocument();
    const urls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls).toEqual(['/api/crons/agents']);
  });
});
