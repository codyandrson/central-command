/** A Person's single-person values in the Graph panel: the read-only lines on
 *  the entity card, and the three fields of the node edit form. A fact is an
 *  edge between two entities, so a birth date, a phone number or another name
 *  for the person lives on the entity itself (graph_ontology.Person). */
import { FIELD, type PersonForm } from './graphUi';

export function PersonCard({ node }: {
  node: { aliases?: string[] | null; birth_date?: string | null; phone?: string | null };
}) {
  const aliases = node.aliases || [];
  if (!aliases.length && !node.birth_date && !node.phone) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[0.7rem]" aria-label="Person details">
      {aliases.length > 0 && (
        <>
          <dt className="text-muted-foreground">Also called</dt>
          <dd className="text-foreground">{aliases.join(', ')}</dd>
        </>
      )}
      {node.birth_date && (
        <>
          <dt className="text-muted-foreground">Born</dt>
          <dd className="text-foreground">{node.birth_date}</dd>
        </>
      )}
      {node.phone && (
        <>
          <dt className="text-muted-foreground">Phone</dt>
          <dd className="text-foreground">{node.phone}</dd>
        </>
      )}
    </dl>
  );
}

export function PersonFields({ form, onChange }: {
  form: PersonForm;
  onChange: (form: PersonForm) => void;
}) {
  return (
    <div className="space-y-1.5">
      <input
        className={FIELD}
        value={form.aliases}
        onChange={(e) => onChange({ ...form, aliases: e.target.value })}
        placeholder="Other names, comma-separated (never a role word like 'Mom')"
        aria-label="Person aliases"
      />
      <label className="flex items-center gap-2 text-[0.7rem] text-muted-foreground">
        <span className="shrink-0">born</span>
        <input
          type="date"
          className={FIELD}
          value={form.birthDate}
          onChange={(e) => onChange({ ...form, birthDate: e.target.value })}
          aria-label="Person birth date"
        />
      </label>
      <input
        className={FIELD}
        value={form.phone}
        maxLength={40}
        onChange={(e) => onChange({ ...form, phone: e.target.value })}
        placeholder="Phone, as written"
        aria-label="Person phone"
      />
    </div>
  );
}
