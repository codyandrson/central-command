/**
 * Goals (lines of effort) page types — the shape `GET /api/loe` returns.
 *
 * Hand-declared over an untyped RPC/REST payload, same reasoning as the
 * Activity screen's wire shapes: a field the backend never sends is
 * otherwise invisible to `tsc` and to every test.
 */

export interface LoeCheckIn {
  light: 'green' | 'yellow' | 'red' | string;
  confidence: number;
  summary?: string;
  created_at?: string;
  semantic_version?: number;
}

export interface LoeSemanticVersion {
  version: number;
  semantic: { questions: string[]; thresholds: string };
  /** null for the current version. */
  superseded_at: string | null;
}

export interface Loe {
  id: string;
  name: string;
  cadence: string;
  status: string;
  agent_id: string;
  semantic: { questions: string[]; thresholds: string };
  semantic_version: number;
  presentation: Record<string, unknown>;
  latest: LoeCheckIn | null;
  /** Up to `history` (default 12, cockpit requests 50), newest first. */
  history: LoeCheckIn[];
  /** Every semantic version, newest first — the current one carries superseded_at: null. */
  semantic_history: LoeSemanticVersion[];
}
