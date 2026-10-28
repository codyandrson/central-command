/** Small shared pieces of the Graph panel's forms (kept out of the component
 *  modules so React fast refresh sees components only). */
import type { GraphGroupScope } from './useGraph';

export const FIELD = 'w-full rounded-lg border border-input/80 bg-background/72 px-2 py-1.5 text-[0.733rem] text-foreground';

/** "Public" / "Private · <agent_id>" — the operator-facing label for a raw
 *  `group_id`, everywhere one is shown. The underlying id stays available
 *  via `title` so it's never fully hidden, just not the primary label. */
export function scopeLabel(g: GraphGroupScope): string {
  return g.scope === 'private' ? `Private · ${g.agent_id}` : `Public (${g.id})`;
}
