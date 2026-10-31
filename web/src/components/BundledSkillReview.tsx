import { useState } from 'react';
import {
  GUTTER, countMarkers, itemKey, mergedRows, sideBySideRows, summaryLine,
  type MergedRow, type Review, type ReviewItem, type Verdict,
} from '@/features/skills/skillReview';
import type { ResolveResult } from '@/features/skills/useSkillReview';

/** The row styles. The gutter mark and the card's legend carry the meaning;
 *  colour only reinforces it. */
const ROW_CLASS: Record<MergedRow['kind'], string> = {
  same: '',
  add: 'bg-green-500/10 text-green-600 dark:text-green-400',
  del: 'bg-red-500/10 text-red-600 dark:text-red-400 line-through decoration-red-500/50',
  mine: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  'mine-del': 'bg-blue-500/10 text-blue-600 dark:text-blue-400 line-through decoration-blue-500/50',
  conflict: '',
};

const STATUS_WORD: Record<string, string> = {
  modified: 'changed here and in the release',
  'retired-upstream': 'retired in the release',
  'retired-locally': 'retired here, changed in the release',
};

interface Choice { verdict: Verdict; label: string }

/** The verdict buttons a row offers (cockpit-review.md §Section, item 5-6). */
function choicesFor(item: ReviewItem): Choice[] {
  if (item.status === 'retired-upstream') {
    return [{ verdict: 'take_release', label: 'Retire (take release)' }, { verdict: 'keep_mine', label: 'Keep mine' }];
  }
  if (item.status === 'retired-locally') {
    return [
      { verdict: 'take_release', label: 'Reactivate with release text' },
      { verdict: 'keep_mine', label: 'Keep retired' },
    ];
  }
  return [{ verdict: 'take_release', label: 'Take release' }, { verdict: 'keep_mine', label: 'Keep mine' }];
}

function verdictLabel(item: ReviewItem): string {
  if (item.verdict === 'edit') return 'Edited';
  return choicesFor(item).find((c) => c.verdict === item.verdict)?.label ?? String(item.verdict);
}

function Row({ kind, text }: { kind: MergedRow['kind']; text: string }) {
  return (
    <div className={`flex ${ROW_CLASS[kind]}`}>
      <span aria-hidden className="w-5 shrink-0 select-none text-center opacity-70">{GUTTER[kind]}</span>
      <span className="whitespace-pre-wrap break-all min-w-0">{text || ' '}</span>
    </div>
  );
}

function Block({ label, lines }: { label: string; lines: string[] }) {
  return (
    <div>
      <div className="px-1 font-semibold opacity-80">{label}</div>
      {lines.length === 0 ? <div className="px-1 italic opacity-60">(empty)</div> : lines.map((l, i) => (
        <div key={i} className="flex">
          <span aria-hidden className="w-5 shrink-0 select-none text-center">{GUTTER.conflict}</span>
          <span className="whitespace-pre-wrap break-all min-w-0">{l || ' '}</span>
        </div>
      ))}
    </div>
  );
}

function MergedDiff({ item }: { item: ReviewItem }) {
  const rows = mergedRows(item);
  return (
    <div className="font-mono text-[0.7rem] leading-snug rounded border border-border">
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 px-2 py-1 border-b border-border text-muted-foreground font-sans text-[0.65rem]">
        <span>+ added by the release</span><span>- removed by the release</span>
        <span>~ differs here only</span><span>! conflict</span>
      </div>
      <div className="max-h-72 overflow-auto py-1">
        {rows.map((r, i) => r.kind === 'conflict' ? (
          <div key={i} role="group" aria-label="conflict region"
            className="my-1 border-y border-amber-500/60 bg-amber-500/15 text-amber-700 dark:text-amber-300 space-y-1 py-1">
            <Block label="mine (library)" lines={r.region.mine} />
            <Block label="base" lines={r.region.base} />
            <Block label="release" lines={r.region.release} />
          </div>
        ) : <Row key={i} kind={r.kind} text={r.text} />)}
      </div>
    </div>
  );
}

function SideBySide({ item }: { item: ReviewItem }) {
  const rows = sideBySideRows(item.ours, item.theirs);
  return (
    <div className="font-mono text-[0.7rem] leading-snug rounded border border-border">
      <div className="grid grid-cols-2 border-b border-border font-sans text-[0.65rem] font-semibold text-muted-foreground">
        <span className="px-2 py-1">mine (library)</span><span className="px-2 py-1 border-l border-border">release</span>
      </div>
      <div className="max-h-72 overflow-auto">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-2">
            <span className={`px-2 whitespace-pre-wrap break-all ${!r.same && r.left !== null ? ROW_CLASS.mine : ''}`}>
              {!r.same && r.left !== null && <span aria-hidden className="mr-1">~</span>}{r.left ?? ''}
            </span>
            <span className={`px-2 border-l border-border whitespace-pre-wrap break-all ${!r.same && r.right !== null ? ROW_CLASS.add : ''}`}>
              {!r.same && r.right !== null && <span aria-hidden className="mr-1">+</span>}{r.right ?? ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PlainText({ label, text }: { label: string; text: string | null }) {
  return (
    <div>
      <div className="text-[0.65rem] text-muted-foreground mb-0.5">{label}</div>
      <pre className="max-h-48 overflow-auto rounded border border-border px-2 py-1 font-mono text-[0.7rem] whitespace-pre-wrap break-all">{text ?? ''}</pre>
    </div>
  );
}

interface CardProps {
  item: ReviewItem;
  resolve: (item: ReviewItem, verdict: Verdict, content?: string) => Promise<ResolveResult>;
}

function ReviewCard({ item, resolve }: CardProps) {
  const [reopened, setReopened] = useState(false);
  const [sideBySide, setSideBySide] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const key = itemKey(item);

  if (item.verdict !== null && !reopened) {
    return (
      <div className="flex items-center justify-between gap-2 rounded border border-border px-3 py-1.5 text-xs">
        <span className="min-w-0 truncate">
          <span className="font-mono">{key}</span> — {verdictLabel(item)}
        </span>
        <button type="button" onClick={() => setReopened(true)} className="text-primary hover:underline shrink-0">
          Change
        </button>
      </div>
    );
  }

  const post = async (verdict: Verdict, content?: string) => {
    setPosting(true);
    setError(null);
    const out = await resolve(item, verdict, content);
    setPosting(false);
    if (out.ok) { setEditing(null); setReopened(false); return; }
    setError(out.status === 409
      ? 'This doc changed underneath you — the review was computed again; look at it once more.'
      : out.message);
  };

  const markers = editing === null ? 0 : countMarkers(editing);
  const hasMerge = item.status === 'modified' && item.merged !== null;
  return (
    <section aria-label={key} className="rounded border border-border px-3 py-2 space-y-2 text-xs">
      <header className="space-y-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{item.skill_id}</span>
          <span className="font-mono">{item.doc_key}</span>
          <span className="text-muted-foreground">{STATUS_WORD[item.status] ?? item.status}</span>
        </div>
        {item.base_origin === 'installed-tree' && (
          <div className="text-muted-foreground">
            compared against the installed copy (no provenance recorded before this release)
          </div>
        )}
        {item.conflicts > 0 && (
          <div className="text-amber-600 dark:text-amber-400">
            {item.conflicts} conflict {item.conflicts === 1 ? 'region' : 'regions'}
          </div>
        )}
      </header>

      {editing !== null ? (
        <div className="space-y-1">
          <textarea
            aria-label={`Edited text for ${key}`}
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
            rows={14}
            className="w-full rounded border border-border bg-background px-2 py-1 font-mono text-[0.7rem]"
          />
          {markers > 0 && (
            <p className="text-amber-600 dark:text-amber-400">
              {markers} conflict {markers === 1 ? 'marker remains' : 'markers remain'}
            </p>
          )}
        </div>
      ) : hasMerge ? (
        <>
          <label className="flex items-center gap-1 text-muted-foreground">
            <input type="checkbox" checked={sideBySide} onChange={(e) => setSideBySide(e.target.checked)} />
            Side by side (mine | release)
          </label>
          {sideBySide ? <SideBySide item={item} /> : <MergedDiff item={item} />}
        </>
      ) : item.status === 'retired-locally' ? (
        <PlainText label="Release text" text={item.theirs} />
      ) : (
        <PlainText label="Library text (the release no longer ships it)" text={item.ours} />
      )}

      {error && <p role="alert" className="text-red-500">{error}</p>}

      <div className="flex flex-wrap gap-2">
        {editing !== null ? (
          <>
            <button type="button" disabled={markers > 0 || posting} onClick={() => post('edit', editing)}
              className="rounded bg-primary text-primary-foreground px-2.5 py-1 font-semibold disabled:opacity-50">
              Save
            </button>
            <button type="button" disabled={posting} onClick={() => { setEditing(null); setError(null); }}
              className="rounded border border-border px-2.5 py-1">
              Cancel
            </button>
          </>
        ) : (
          <>
            {choicesFor(item).map((c) => (
              <button key={c.verdict} type="button" disabled={posting} onClick={() => post(c.verdict)}
                className="rounded border border-border px-2.5 py-1 hover:bg-muted disabled:opacity-50">
                {c.label}
              </button>
            ))}
            {item.status === 'modified' && (
              <button type="button" disabled={posting}
                onClick={() => setEditing(item.resolved_content ?? item.merged ?? item.ours ?? '')}
                className="rounded border border-border px-2.5 py-1 hover:bg-muted disabled:opacity-50">
                Edit merged
              </button>
            )}
            {reopened && (
              <button type="button" onClick={() => setReopened(false)} className="px-2 py-1 text-muted-foreground hover:underline">
                Close
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/** The dialog's "Bundled skill docs" section: what refreshes silently, and one
 *  card per doc that needs the operator's decision. */
export function BundledSkillReview({ review, resolve }: {
  review: Review;
  resolve: CardProps['resolve'];
}) {
  const summary = summaryLine(review);
  const decide = review.items.filter((i) => i.needs_verdict);
  const retiredSkills = review.items.filter((i) => i.status === 'skill-retired');
  if (!summary && decide.length === 0 && retiredSkills.length === 0) return null;
  return (
    <section aria-label="Bundled skill docs" className="space-y-2">
      <h3 className="text-sm font-semibold">Bundled skill docs</h3>
      {summary && <p className="text-xs text-muted-foreground">{summary}</p>}
      {retiredSkills.map((i) => (
        <p key={i.skill_id} className="text-xs text-muted-foreground">
          {i.skill_id}: retired here — not refreshed
        </p>
      ))}
      <div className="space-y-2 max-h-[55vh] overflow-y-auto pr-1">
        {decide.map((i) => <ReviewCard key={itemKey(i)} item={i} resolve={resolve} />)}
      </div>
    </section>
  );
}

