/** "2h 5m" / "14m" / "under a minute" — how long a dependency has been down. */
export function downFor(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'a while';
  const ms = now - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 60_000) return 'under a minute';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
