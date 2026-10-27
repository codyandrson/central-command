import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { OverridesPanel } from './AgentsView';
import { AuthorOverrideDialog } from './AgentDialogs';
import type { AgentRow, CapabilityRow, GateOverride } from './types';

// Field-for-field the shape tests/test_overrides_wire.py pins.
const row = (over: Partial<GateOverride>): GateOverride => ({
  id: 'o1', agent_id: 'drafter', capability: 'catalog.tag', scope: {},
  scope_text: 'every use', state: 'shadow', shadow: { k: 2, n: 5 }, matches: 2,
  last_match: '2026-10-20T10:00:00Z', granted_by: 'operator',
  granted_at: '2026-10-19T10:00:00Z', revoked_at: null,
  reversibility: 'reversible', requires_acknowledgement: false, ...over,
});

const FIXTURE = [
  row({ id: 's' }),
  row({ id: 'l', capability: 'jira.add_comment', scope: { target: ['DOE'] },
    scope_text: 'targets DOE', state: 'live', shadow: { k: 0, n: 0 } }),
  row({ id: 'u', capability: 'catalog.untag', state: 'suspended', scope_text: 'suspended scope' }),
  row({ id: 'r', capability: 'jira.transition', state: 'revoked', revoked_at: '2026-10-21T10:00:00Z',
    scope_text: 'revoked scope' }),
];

Element.prototype.scrollIntoView = vi.fn();

describe('OverridesPanel', () => {
  it('renders each state and revokes in one click with the override id', async () => {
    const onRevoke = vi.fn();
    render(<OverridesPanel overrides={FIXTURE} onAuthor={vi.fn()} onRevoke={onRevoke} />);
    expect(screen.getByText('in shadow 2 of 5')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    expect(screen.getByText('targets DOE')).toBeInTheDocument();
    expect(screen.getByText('suspended')).toBeInTheDocument();
    expect(screen.getByText('revoked')).toBeInTheDocument();
    const buttons = screen.getAllByRole('button', { name: 'Revoke' });
    expect(buttons).toHaveLength(3); // revoked rows have none
    await userEvent.click(buttons[1]);
    expect(onRevoke).toHaveBeenCalledWith('l');
  });
});

const AGENT = { id: 'drafter', name: 'Drafter' } as AgentRow;
const cap = (over: Partial<CapabilityRow>): CapabilityRow => ({
  name: 'catalog.tag', kind: 'write', default_gate: 'human', risk: 'a wrong tag',
  arguments: ['tag'], description: '', reversibility: 'reversible',
  requires_acknowledgement: false, scope_forms: ['argument', 'target', 'schedule'],
  overridable: true, overridable_reason: 'ok', ...over,
});
const CATALOG = [
  cap({}),
  cap({ name: 'mail.send', reversibility: 'irreversible', requires_acknowledgement: true,
    risk: 'sends real mail', scope_forms: ['argument', 'schedule'] }),
  cap({ name: 'litellm.apply_config_change', requires_acknowledgement: true,
    risk: 'rewrites the gateway', scope_forms: ['schedule'] }),
  cap({ name: 'sandbox.exec', default_gate: 'none', overridable: false }),
];

async function choose(name: string) {
  await userEvent.click(screen.getByRole('button', { name: /capability to override/i }));
  await userEvent.click(await screen.findByRole('option', { name }));
}
const submit = () => screen.getByRole('button', { name: 'Author override' });
const dialog = (onAuthor = vi.fn().mockResolvedValue({
  override: row({}), warnings: [], overrides: [] }), agent = AGENT) =>
  render(<AuthorOverrideDialog agent={agent} capabilities={CATALOG} open
    onOpenChange={vi.fn()} onAuthor={onAuthor} />);

describe('AuthorOverrideDialog', () => {
  it('refuses to submit with no capability and offers only overridable ones', async () => {
    dialog();
    expect(submit()).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /capability to override/i }));
    expect(screen.queryByRole('option', { name: 'sandbox.exec' })).not.toBeInTheDocument();
  });

  it('submits the scope exactly as built', async () => {
    const onAuthor = vi.fn().mockResolvedValue({ override: row({}), warnings: [], overrides: [] });
    dialog(onAuthor);
    await choose('catalog.tag');
    await userEvent.click(screen.getByLabelText(/named targets/i));
    await userEvent.type(screen.getByLabelText('Target values'), 'DOE, RIV');
    await userEvent.click(screen.getByLabelText(/one schedule/i));
    await userEvent.type(screen.getByLabelText('Schedule id'), 'nightly');
    await userEvent.click(submit());
    expect(onAuthor).toHaveBeenCalledWith(
      'catalog.tag', { target: ['DOE', 'RIV'], schedule: 'nightly' }, false);
  });

  it.each(['mail.send', 'litellm.apply_config_change'])(
    'refuses %s until the acknowledgement is ticked', async (name) => {
      const onAuthor = vi.fn().mockResolvedValue({ override: row({}), warnings: [], overrides: [] });
      dialog(onAuthor);
      await choose(name);
      expect(screen.getByTestId('override-risk')).toBeInTheDocument();
      expect(submit()).toBeDisabled();
      fireEvent.click(screen.getByLabelText(/acknowledge the risk/i));
      expect(submit()).toBeEnabled();
      await userEvent.click(submit());
      expect(onAuthor).toHaveBeenCalledWith(name, {}, true);
    });

  it('never offers the auditor as holder', () => {
    dialog(undefined, { id: 'auditor', name: 'Auditor' } as AgentRow);
    expect(screen.getByText(/never given an override/i)).toBeInTheDocument();
    expect(submit()).toBeDisabled();
  });
});
