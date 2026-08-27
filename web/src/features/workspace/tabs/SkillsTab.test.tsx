import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { SkillGrant } from '@/features/agents/types';
import { SkillsTab } from './SkillsTab';

const rpc = vi.fn();

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({ rpc, connectionState: 'connected' }),
}));

function grant(over: Partial<SkillGrant> = {}): SkillGrant {
  return {
    agent_id: 'alpha',
    skill_id: 'jira-hygiene',
    granted_by: 'operator',
    granted_at: '2026-07-30T10:00:00Z',
    revoked_at: null,
    revoked_reason: null,
    title: 'Jira hygiene',
    summary: 'How this team writes issues.',
    status: 'ACTIVE',
    has_guidance: true,
    reference_count: 2,
    ...over,
  };
}

describe('SkillsTab', () => {
  it('renders the agent granted skills from agents.detail', async () => {
    rpc.mockResolvedValue({
      skills: [
        grant(),
        grant({ skill_id: 'stale-one', title: 'Stale one', summary: 'No guidance yet.', has_guidance: false, reference_count: 1 }),
        grant({ skill_id: 'gone', title: 'Gone', summary: 'Old one.', revoked_at: '2026-07-31T09:00:00Z', revoked_reason: 'superseded' }),
      ],
    });

    render(<SkillsTab agentId="alpha" />);

    expect(await screen.findByText('Jira hygiene')).toBeInTheDocument();
    expect(rpc).toHaveBeenCalledWith('agents.detail', { agentId: 'alpha' });
    expect(screen.getByText('How this team writes issues.')).toBeInTheDocument();
    expect(screen.getByText(/2 refs/)).toBeInTheDocument();
    // has_guidance false is a "not delivered" grant, still listed
    expect(screen.getByText('Stale one')).toBeInTheDocument();
    expect(screen.getByText('not delivered')).toBeInTheDocument();
    // revoked grants are separated, not counted as granted
    expect(screen.getByText('2 granted')).toBeInTheDocument();
    expect(screen.getByText('Revoked (1)')).toBeInTheDocument();
  });

  it('shows the empty state pointing at the management screens', async () => {
    rpc.mockResolvedValue({ skills: [] });

    render(<SkillsTab agentId="alpha" />);

    expect(await screen.findByText('No skills granted')).toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });
});
