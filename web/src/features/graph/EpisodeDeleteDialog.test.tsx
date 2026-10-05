import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EpisodeDeleteDialog } from './EpisodeDeleteDialog';
import type { EpisodeDeletePreview } from './useGraph';

const fact = (uuid: string, text: string) => ({
  uuid, name: 'REL', fact: text, source: 'a', source_name: 'Jane Doe', target: 'b', target_name: 'Example Co',
});

const PREVIEW: EpisodeDeletePreview = {
  episode: {
    uuid: 'ep-1', name: 'Jane joins Example Co', group_id: 'central_command',
    content: 'Jane Doe joined Example Co on 2025-03-01.', source_description: 'operator',
    created_at: '2026-10-01T09:00:00Z', valid_at: '2026-10-01T09:00:00Z',
  },
  facts: [fact('f1', 'Jane Doe works at Example Co')],
  entities: [{ uuid: 'n1', name: 'Jane Doe', labels: ['Entity', 'Person'], group_id: 'central_command' }],
  collateral_facts: [fact('f9', 'Sam Rivers manages Jane Doe')],
  provenance_facts: [fact('f2', 'Example Co is in Springfield')],
  episodes_losing_fact_refs: ['ep-2'],
  digest: 'digest-1',
};

function renderDialog(over: Partial<Parameters<typeof EpisodeDeleteDialog>[0]> = {}) {
  const props = {
    episodeUuid: 'ep-1',
    open: true,
    onOpenChange: vi.fn(),
    loadPreview: vi.fn().mockResolvedValue(PREVIEW),
    deleteEpisode: vi.fn().mockResolvedValue({ status: 'done', job_id: 4, result: {} }),
    onDeleted: vi.fn(),
    ...over,
  };
  render(<EpisodeDeleteDialog {...props} />);
  return props;
}

describe('EpisodeDeleteDialog', () => {
  it('renders the preview: counts, the facts and entities that go, collateral flagged, provenance that survives', async () => {
    renderDialog();
    expect(await screen.findByTestId('delete-counts')).toHaveTextContent(
      'Deletes the episode, 1 fact, 1 entity and 1 collateral fact. 1 surviving fact stop citing it.',
    );
    expect(screen.getByText(/Jane Doe works at Example Co/)).toBeInTheDocument();
    expect(screen.getByText(/Collateral/)).toBeInTheDocument();
    expect(screen.getByText(/Not created by this episode/)).toBeInTheDocument();
    expect(screen.getByText(/Sam Rivers manages Jane Doe/)).toBeInTheDocument();
    expect(screen.getByText(/Survive, but stop citing/)).toBeInTheDocument();
    expect(screen.getByText(/Example Co is in Springfield/)).toBeInTheDocument();
  });

  it('requires the explicit acknowledgement, then deletes with the preview digest', async () => {
    const user = userEvent.setup({ delay: null });
    const props = renderDialog();
    await screen.findByTestId('delete-counts');
    const del = screen.getByRole('button', { name: 'Delete episode' });
    expect(del).toBeDisabled();
    await user.click(screen.getByLabelText('I understand this deletion is irreversible'));
    expect(del).toBeEnabled();
    await user.click(del);
    await waitFor(() => expect(props.deleteEpisode).toHaveBeenCalledWith('ep-1', 'digest-1'));
    expect(props.onDeleted).toHaveBeenCalledWith(PREVIEW);
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('says "queued" when the deletion is waiting its turn, and does not report it done', async () => {
    const user = userEvent.setup({ delay: null });
    const props = renderDialog({
      deleteEpisode: vi.fn().mockResolvedValue({ status: 'queued', job_id: 12 }),
    });
    await screen.findByTestId('delete-counts');
    await user.click(screen.getByLabelText('I understand this deletion is irreversible'));
    await user.click(screen.getByRole('button', { name: 'Delete episode' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Queued (job 12)');
    expect(props.onDeleted).not.toHaveBeenCalled();
  });

  it('re-reads the preview when the graph changed since it was shown', async () => {
    const user = userEvent.setup({ delay: null });
    const props = renderDialog({
      deleteEpisode: vi.fn().mockRejectedValue(new Error('the graph changed since this preview was read — nothing was deleted')),
    });
    await screen.findByTestId('delete-counts');
    await user.click(screen.getByLabelText('I understand this deletion is irreversible'));
    await user.click(screen.getByRole('button', { name: 'Delete episode' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed since');
    await waitFor(() => expect(props.loadPreview).toHaveBeenCalledTimes(2));
    expect(props.onDeleted).not.toHaveBeenCalled();
  });
});
