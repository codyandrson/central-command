/**
 * How long something has been sitting in the Decisions Inbox — the badge tier
 * applied to the queue itself.
 *
 * The inbox is oldest-first by contract, so the oldest item is always at the
 * top; what the operator could not see is HOW old, because `RelativeTime`
 * renders "3d ago" in the same muted grey as "3m ago". A three-day-old
 * approval is a blocked agent, and it should not look like a fresh one.
 *
 * Thresholds are CLIENT constants, not server config: they are a display
 * judgement about attention, and nothing in the spine branches on them. If an
 * agent's behaviour ever depends on inbox age, that belongs in the runtime
 * with its own record, not here.
 */
export const AGING_HOURS = 24;
export const STALE_HOURS = 72;

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export type AgingLevel = 'fresh' | 'aging' | 'stale';

function elapsed(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return null;
  return Math.max(0, now - at);
}

export function agingLevel(
  iso: string | null | undefined, now: number = Date.now(),
): AgingLevel {
  const ms = elapsed(iso, now);
  if (ms === null) return 'fresh';
  if (ms >= STALE_HOURS * HOUR_MS) return 'stale';
  if (ms >= AGING_HOURS * HOUR_MS) return 'aging';
  return 'fresh';
}

/**
 * "3d" / "25h" / "<1h" — the same vocabulary as the session stall chip, and
 * the same rule: REPORT the elapsed time, never round it UP. The stall chip
 * shipped with a `Math.max(1, …)` that made every chip claim "1d"; a chip that
 * cannot tell an hour from a day is worse than no chip, because it is
 * confidently wrong.
 */
export function ageLabel(elapsedMs: number): string {
  if (elapsedMs >= DAY_MS) return `${Math.floor(elapsedMs / DAY_MS)}d`;
  if (elapsedMs >= HOUR_MS) return `${Math.floor(elapsedMs / HOUR_MS)}h`;
  return '<1h';
}

export function ageOf(
  iso: string | null | undefined, now: number = Date.now(),
): string | null {
  const ms = elapsed(iso, now);
  return ms === null ? null : ageLabel(ms);
}

export interface OldestAge {
  level: AgingLevel;
  label: string;
}

/**
 * The oldest item in a section, when it is old enough to be worth naming.
 * Returns null for a healthy section — a section label that always carries an
 * age reads as decoration and stops being noticed.
 */
export function oldestAge(
  isos: Array<string | null | undefined>, now: number = Date.now(),
): OldestAge | null {
  let worst: number | null = null;
  for (const iso of isos) {
    const ms = elapsed(iso, now);
    if (ms === null) continue;
    if (worst === null || ms > worst) worst = ms;
  }
  if (worst === null || worst < AGING_HOURS * HOUR_MS) return null;
  return {
    level: worst >= STALE_HOURS * HOUR_MS ? 'stale' : 'aging',
    label: ageLabel(worst),
  };
}
