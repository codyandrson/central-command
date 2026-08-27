/**
 * GoalPreviewCard — draft-tile rendering of a [goal-preview:{...}] chat
 * marker (see extractGoalPreviews.ts) and of a pending `loe.create`
 * proposal in the Decisions Inbox. Adapted from GoalTile's markup in
 * LoeView.tsx, but it is not a real Loe: no check-in data, no click target,
 * visibly marked as a preview.
 */
import { Target } from 'lucide-react';
import type { GoalPreviewData } from './extractGoalPreviews';

export function GoalPreviewCard({ preview }: { preview: GoalPreviewData }) {
  return (
    <div className="rounded-lg border border-l-4 border-border/50 border-l-primary/50 bg-background/40 p-3">
      <div className="flex items-center gap-2">
        <Target size={12} className="shrink-0 text-primary" aria-hidden="true" />
        <span className="min-w-0 truncate text-[0.8rem] font-semibold text-foreground">{preview.name}</span>
        {preview.cadence && <span className="cockpit-badge ml-auto shrink-0">{preview.cadence}</span>}
        <span className="cockpit-badge shrink-0" data-tone="warning">preview</span>
      </div>

      {preview.questions && preview.questions.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[0.733rem] text-muted-foreground">
          {preview.questions.map((q, i) => <li key={i}>{q}</li>)}
        </ul>
      )}

      {preview.thresholds && (
        <p className="mt-1.5 text-[0.7rem] text-muted-foreground">{preview.thresholds}</p>
      )}
    </div>
  );
}
