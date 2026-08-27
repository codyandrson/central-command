/**
 * Maps a check-in's `light` to a border/dot color and a line color for the
 * sparkline. Deliberately three known lights + a neutral fallback — an
 * unrecognized light renders neutrally rather than looking broken, and
 * `latest === null` (no check-in yet) is its own neutral state, never styled
 * as a miss. No red "FAILED" styling, no streaks, no guilt copy.
 */
export type Light = 'green' | 'yellow' | 'red' | string;

const TONE: Record<string, { border: string; dot: string; line: string; badge: string }> = {
  green: { border: 'border-l-green', dot: 'bg-green', line: '#22c55e', badge: 'success' },
  yellow: { border: 'border-l-orange', dot: 'bg-orange', line: '#f97316', badge: 'warning' },
  red: { border: 'border-l-red', dot: 'bg-red', line: '#ef4444', badge: 'danger' },
};

const NEUTRAL = { border: 'border-l-border', dot: 'bg-muted-foreground/40', line: '#888888', badge: '' };

export function lightTone(light: Light | null | undefined) {
  if (!light) return NEUTRAL;
  return TONE[light] ?? NEUTRAL;
}
