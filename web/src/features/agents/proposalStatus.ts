/**
 * How a proposal's status reads to the operator.
 *
 * The backend sends the raw lifecycle value and the record panel used to print
 * it verbatim, which is fine while every value says what it means. RETRY_PENDING
 * (outage equivalence slice 3) does not: it is an APPROVED proposal whose
 * execution hit a transient dependency failure and is queued for replay. Printed
 * raw it reads as "something is still waiting on me", which is exactly the thing
 * the doctrine forbids — the operator's decision stands; the world-change is
 * what queues.
 *
 * A display map, deliberately: no wire field was added, so there is nothing for
 * a backend wire-shape test to guard. Unknown statuses pass through unchanged —
 * a status this map has not met is still true, and hiding it would be worse.
 */
export const PROPOSAL_STATUS_LABELS: Record<string, string> = {
  RETRY_PENDING: 'approved — execution queued (provider outage)',
};

export function proposalStatusLabel(status: string): string {
  return PROPOSAL_STATUS_LABELS[status] ?? status;
}
