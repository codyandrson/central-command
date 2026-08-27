/**
 * ConfigTab — the agent's governed CHARTER (per-agent config in Central Command).
 *
 * The vendored version of this tab browsed SOUL.md/TOOLS.md/… in a Nerve
 * filesystem workspace that does not exist here, so every file read "does not
 * exist yet". The real per-agent guidance is the versioned charter behind
 * /api/cc/charter/:agentId (FastAPI GET/POST /api/agents/{id}/charter).
 *
 * The content is shown and saved VERBATIM: an agent's capability list is
 * GENERATED from its grants at run start, so the UI must never synthesise or
 * strip a section. Saving appends a new version — history is append-only.
 */

import { useEffect, useState, useCallback, useRef } from 'react';
import { RefreshCw, Pencil, Save, X, CheckCircle, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { clearPersistedDraft, readPersistedDraft, writePersistedDraft } from '../persistedDrafts';
import { apiFetch } from '@/lib/apiFetch';

const DRAFT_KIND = 'charter-editor';

interface CharterVersion {
  version: number;
  content: string;
  status?: string;
  rationale: string | null;
  created_by: string;
  created_at: string | null;
}

interface CharterPayload {
  current: CharterVersion;
  versions: CharterVersion[];
}

interface ConfigTabProps {
  agentId: string;
}

/** Workspace tab displaying the agent's current governed charter. */
export function ConfigTab({ agentId }: ConfigTabProps) {
  const [charter, setCharter] = useState<CharterPayload | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialDraft = readPersistedDraft<string>(DRAFT_KIND, agentId);
  const [editing, setEditing] = useState(() => initialDraft !== null);
  const [editContent, setEditContent] = useState(() => initialDraft ?? '');
  const [rationale, setRationale] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const requestVersionRef = useRef(0);
  const agentIdRef = useRef(agentId);

  const content = charter?.current.content ?? null;

  const clearDraft = useCallback(() => clearPersistedDraft(DRAFT_KIND, agentId), [agentId]);

  useEffect(() => () => clearTimeout(feedbackTimer.current), []);

  const load = useCallback(async () => {
    const requestAgentId = agentId;
    const requestVersion = ++requestVersionRef.current;
    setIsLoading(true);
    setError(null);
    try {
      const data = await apiFetch<{ result?: CharterPayload }>(
        `/api/cc/charter/${encodeURIComponent(requestAgentId)}`, undefined, 'Failed to load charter',
      );
      if (requestVersionRef.current !== requestVersion || agentIdRef.current !== requestAgentId) return;
      if (!data.result) throw new Error('Failed to load charter');
      setCharter(data.result);
    } catch (err) {
      if (requestVersionRef.current !== requestVersion || agentIdRef.current !== requestAgentId) return;
      setCharter(null);
      setError((err as Error).message);
    } finally {
      if (requestVersionRef.current === requestVersion) setIsLoading(false);
    }
  }, [agentId]);

  // Agent switch: drop the previous agent's charter and any in-flight read.
  useEffect(() => {
    agentIdRef.current = agentId;
    requestVersionRef.current += 1;
    setCharter(null);
    setError(null);

    const storedDraft = readPersistedDraft<string>(DRAFT_KIND, agentId);
    setEditContent(storedDraft ?? '');
    setEditing(storedDraft !== null);
    setRationale('');
    void load();
  }, [agentId, load]);

  useEffect(() => {
    if (!editing || editContent === (content ?? '')) {
      clearDraft();
      return;
    }
    writePersistedDraft(DRAFT_KIND, agentId, editContent);
  }, [agentId, clearDraft, content, editContent, editing]);

  const showFeedback = useCallback((type: 'success' | 'error', message: string) => {
    clearTimeout(feedbackTimer.current);
    setFeedback({ type, message });
    feedbackTimer.current = setTimeout(() => setFeedback(null), 5000);
  }, []);

  const handleEdit = useCallback(() => {
    setEditContent(readPersistedDraft<string>(DRAFT_KIND, agentId) ?? content ?? '');
    setEditing(true);
    setTimeout(() => textareaRef.current?.focus(), 50);
  }, [agentId, content]);

  const handleSave = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiFetch<{ result?: { version: number; warnings?: string[] } }>(
        `/api/cc/charter/${encodeURIComponent(agentId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: editContent, rationale }),
        },
        'Failed to save',
      );
      clearDraft();
      setEditing(false);
      setRationale('');
      const warnings = data.result?.warnings ?? [];
      showFeedback(
        'success',
        `Saved as v${data.result?.version ?? '?'}${warnings.length ? ` — ${warnings.join('; ')}` : ''}`,
      );
      await load();
    } catch (err) {
      showFeedback('error', (err as Error).message);
    } finally {
      setIsLoading(false);
    }
  }, [agentId, clearDraft, editContent, load, rationale, showFeedback]);

  const handleCancel = useCallback(() => {
    clearDraft();
    setEditing(false);
  }, [clearDraft]);

  // Warn before unload when editing is active (issue #9)
  useEffect(() => {
    if (!editing) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [editing]);

  const current = charter?.current;
  const stamp = current?.created_at ? new Date(current.created_at).toLocaleString() : null;

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-center gap-2 border-b border-border/60 bg-gradient-to-r from-secondary/84 to-card/80 px-3 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[0.733rem] font-semibold text-foreground">
            Charter{current ? ` · v${current.version}` : ''}
          </div>
          <div className="text-[0.667rem] text-muted-foreground truncate">
            {current
              ? [
                  current.version === 0 ? 'built-in (no governed version yet)' : `by ${current.created_by}`,
                  stamp,
                  charter && charter.versions.length ? `${charter.versions.length} version(s)` : null,
                  current.rationale,
                ].filter(Boolean).join(' · ')
              : 'no charter loaded'}
          </div>
        </div>
        <button
          onClick={() => void load()}
          disabled={isLoading}
          className="shell-icon-button size-10 shrink-0 px-0 disabled:cursor-not-allowed disabled:opacity-50"
          title="Refresh"
          aria-label="Refresh charter"
        >
          <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Feedback toast */}
      {feedback && (
        <div className={`px-3 py-1.5 text-[0.667rem] flex items-center gap-1.5 border-b ${
          feedback.type === 'success'
            ? 'bg-green/10 text-green border-green/20'
            : 'bg-red/10 text-red border-red/20'
        }`}>
          {feedback.type === 'success' ? <CheckCircle size={10} /> : <AlertCircle size={10} />}
          {feedback.message}
        </div>
      )}

      {error && (
        <div className="px-3 py-2 text-[0.667rem] text-red bg-red/10">{error}</div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!charter && !isLoading && !error && (
          <div className="text-muted-foreground px-3 py-4 text-[0.733rem] text-center">
            No charter for this agent.
          </div>
        )}

        {charter && !editing && (
          <div className="relative">
            <div className="absolute top-2 right-2 z-10">
              <button
                onClick={handleEdit}
                className="shell-icon-button size-9 px-0"
                title="Edit"
                aria-label="Edit charter"
              >
                <Pencil size={14} />
              </button>
            </div>
            <pre className="px-3 py-2 text-[0.733rem] text-foreground whitespace-pre-wrap break-words [overflow-wrap:anywhere] font-mono leading-relaxed">
              {content}
            </pre>
          </div>
        )}

        {editing && (
          <div className="flex flex-col h-full">
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={e => setEditContent(e.target.value)}
              className="flex-1 w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] px-3 py-2 text-[0.733rem] font-mono bg-background text-foreground border-0 resize-none outline-none focus-visible:ring-2 focus-visible:ring-purple/50 focus-visible:ring-offset-0 focus-visible:ring-inset"
              spellCheck={false}
              wrap="soft"
              aria-label="Charter content"
            />
            <div className="flex items-center gap-2 border-t border-border/60 bg-secondary/28 px-3 py-2">
              <input
                value={rationale}
                onChange={e => setRationale(e.target.value)}
                placeholder="Why this change (recorded on the version)"
                aria-label="Rationale"
                className="min-w-0 flex-1 rounded-md border border-border/60 bg-background px-2 py-1 text-[0.733rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-purple/50"
              />
              <Button
                onClick={() => void handleSave()}
                disabled={isLoading}
                size="sm"
                className="text-[0.733rem] uppercase tracking-[0.12em]"
              >
                <Save size={12} /> Save
              </Button>
              <Button
                onClick={handleCancel}
                variant="outline"
                size="sm"
                className="text-[0.733rem] uppercase tracking-[0.12em]"
              >
                <X size={12} /> Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
