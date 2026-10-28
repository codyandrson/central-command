import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AddEntity } from './AddEntity';

const REFUSAL = 'The embedder is down, so nothing was written. Your entry is kept — save it again once the embedder is back.';

describe('AddEntity', () => {
  it('shows an embedder refusal and keeps the unsaved entry filled', async () => {
    const onCreate = vi.fn().mockRejectedValueOnce(new Error(REFUSAL)).mockResolvedValueOnce(undefined);
    render(
      <AddEntity
        entityTypes={['Person']}
        groups={[{ id: 'central_command', scope: 'shared' } as never]}
        onCreate={onCreate}
        onClose={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New entity name'), 'Jane Doe');
    await user.type(screen.getByLabelText('New entity summary'), 'Works at Example Co');
    await user.click(screen.getByRole('button', { name: 'Person' }));
    await user.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The embedder is down');
    expect(screen.getByLabelText('New entity name')).toHaveValue('Jane Doe');
    expect(screen.getByLabelText('New entity summary')).toHaveValue('Works at Example Co');

    // One click once the embedder is back: the same entry, and then it clears.
    await user.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(2));
    expect(onCreate.mock.calls[1][0]).toMatchObject({
      name: 'Jane Doe', summary: 'Works at Example Co', labels: ['Person'], group_id: 'central_command',
    });
    await waitFor(() => expect(screen.getByLabelText('New entity name')).toHaveValue(''));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
