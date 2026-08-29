/**
 * Notification tiers — a PROJECTION of the existing event vocabulary.
 *
 * Doctrine decision 4: not everything the team does deserves to interrupt the
 * operator, and not everything deserves to be silent. Three tiers:
 *
 *   interrupt — something is BLOCKED ON THE OPERATOR, or something broke. Toasts.
 *   badge     — worth noticing, not worth stopping for. Counts and chips.
 *   digest    — the running log. Deferred to the EA slice.
 *
 * Deliberately NOT a new event kind: the runtime already emits a precise
 * vocabulary, and adding a `tier` field to the durable log would make an
 * append-only record carry a display decision that changes over time. The tier
 * is computed here, in the cockpit, where changing it costs a rebuild.
 *
 * UNKNOWN KINDS DEFAULT TO `digest`. A future chatty event — some per-token
 * or per-poll kind nobody thought about — must never become a toast by
 * accident. Adding an interrupt is a deliberate act: put it in the set below.
 */
export type NotificationTier = 'interrupt' | 'badge' | 'digest';

/**
 * Blocked-on-the operator, exhaustively. Every one of these means work has stopped
 * until the operator does something.
 */
const INTERRUPT_KINDS = new Set([
  // A proposal is parked at the approval gate.
  'proposal.created',
  // An agent asked, or judged a one-liner wouldn't do and opened a lane.
  'agent.asked',
  'agent.discussion_opened',
  // The D7 loop cannot proceed without an answer.
  'orchestration.question',
  'orchestration.parked',
  'orchestration.stalled',
  // A declared capability gap: the agent has stopped rather than guessed, and
  // only the operator can supply the remedy (material, a doc, a hire).
  'orchestration.capability_gap',
  'gap.declared',
  // The queue has stopped moving.
  'dispatch.stalled',
]);

/**
 * Worth a count or a chip, not a toast: progress the operator may want to
 * glance at, and the stall/review flags that already have their own visual
 * language in the panels.
 */
const BADGE_RE = /(stall|review|step)/;

/**
 * Both separators on purpose: the durable vocabulary has `proposal.failed`
 * AND `session.resume_failed`. Matching only on a dot would let a failed
 * resume — a dead run — pass silently into the digest.
 */
const FAILURE_RE = /[._](failed|error)$/;

/** `cc.proposal.created` and `proposal.created` are the same kind. */
export function normalizeKind(kind: string): string {
  return (kind || '').trim().replace(/^cc\./, '');
}

export function classifyEvent(kind: string): NotificationTier {
  const k = normalizeKind(kind);
  if (!k) return 'digest';
  if (INTERRUPT_KINDS.has(k)) return 'interrupt';
  // Anything that FAILED interrupts, whoever emitted it — a broken write or a
  // dead run is the one class of event where silence is the wrong default and
  // an exhaustive list would go stale the first time a new subsystem lands.
  if (FAILURE_RE.test(k)) return 'interrupt';
  if (BADGE_RE.test(k)) return 'badge';
  return 'digest';
}

/** An interrupt with no projection row of its own — toasted from the push. */
export function isErrorKind(kind: string): boolean {
  return FAILURE_RE.test(normalizeKind(kind));
}
