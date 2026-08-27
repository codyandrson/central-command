import { memo, useState, useEffect } from 'react';
import { Clock, Play, CheckCircle2, AlertCircle, XCircle, Compass, CornerDownRight, StopCircle, Ban } from 'lucide-react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { KanbanTask } from './types';
import { getTaskPriorityLabel, getTaskPriorityTone, getTaskRunStatus, getTaskRunTone } from './tone';

/* ── D7 phase 2: delegation markers ── */
function agentLabel(assignee?: string): string {
  if (!assignee) return '';
  return assignee === 'operator' ? 'Operator' : assignee.replace('agent:', '@');
}

/** Project card: a task that fanned work out to sub-agents. */
function ProjectDelegationRow({ count, done }: { count: number; done: number }) {
  const allDone = done >= count;
  return (
    <div className="mt-2 ml-4 flex items-center gap-1.5">
      <span
        className="inline-flex items-center gap-1 rounded-full border border-orange/35 bg-orange/10 px-2 py-0.5 text-[0.667rem] font-semibold text-orange"
        title={`This project delegated ${count} sub-task${count === 1 ? '' : 's'} to the team`}
      >
        <Compass size={10} />
        {count} sub-task{count === 1 ? '' : 's'}
      </span>
      <span className={`text-[0.667rem] font-medium tabular-nums ${allDone ? 'text-green' : 'text-muted-foreground'}`}>
        {done}/{count} settled
      </span>
    </div>
  );
}

/** Sub-task card: a task fanned out from a parent session (an orchestrator
 * delegation, or — since a task created from an approved task.create
 * proposal also carries `parentSessionId`, pointing at the PROPOSING session
 * — a task created off the back of another agent's proposal). The parent's
 * title is shown when known; without it the copy stays source-agnostic
 * rather than assuming "orchestrator". */
function SubtaskDelegationRow({ agent, parentTitle }: { agent?: string; parentTitle?: string }) {
  return (
    <div className="mt-2 ml-4 flex items-center gap-1.5 text-[0.667rem] text-info/90">
      <CornerDownRight size={10} className="shrink-0" />
      <span className="font-semibold">{agentLabel(agent) || 'sub-agent'}</span>
      <span className="text-muted-foreground">·</span>
      <span className="truncate text-muted-foreground" title={parentTitle ? `Part of “${parentTitle}”` : 'Linked to a parent session'}>
        {parentTitle ? `part of “${parentTitle}”` : 'linked to a parent session'}
      </span>
    </div>
  );
}

/* ── Run status indicators ── */
function RunBadge({ status }: { status: string }) {
  const safeStatus = getTaskRunStatus(status);
  const tone = getTaskRunTone(safeStatus);

  switch (safeStatus) {
    case 'running':
      return (
        <span className={`inline-flex items-center gap-1 text-[0.667rem] font-semibold ${tone.textClass}`}>
          <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
          {/* Same word as the sessions sidebar's badge — one vocabulary for
              "an agent is mid-turn", whichever surface you are looking at. */}
          Working
        </span>
      );
    case 'done':
      return (
        <span className={`inline-flex items-center gap-1 text-[0.667rem] font-semibold ${tone.textClass}`}>
          <CheckCircle2 size={10} /> Done
        </span>
      );
    case 'error':
      return (
        <span className={`inline-flex items-center gap-1 text-[0.667rem] font-semibold ${tone.textClass}`}>
          <AlertCircle size={10} /> Error
        </span>
      );
    case 'aborted':
      return (
        <span className={`inline-flex items-center gap-1 text-[0.667rem] font-semibold ${tone.textClass}`}>
          <XCircle size={10} /> Aborted
        </span>
      );
    default:
      return null;
  }
}

interface KanbanCardProps {
  task: KanbanTask;
  onClick: (task: KanbanTask) => void;
  /** True when rendered inside DragOverlay — skips sortable hook */
  isOverlay?: boolean;
  /** Alias for isOverlay (compat with KanbanBoard) */
  isDragOverlay?: boolean;
}

export const KanbanCard = memo(function KanbanCard({ task, onClick, isOverlay, isDragOverlay }: KanbanCardProps) {
  const overlay = isOverlay || isDragOverlay;
  return overlay ? (
    <CardContent task={task} onClick={onClick} isDragging isOverlay />
  ) : (
    <SortableCard task={task} onClick={onClick} />
  );
});

/* ── Sortable wrapper (only used for in-place cards, not overlay) ── */
function SortableCard({ task, onClick }: { task: KanbanTask; onClick: (task: KanbanTask) => void }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: task.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <CardContent task={task} onClick={onClick} isDragging={isDragging} />
    </div>
  );
}

/* ── Visual card content (shared between sortable + overlay) ── */
function CardContent({
  task,
  onClick,
  isDragging,
  isOverlay,
}: {
  task: KanbanTask;
  onClick: (task: KanbanTask) => void;
  isDragging?: boolean;
  isOverlay?: boolean;
}) {
  const priorityTone = getTaskPriorityTone(task.priority);
  const priorityLabel = getTaskPriorityLabel(task.priority);

  return (
    <button
      type="button"
      onClick={() => { if (!isDragging) onClick(task); }}
      className={`group w-full cursor-pointer rounded-[18px] border border-border/70 bg-background/58 px-3 py-3 text-left shadow-[0_10px_26px_rgba(0,0,0,0.14)] transition-[transform,box-shadow,border-color,background-color,opacity] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        isOverlay
          ? 'scale-[1.02] rotate-[1deg] border-primary/40 bg-card/92 shadow-[0_18px_40px_rgba(0,0,0,0.28)]'
          : isDragging
            ? 'opacity-30'
            : 'hover:-translate-y-px hover:border-primary/24 hover:bg-card/80 hover:shadow-[0_16px_34px_rgba(0,0,0,0.2)]'
      }`}
    >
      {/* Row 1: priority dot + title */}
      <div className="flex items-start gap-2">
        <span
          className={`mt-1 h-2 w-2 shrink-0 rounded-full ${priorityTone.dotClass}`}
          title={priorityLabel}
          aria-label={`Priority: ${priorityLabel}`}
          role="img"
        />
        <span className="text-[0.867rem] font-semibold leading-[18px] text-foreground line-clamp-2 min-w-0">
          {task.title}
        </span>
      </div>

      {/* Row 2: description preview */}
      {task.description && (
        <p className="mt-1 ml-4 text-[0.733rem] leading-[15px] text-muted-foreground line-clamp-1">
          {task.description}
        </p>
      )}

      {/* Row 3: labels */}
      {task.labels.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5 ml-4">
          {task.labels.slice(0, 3).map((label, idx) => (
            <span
              key={`${label}-${idx}`}
              className="rounded-full border border-border/55 bg-background/50 px-2 py-0.5 text-[0.667rem] font-medium leading-none text-muted-foreground"
            >
              {label}
            </span>
          ))}
          {task.labels.length > 3 && (
            <span className="text-[0.667rem] text-muted-foreground">
              +{task.labels.length - 3}
            </span>
          )}
        </div>
      )}

      {/* D7 phase 2: delegation — a project's fan-out, or a sub-task's origin */}
      {task.delegation?.role === 'project' && (
        <ProjectDelegationRow
          count={task.delegation.childCount ?? 0}
          done={task.delegation.childDone ?? 0}
        />
      )}
      {task.delegation?.role === 'subtask' && (
        <SubtaskDelegationRow agent={task.assignee} parentTitle={task.delegation.parentTitle} />
      )}

      {/* Row 3: meta line (assignee, run status, time) */}
      <div className="flex items-center gap-2 mt-1.5 ml-4 text-[0.733rem] text-muted-foreground">
        {task.assignee && task.delegation?.role !== 'subtask' && (
          <span className="truncate max-w-[100px]">
            {agentLabel(task.assignee)}
          </span>
        )}

        {task.run && <RunBadge status={task.run.status} />}

        {task.stopped && (
          <span className="inline-flex items-center gap-1 text-[0.667rem] font-semibold text-orange">
            <StopCircle size={10} /> Stopped
          </span>
        )}

        {/* CANCELLED folds into the Done lane — this is the card's own tell,
            since the column header now just says "Done". */}
        {task.status === 'CANCELLED' && (
          <span className="inline-flex items-center gap-1 text-[0.667rem] font-semibold text-muted-foreground">
            <Ban size={10} /> Cancelled
          </span>
        )}

        {task.run?.status === 'running' && task.run.startedAt && (
          <span className="inline-flex items-center gap-0.5 text-[0.667rem] text-info/80">
            <Clock size={9} />
            <ElapsedTime since={task.run.startedAt} />
          </span>
        )}

        {task.dueAt && (
          <span className="inline-flex items-center gap-0.5 ml-auto">
            <Play size={9} className="rotate-90" />
            {new Date(task.dueAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </span>
        )}
      </div>
    </button>
  );
}

/* ── Tiny elapsed-time component (ticks every second) ── */
function ElapsedTime({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return <span>{m}:{s.toString().padStart(2, '0')}</span>;
}
