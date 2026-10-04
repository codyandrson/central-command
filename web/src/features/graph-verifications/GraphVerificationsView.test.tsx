import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphVerificationsView } from './GraphVerificationsView';
import { DETAIL_CLIP, clip, mechanicalMessages } from './mechanical';
import type { VerificationMechanical, VerificationRow } from './useGraphVerifications';

const hook = vi.hoisted(() => ({ rows: [] as unknown[] }));

vi.mock('./useGraphVerifications', () => ({
  useGraphVerifications: () => ({
    enabled: true,
    mode: 'shadow',
    awaiting: hook.rows,
    recent_closed: [],
    loading: false,
    error: '',
    refresh: vi.fn(),
    confirm: vi.fn(),
    problem: vi.fn(),
  }),
}));

function row(mechanical: VerificationMechanical): VerificationRow {
  return {
    id: 'gv_1',
    proposal_id: 'p_1',
    episode_name: 'Doe prefers morning calls',
    group_id: 'central_command',
    scope: 'shared',
    status: 'AWAITING_OPERATOR',
    episode_uuid: null,
    mechanical,
    delta: null,
    verdict: null,
    verdict_rationale: null,
    has_invalidations: false,
    problem_note: null,
    remediation_of: null,
    created_at: '2026-10-04T10:00:00Z',
    closed_at: null,
    closed_by: null,
    approved_text: 'John Doe prefers morning calls.',
  };
}

describe('mechanicalMessages', () => {
  it('words an ingest failure as FAILED and not retried, with the error as detail', () => {
    const flags = mechanicalMessages({ ingest_failed: true, error: 'JSONDecodeError: Unterminated string' });
    expect(flags).toEqual([
      { text: 'extraction FAILED and was not retried', detail: 'JSONDecodeError: Unterminated string' },
    ]);
  });

  it('still says so when no error text was recorded', () => {
    const [flag] = mechanicalMessages({ ingest_failed: true, error: null });
    expect(flag.text).toMatch(/FAILED and was not retried/);
    expect(flag.detail).toBe('no error text was recorded');
  });

  it('keeps the historical wording for rows the old re-submit produced', () => {
    expect(mechanicalMessages({ missing: true, resubmitted: true }).map((f) => f.text)).toEqual([
      'episode never landed, even after a re-submission',
    ]);
    expect(mechanicalMessages({ missing: true }).map((f) => f.text)).toEqual(['episode never landed']);
  });

  it('keeps the existing findings unchanged', () => {
    const texts = mechanicalMessages({
      missing: false, empty_delta: true, unembedded: ['A', 'B'], no_approved_text: true,
    }).map((f) => f.text);
    expect(texts).toEqual([
      'extraction produced nothing',
      '2 entities unembedded (invisible to semantic recall)',
      'no approved text on record',
    ]);
  });

  it('clips long text with an ellipsis and leaves short text alone', () => {
    expect(clip('short')).toBe('short');
    const long = 'x'.repeat(DETAIL_CLIP + 50);
    expect(clip(long)).toHaveLength(DETAIL_CLIP + 1);
    expect(clip(long).endsWith('…')).toBe(true);
  });
});

describe('GraphVerificationsView ingest failure', () => {
  beforeEach(() => { hook.rows = []; });

  it('shows the failure label and the error on the awaiting card', () => {
    hook.rows = [row({ ingest_failed: true, error: 'ValidationError: bad group' })];
    render(<GraphVerificationsView />);
    expect(screen.getByText(/extraction FAILED and was not retried/)).toBeInTheDocument();
    expect(screen.getByTestId('mechanical-detail')).toHaveTextContent('ValidationError: bad group');
  });

  it('clips a long error on screen but keeps all of it in the title', () => {
    const error = `boom ${'y'.repeat(2000)}`;
    hook.rows = [row({ ingest_failed: true, error })];
    render(<GraphVerificationsView />);
    const detail = screen.getByTestId('mechanical-detail');
    expect(detail.textContent!.length).toBeLessThan(error.length);
    expect(detail.textContent!.endsWith('…')).toBe(true);
    expect(detail.parentElement).toHaveAttribute('title', error);
  });

  it('does not offer "Nothing expected" for a failed extraction', () => {
    hook.rows = [row({ ingest_failed: true, error: 'x' })];
    render(<GraphVerificationsView />);
    expect(screen.queryByText('Nothing expected')).not.toBeInTheDocument();
    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });
});
