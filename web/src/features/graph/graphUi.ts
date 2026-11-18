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

/** A Person's single-person values as the edit form holds them: aliases as one
 *  comma-separated line. */
export interface PersonForm { aliases: string; birthDate: string; phone: string }

export function personForm(node: { aliases?: string[] | null; birth_date?: string | null; phone?: string | null }): PersonForm {
  return {
    aliases: (node.aliases || []).join(', '),
    birthDate: node.birth_date || '',
    phone: node.phone || '',
  };
}

/** The comma-separated line back to a list: trimmed, empties dropped. The
 *  server does the rest (case-insensitive de-duplication, the node's own name,
 *  the caps). */
export function parseAliases(text: string): string[] {
  return text.split(',').map((a) => a.trim()).filter(Boolean);
}

/** Only what CHANGED goes in the PATCH — an omitted field is left alone by the
 *  writer, and an empty list / string clears one. Leaving `aliases` out of a
 *  rename lets the server keep the old name as an alias. */
export function personPatch(
  node: { aliases?: string[] | null; birth_date?: string | null; phone?: string | null },
  form: PersonForm,
): { aliases?: string[]; birth_date?: string; phone?: string } {
  const patch: { aliases?: string[]; birth_date?: string; phone?: string } = {};
  const aliases = parseAliases(form.aliases);
  if (aliases.join('\n') !== (node.aliases || []).join('\n')) patch.aliases = aliases;
  if (form.birthDate.trim() !== (node.birth_date || '')) patch.birth_date = form.birthDate.trim();
  if (form.phone.trim() !== (node.phone || '')) patch.phone = form.phone.trim();
  return patch;
}

export function isPerson(node: { labels: string[] }): boolean {
  return node.labels.includes('Person');
}
