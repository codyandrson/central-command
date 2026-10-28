/**
 * The wire shapes of `outages.list` / `outages.retry` (api/outages.py,
 * epic-outage-doctrine story 9). Declared by hand over an untyped RPC, so a
 * field the backend stops sending is invisible to `tsc` — the backend's
 * tests/test_outage_wire.py pins these.
 */

export type DependencyState = 'up' | 'down' | 'n/a';

/** What waits on a dependency, per kind of unit. */
export interface OutageWaiting {
  sessions: number;
  proposals: number;
  tasks: number;
  work_items: number;
  ingest_jobs: number;
}

/** A unit failing against a healthy dependency (the loud rule's report). */
export interface FailingUnit {
  kind: string;
  id: string;
  attempts: number;
  last_error: string | null;
}

export interface OutageDependency {
  name: string;
  state: DependencyState;
  /** When the dependency last CHANGED state — NOT when it was last probed.
   *  Null for a dependency never seen down. */
  checked_at: string | null;
  down_since: string | null;
  waiting: OutageWaiting;
  /** Approved proposals a restart interrupted: only the operator moves them. */
  interrupted: number;
  failing: FailingUnit[];
  /** Every waiting unit plus the interrupted ones. */
  total: number;
}

export interface OutagesPayload {
  dependencies: OutageDependency[];
}

export interface OutageRetryResult {
  probe: 'up' | 'down';
  driven: OutageWaiting;
}
