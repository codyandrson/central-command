import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CronDialog } from './CronDialog';

vi.mock('@/contexts/SessionContext', () => ({ useSessionContext: () => ({ agentName: 'Kim' }) }));
vi.mock('@/hooks/useGatewayModelCatalog', () => ({ useGatewayModelCatalog: () => ({ models: [], error: null }) }));

describe('CronDialog delivery section', () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = vi.fn();
    HTMLDialogElement.prototype.close = vi.fn();
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response)) as typeof fetch;
  });

  it('is marked under construction, disabled, and never fetches channels', () => {
    render(<CronDialog open onClose={vi.fn()} onSubmit={vi.fn()} mode="create" />);

    expect(screen.getByText(/no external messaging channel is wired to this deployment yet/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery mode (under construction)')).toBeDisabled();
    expect(screen.queryByLabelText('Delivery channel')).not.toBeInTheDocument();
    const urls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls).not.toContain('/api/channels');
  });
});
