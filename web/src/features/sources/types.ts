/**
 * Sources & catalog — the document-inventory surface (2026-08-23 sources
 * spec, Decision 2), whose first customer is the existing email feed.
 *
 * Shapes mirror the backend rows verbatim (central_command/api/routes.py
 * list_sources + repo.list_catalog_documents). snake_case is kept on purpose:
 * these objects cross the REST boundary untranslated, so the field names in
 * the UI grep-match the backend.
 */

/** Catalog counts — filesystem sources only; null on the email row. */
export interface CatalogOverview {
  documents: number;
  rescinded: number;
  versions: number;
  locations: number;
  missing: number;
  /** Versions still owed a work item — the catalog's enrollment backlog. */
  unenrolled: number;
}

/** Ledger census — the email feed only; null on filesystem rows. */
export interface FeedOverview {
  enrolled: number;
  processed: number;
  pending: number;
  failed: number;
}

/**
 * One row per source. The email feed is SYNTHESIZED at read time from
 * settings + the ledger (`read_only: true`) and carries the same key set as a
 * stored filesystem row, so both render through one type.
 */
export interface Source {
  id: string;
  kind: 'filesystem' | 'email';
  name: string;
  read_only: boolean;
  enabled: boolean;
  config: Record<string, unknown>;
  cursor: Record<string, unknown> | null;
  last_polled_at: string | null;
  overview: CatalogOverview | null;
  feed_overview: FeedOverview | null;
}

export interface CatalogDocument {
  id: string;
  source_id: string;
  lineage_key: string;
  title: string | null;
  status: string;
  rescinded_reason: string | null;
  rescinded_at: string | null;
  latest_version: number | null;
  location_count: number;
  missing_count: number;
}

/** What POST /api/sources/{id}/walk returns, rendered inline after a walk. */
export type WalkSummary = Record<string, number | string>;
