/** What the cockpit says about a session's charter, from the server's row. */
export interface CharterStamp {
  /** "charter v3", or "charter unknown" for a session older than the stamp. */
  label: string;
  /** "a newer charter (v5) exists", when the agent has moved on. */
  newer: string | null;
  /** One sentence for where Re-run is offered, or null when nothing would change. */
  rerunHint: string | null;
}

export function charterStamp(
  row: { charterVersion?: number | null; charterCurrent?: number | null } | null | undefined,
): CharterStamp | null {
  if (!row) return null;
  const { charterVersion: version, charterCurrent: current } = row;
  const known = version !== null && version !== undefined;
  const hasCurrent = current !== null && current !== undefined;
  const stale = known && hasCurrent && current > version;
  return {
    label: known ? `charter v${version}` : 'charter unknown',
    newer: stale ? `a newer charter (v${current}) exists` : null,
    rerunHint: stale
      ? `It ran under charter v${version}; a re-run uses v${current}.`
      : !known && hasCurrent
        ? `Which charter it ran under is unknown; a re-run uses v${current}.`
        : null,
  };
}
