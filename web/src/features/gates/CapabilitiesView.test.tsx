import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CapabilitiesView } from './CapabilitiesView';

const rpc = vi.fn();
vi.mock('@/contexts/GatewayContext', () => ({ useGateway: () => ({ rpc }) }));

const cap = (o: object) => ({
  kind: 'write', risk: 'low', arguments: [], description: '', reversibility: 'reversible',
  requires_acknowledgement: false, scope_forms: [], overridable: true,
  overridable_reason: 'a gated write may be relaxed', default_gate: 'human approval', ...o,
});
const ov = (o: object) => ({
  agent_id: 'drafter', scope: {}, scope_text: 'every use', shadow: { k: 0, n: 0 }, matches: 0,
  last_match: null, granted_by: 'operator', granted_at: '2026-10-19T10:00:00Z', revoked_at: null,
  reversibility: 'reversible', requires_acknowledgement: false, ...o,
});

describe('CapabilitiesView', () => {
  it('shows gates, acknowledgement, overridability and revokes with the real shape', async () => {
    const overrides = [
      ov({ id: 's', capability: 'catalog.tag', state: 'shadow', shadow: { k: 2, n: 5 } }),
      ov({ id: 'l', agent_id: 'scribe', capability: 'catalog.tag', state: 'live', scope_text: 'targets DOE' }),
    ];
    rpc.mockImplementation(async (m: string, p: { capability?: string }) => {
      if (m === 'capabilities.list') return { capabilities: [
        cap({ name: 'catalog.tag' }),
        cap({ name: 'mail.send', reversibility: 'irreversible', requires_acknowledgement: true }),
        cap({ name: 'work.bulk_dismiss', default_gate: 'auditor', overridable_reason: 'the auditor reviews it; an override may still replace that' }),
        cap({ name: 'graph.read', default_gate: 'none', overridable: false, overridable_reason: 'nothing to relax: it is not gated' }),
      ] };
      if (m === 'overrides.list') return { overrides: p.capability === 'catalog.tag' ? overrides : [] };
      return {};
    });
    render(<CapabilitiesView />);
    expect(await screen.findByText('in shadow 2 of 5')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    expect(screen.getByText('targets DOE')).toBeInTheDocument();
    expect(screen.getByText('requires acknowledgement')).toBeInTheDocument();
    expect(screen.getByText('default gate: auditor').parentElement).toHaveTextContent('overridable');
    expect(screen.getByText('default gate: auditor').parentElement).not.toHaveTextContent('not overridable');
    expect(screen.getByText('not overridable')).toBeInTheDocument();
    expect(screen.getByText('nothing to relax: it is not gated')).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole('button', { name: 'Revoke' })[0]);
    await waitFor(() => expect(rpc).toHaveBeenCalledWith(
      'overrides.revoke', { agentId: 'drafter', overrideId: 's', reason: '' },
    ));
  });
});
