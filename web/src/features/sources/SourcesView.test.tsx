/**
 * SourcesView renders both source kinds off the real hook with `fetch`
 * mocked: the email row must carry its read-only marker (it is configured in
 * .env and the panel must not imply otherwise), the filesystem row its
 * catalog counts.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SourcesView } from './SourcesView';

const SOURCES = {
  sources: [
    {
      id: 'email-feed', kind: 'email', name: 'Email feed (Gmail via n8n façade)',
      read_only: true, enabled: true,
      config: { query: 'in:inbox newer_than:1d', poll_seconds: 60, backlog_query: 'in:inbox', backlog_window_days: 30 },
      cursor: null, last_polled_at: null, overview: null,
      feed_overview: { enrolled: 17, processed: 10, pending: 5, failed: 2 },
    },
    {
      id: 'docs-drive', kind: 'filesystem', name: 'Docs drive',
      read_only: false, enabled: true, config: { root: '/srv/docs' },
      cursor: { last_walk_at: '2026-08-30T08:00:00+00:00' },
      last_polled_at: '2026-08-30T08:00:00+00:00',
      overview: { documents: 3, rescinded: 1, versions: 5, locations: 4, missing: 2, unenrolled: 2 },
      feed_overview: null,
    },
  ],
};

describe('SourcesView', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (String(url).startsWith('/api/sources') ? SOURCES : { documents: [] }),
    })) as unknown as typeof fetch;
  });

  it('marks the email feed read-only and shows its ledger counts', async () => {
    render(<SourcesView />);
    await waitFor(() => expect(screen.getByText(/Email feed/)).toBeInTheDocument());
    expect(screen.getByText(/read-only — configured in \.env/)).toBeInTheDocument();
    expect(screen.getByText(/17 enrolled · 5 pending · 10 processed · 2 failed/)).toBeInTheDocument();
  });

  it('shows a filesystem source with its catalog counts', async () => {
    render(<SourcesView />);
    await waitFor(() => expect(screen.getByText('Docs drive')).toBeInTheDocument());
    expect(screen.getByText(/3 docs · 5 versions · 2 unenrolled · 2 missing · 1 rescinded/)).toBeInTheDocument();
  });
});
