import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Loader2, Paperclip, Upload, X } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useSessionContext } from '@/contexts/SessionContext';
import type { TaskStatus, TaskPriority } from './types';
import type { CreateTaskPayload, TaskAttachment } from './hooks/useKanban';
import { AssigneeCombobox } from './components/AssigneeCombobox';
import { buildAssigneeOptions } from './lib/assigneeOptions';

// Documents only — task attachments reuse the chat seam
// (`central_command/api/attachments.py`), which extracts to text via markitdown.
// Images are rejected client-side; chat already refuses image delivery for
// the current (text-only) model, so staging one here would just fail server-side.
const ACCEPT_DOCS = '.pdf,.docx,.pptx,.xlsx,.txt,.md,.csv,text/*';

interface StagedFile {
  id: string;
  file: File;
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === 'string') {
        resolve(result.split(',', 2)[1] ?? '');
        return;
      }
      reject(new Error(`Failed to read "${file.name}".`));
    };
    reader.onerror = () => reject(new Error(`Failed to read "${file.name}".`));
    reader.readAsDataURL(file);
  });
}

const STATUS_OPTIONS: { value: TaskStatus; label: string }[] = [
  { value: 'backlog', label: 'Backlog' },
  { value: 'todo', label: 'To Do' },
  { value: 'in-progress', label: 'In Progress' },
  { value: 'review', label: 'Review' },
];

const PRIORITY_OPTIONS: { value: TaskPriority; label: string }[] = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'normal', label: 'Normal' },
  { value: 'low', label: 'Low' },
];

interface CreateTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (payload: CreateTaskPayload) => Promise<void>;
}

export function CreateTaskDialog({ open, onOpenChange, onCreate }: CreateTaskDialogProps) {
  const { sessions, agentName } = useSessionContext();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<TaskStatus>('todo');
  const [priority, setPriority] = useState<TaskPriority>('normal');
  const [labelsRaw, setLabelsRaw] = useState('');
  const [assignee, setAssignee] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stagedFiles, setStagedFiles] = useState<StagedFile[]>([]);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  /* Focus title on open */
  useEffect(() => {
    if (open) {
      // Small delay so the dialog animation finishes
      const t = setTimeout(() => titleRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
  }, [open]);

  /* Reset form on close */
  useEffect(() => {
    if (!open) {
      setTitle('');
      setDescription('');
      setStatus('todo');
      setPriority('normal');
      setLabelsRaw('');
      setAssignee('');
      setError(null);
      setStagedFiles([]);
    }
  }, [open]);

  const trimmedTitle = title.trim();
  const isValid = trimmedTitle.length > 0 && trimmedTitle.length <= 500;
  const assigneeOptions = useMemo(
    () => buildAssigneeOptions(sessions, agentName),
    [agentName, sessions],
  );

  const handleFilesSelected = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return;
    const rejectedImage = Array.from(files).some(f => f.type.startsWith('image/'));
    if (rejectedImage) {
      setError('Images are not supported as task attachments — attach a document instead.');
    }
    const docs = Array.from(files).filter(f => !f.type.startsWith('image/'));
    setStagedFiles(prev => [
      ...prev,
      ...docs.map(file => ({ id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`, file })),
    ]);
  }, []);

  const removeStagedFile = useCallback((id: string) => {
    setStagedFiles(prev => prev.filter(f => f.id !== id));
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    // Ignore leaves into a child element — only clear on leaving the zone itself.
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setIsDraggingOver(false);
  }, []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
    if (!assignee) {
      setError('Attachments require an assignee — an unassigned task has no model to deliver them to.');
      return;
    }
    handleFilesSelected(e.dataTransfer.files);
  }, [assignee, handleFilesSelected]);

  // Attachments require an assignee (no model to deliver them to otherwise) —
  // clearing the assignee after staging files must drop the staged files too,
  // not silently submit them.
  useEffect(() => {
    if (!assignee && stagedFiles.length > 0) setStagedFiles([]);
  }, [assignee, stagedFiles.length]);

  const handleSubmit = useCallback(async () => {
    if (!isValid || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const labels = labelsRaw
        .split(',')
        .map(l => l.trim())
        .filter(Boolean);
      const attachments: TaskAttachment[] = await Promise.all(
        stagedFiles.map(async ({ file }) => ({
          name: file.name,
          mimeType: file.type || 'application/octet-stream',
          content: await readAsBase64(file),
        })),
      );
      const payload: CreateTaskPayload = {
        title: trimmedTitle,
        description: description.trim() || undefined,
        status,
        priority,
        ...(labels.length > 0 ? { labels } : {}),
        ...(assignee ? { assignee } : {}),
        ...(attachments.length > 0 ? { attachments } : {}),
      };
      await onCreate(payload);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create task. Try again.");
    } finally {
      setSubmitting(false);
    }
  }, [isValid, submitting, trimmedTitle, description, status, priority, labelsRaw, assignee, stagedFiles, onCreate, onOpenChange]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && document.activeElement?.tagName !== 'TEXTAREA') {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  const selectClass = 'cockpit-select h-11 text-sm';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[92vw] gap-4 p-5 sm:max-w-[680px]" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <div className="cockpit-kicker">
            <span className="text-primary">◆</span>
            Task Board
          </div>
          <DialogTitle className="text-[1.4rem] font-semibold tracking-[-0.03em] text-foreground">Create task</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">Capture the work, set the lane, and leave the board readable for the next handoff.</DialogDescription>
        </DialogHeader>

        {error && (
          <div className="cockpit-note text-sm" data-tone="danger">
            {error}
          </div>
        )}

        {/* Title */}
        <div>
          <label htmlFor="kb-new-title" className="cockpit-field-label mb-2 block">
            Title <span className="text-destructive">*</span>
          </label>
          <Input
            id="kb-new-title"
            ref={titleRef}
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Task title…"
            maxLength={500}
            className="h-11"
            aria-invalid={title.length > 0 && !isValid}
          />
          {title.length > 0 && trimmedTitle.length === 0 && (
            <p className="text-[0.667rem] text-destructive mt-0.5">Title is required.</p>
          )}
        </div>

        {/* Description */}
        <div>
          <label htmlFor="kb-new-desc" className="cockpit-field-label mb-2 block">
            Description
          </label>
          <textarea
            id="kb-new-desc"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Markdown description (optional)…"
            rows={4}
            className="cockpit-textarea min-h-[144px]"
          />
        </div>

        {/* 2-col grid for secondary fields */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* Status */}
          <div>
            <label htmlFor="kb-new-status" className="cockpit-field-label mb-2 block">
              Status
            </label>
            <select
              id="kb-new-status"
              value={status}
              onChange={e => setStatus(e.target.value as TaskStatus)}
              className={selectClass}
            >
              {STATUS_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Priority */}
          <div>
            <label htmlFor="kb-new-priority" className="cockpit-field-label mb-2 block">
              Priority
            </label>
            <select
              id="kb-new-priority"
              value={priority}
              onChange={e => setPriority(e.target.value as TaskPriority)}
              className={selectClass}
            >
              {PRIORITY_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Labels */}
          <div>
            <label htmlFor="kb-new-labels" className="cockpit-field-label mb-2 block">
              Labels
            </label>
            <Input
              id="kb-new-labels"
              value={labelsRaw}
              onChange={e => setLabelsRaw(e.target.value)}
              placeholder="bug, frontend, urgent"
              className="h-11"
            />
            <p className="mt-1 text-[0.733rem] text-muted-foreground">Comma-separated</p>
          </div>

          {/* Assignee */}
          <div>
            <label htmlFor="kb-new-assignee" className="cockpit-field-label mb-2 block">
              Assignee
            </label>
            <AssigneeCombobox
              id="kb-new-assignee"
              value={assignee}
              onChange={setAssignee}
              options={assigneeOptions}
              ariaLabel="Assignee"
              placeholder="Select assignee"
              noResultsText="No matching assignees"
              inline
            />
          </div>
        </div>

        {/* Attachments */}
        <div>
          <div className="flex items-center gap-2">
            <label className="cockpit-field-label block">Attachments</label>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!assignee}
              title={!assignee ? 'Choose an assignee to attach documents' : 'Attach a document'}
              className="inline-flex items-center gap-1 text-[0.733rem] text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Paperclip size={13} />
              Attach
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPT_DOCS}
              className="hidden"
              onChange={e => { handleFilesSelected(e.target.files); e.target.value = ''; }}
            />
          </div>
          {!assignee && (
            <p className="mt-1 text-[0.733rem] text-muted-foreground">
              Attachments require an assignee — an unassigned task has no model to deliver them to.
            </p>
          )}
          <div
            role="button"
            tabIndex={assignee ? 0 : -1}
            onClick={() => assignee && fileInputRef.current?.click()}
            onKeyDown={e => {
              if (assignee && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            aria-disabled={!assignee}
            className={`mt-2 flex items-center justify-center gap-2 rounded-md border border-dashed px-3 py-4 text-[0.733rem] transition-colors ${
              !assignee
                ? 'border-border text-muted-foreground/50 cursor-not-allowed'
                : isDraggingOver
                  ? 'border-primary bg-primary/5 text-foreground cursor-pointer'
                  : 'border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground cursor-pointer'
            }`}
          >
            <Upload size={14} />
            Drag files here or click to browse
          </div>
          {stagedFiles.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {stagedFiles.map(({ id, file }) => (
                <li
                  key={id}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-2 py-1 text-[0.733rem]"
                >
                  <span className="min-w-0 max-w-[180px] truncate">{file.name}</span>
                  <button
                    type="button"
                    onClick={() => removeStagedFile(id)}
                    className="text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${file.name}`}
                  >
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter className="mt-1">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={!isValid || submitting}>
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Create Task
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
