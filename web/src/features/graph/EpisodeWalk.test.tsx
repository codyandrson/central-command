import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EpisodeWalk } from './EpisodeWalk';
import type { EpisodeDeletePreview, EpisodeIndexRow } from './useGraph';

const row = (uuid: string, name: string): EpisodeIndexRow => ({
  uuid, name, source_description: 'operator', group_id: 'central_command',
  created_at: '2026-10-01T09:00:00Z', valid_at: null, entity_count: 1,
});
const ROWS = [row('ep-1', 'newest'), row('ep-2', 'middle'), row('ep-3', 'oldest')];

const preview = (uuid: string): EpisodeDeletePreview => ({
  episode: { uuid, name: uuid, group_id: 'central_command', content: 'x', source_description: null,
    created_at: null, valid_at: null },
  facts: [], entities: [], collateral_facts: [], provenance_facts: [],
  episodes_losing_fact_refs: [], digest: `d-${uuid}`,
});

describe('EpisodeWalk deletion', () => {
  it('after a deletion lands, moves to the neighbouring episode and reloads the canvas', async () => {
    const user = userEvent.setup({ delay: null });
    const episodeSubgraph = vi.fn(async (uuid: string) => ({
      episode: { ...ROWS.find((r) => r.uuid === uuid)!, content: `${uuid} text` }, nodes: [], edges: [],
    }));
    const onLoad = vi.fn();
    const onDeleted = vi.fn();
    const deleteEpisode = vi.fn().mockResolvedValue({ status: 'done', job_id: 1, result: {} });
    render(
      <EpisodeWalk
        groupId=""
        episodeIndex={vi.fn().mockResolvedValue(ROWS)}
        episodeSubgraph={episodeSubgraph}
        onLoad={onLoad}
        onClose={vi.fn()}
        loadDeletePreview={vi.fn(async (uuid: string) => preview(uuid))}
        deleteEpisode={deleteEpisode}
        onDeleted={onDeleted}
      />,
    );
    await user.click(await screen.findByRole('button', { name: 'Next episode' }));
    expect(await screen.findByText('ep-2 text')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Delete episode/ }));
    await user.click(await screen.findByLabelText('I understand this deletion is irreversible'));
    const dialogButtons = screen.getAllByRole('button', { name: 'Delete episode' });
    await user.click(dialogButtons[dialogButtons.length - 1]);
    await waitFor(() => expect(deleteEpisode).toHaveBeenCalledWith('ep-2', 'd-ep-2'));
    // The middle episode left; the walk stands on its older neighbour now.
    expect(await screen.findByText('ep-3 text')).toBeInTheDocument();
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    expect(episodeSubgraph).toHaveBeenLastCalledWith('ep-3');
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });
});
