/**
 * The seen-set: which interrupt items this browser has already told the operator about.
 *
 * Lives in localStorage because the alternative — deriving "new" from the push
 * stream — cannot work: `_forward_events` has NO REPLAY across reconnects, so
 * a reload or a dropped socket would either re-toast everything already on
 * screen or silently miss what arrived during the gap. Diffing a refetched
 * projection against a persisted set survives both.
 *
 * Keyed by a stable id per interrupt item (`proposal:<id>`, `item:<id>`,
 * `session:<key>`), never by event id — the same parked proposal must not
 * toast twice because two events touched it.
 */
export const SEEN_KEY = 'cc:notify:seen';

/** Keep a tail so an item that briefly disappears and returns stays silent. */
export const SEEN_TAIL_MS = 7 * 24 * 60 * 60 * 1000;

export type SeenSet = Record<string, number>;

export function loadSeen(store: Storage = localStorage): SeenSet {
  try {
    const raw = store.getItem(SEEN_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: SeenSet = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    }
    return out;
  } catch {
    // A corrupt or unavailable store must not break the cockpit. The cost of
    // returning {} is one round of re-toasting, not a broken screen.
    return {};
  }
}

/**
 * Persist, pruned to what still exists plus a 7-day tail. Without the prune
 * this grows forever; without the tail, an item that vanishes for one poll
 * (a transient RPC failure) would toast again on its return.
 */
export function saveSeen(
  seen: SeenSet,
  presentIds: Iterable<string>,
  now: number = Date.now(),
  store: Storage = localStorage,
): SeenSet {
  const present = new Set(presentIds);
  const next: SeenSet = {};
  for (const [id, at] of Object.entries(seen)) {
    if (present.has(id) || now - at < SEEN_TAIL_MS) next[id] = at;
  }
  try {
    store.setItem(SEEN_KEY, JSON.stringify(next));
  } catch { /* quota or private mode — in-memory behaviour is still correct */ }
  return next;
}

/**
 * How loud the toasts are, read from the SAME store the Appearance settings
 * write. Read at push time rather than through SettingsContext so the
 * notification layer keeps working without a provider around it — a
 * notification is an enhancement, and the durable inbox is the real record.
 *
 * The seen-set is updated regardless of mode: turning toasts back on must not
 * replay a morning's worth of already-known items.
 */
export const TOAST_MODE_KEY = 'cc:notify:mode';

export function loadToastMode(store: Storage = localStorage): 'all' | 'errors' | 'off' {
  try {
    const raw = store.getItem(TOAST_MODE_KEY);
    return raw === 'errors' || raw === 'off' ? raw : 'all';
  } catch {
    return 'all';
  }
}
