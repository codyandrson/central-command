import type { ChatComposerState } from '@/types';

export interface RerunLink {
  /** What the row or header says, e.g. "re-run as sess_ab12". */
  label: string;
  /** The session key to jump to. */
  sessionKey: string;
}

/**
 * The re-run links of one session row, newest direction first. A session in the
 * middle of a chain has both. Built from the ids the server's row carries, never
 * from a client-side flip.
 */
export function rerunLinks(
  row: { agentId?: string | null; rerunOf?: string | null; rerunBy?: string | null } | null | undefined,
): RerunLink[] {
  if (!row?.agentId) return [];
  const links: RerunLink[] = [];
  if (row.rerunBy) {
    links.push({ label: `re-run as ${row.rerunBy}`, sessionKey: `agent:${row.agentId}:${row.rerunBy}` });
  }
  if (row.rerunOf) {
    links.push({ label: `re-runs ${row.rerunOf}`, sessionKey: `agent:${row.agentId}:${row.rerunOf}` });
  }
  return links;
}

export function composerRerunLinks(composer: ChatComposerState | null | undefined): RerunLink[] {
  return rerunLinks(composer?.session);
}
