import { useState, useEffect, useMemo } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { InlineSelectOption } from '@/components/ui/InlineSelect';
import type { ImportResult, SiteImportResult, SkillBase, SkillDetail } from './types';
import type { RosterEntry } from './useSkills';

/**
 * The Skills page's write actions.
 *
 * Every one is a DIRECT operator action with an event record, not a proposal:
 * the library is internal state, so authoring it changes nothing in the world
 * and carries no approval gate. What these dialogs owe the operator is an
 * honest account of what each action does to the record — chiefly that a
 * document is versioned, never overwritten, and a revocation is a timestamp,
 * never a delete.
 */

function useAsyncAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (fn: () => Promise<unknown>, onDone?: () => void) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      onDone?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

/* ── Import a skill folder ── */

export function ImportDialog({ open, onOpenChange, onImport }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onImport: (body: {
    path: string; skill_id?: string; source_url?: string; describes?: string;
  }) => Promise<ImportResult>;
}) {
  const [path, setPath] = useState('');
  const [skillId, setSkillId] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [describes, setDescribes] = useState('');
  const [result, setResult] = useState<ImportResult | null>(null);
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setPath(''); setSkillId(''); setSourceUrl(''); setDescribes(''); setResult(null);
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Import a skill folder
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            A folder holding a <span className="font-mono">SKILL.md</span> and an
            optional <span className="font-mono">references/</span> directory.
            The path is read on the API host, not from this browser. To
            REFRESH an existing skill, fill in its skill id below: that adds a
            NEW version of each document — nothing is overwritten, and every
            earlier version stays readable. Left blank, the id is derived from
            the folder, and an import is refused if that id already exists, so
            two vendors' folders can never merge into one skill unnoticed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="cockpit-field-label mb-2 block">Folder path (on the API host)</label>
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/path/to/Central Command/skills/pydantic-ai"
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Skill id (optional)</label>
            <input
              value={skillId}
              onChange={(e) => setSkillId(e.target.value)}
              placeholder="blank: derived from SKILL.md/folder; must not already exist"
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Source URL (optional)</label>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="where this material came from"
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Describes (optional)</label>
            <input
              value={describes}
              onChange={(e) => setDescribes(e.target.value)}
              placeholder="what this documents, and which version — e.g. 'Jira Cloud REST v3'"
              className="cockpit-input"
            />
            <p className="cockpit-note mt-2">
              This is the freshness claim an agent reads before trusting the
              material, and what a <span className="font-mono">stale_knowledge</span>{' '}
              gap argues against. Name the version.
            </p>
          </div>
        </div>

        {result && (
          <p className="cockpit-note">
            Imported into <span className="font-mono">{result.skill_id}</span>:
            guidance <span className="font-mono">{result.guidance}</span> plus{' '}
            {result.references.length} reference document
            {result.references.length === 1 ? '' : 's'}.
          </p>
        )}
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Close
          </Button>
          <Button
            disabled={busy || !path.trim()}
            onClick={() => run(
              async () => { setResult(await onImport({
                path: path.trim(),
                skill_id: skillId.trim() || undefined,
                source_url: sourceUrl.trim() || undefined,
                describes: describes.trim() || undefined,
              })); },
            )}
          >
            {busy ? 'Importing…' : 'Import'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Create an empty skill ── */

export function CreateSkillDialog({ open, onOpenChange, onCreate }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreate: (body: { id: string; title: string; summary: string }) => Promise<void>;
}) {
  const [id, setId] = useState('');
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => {
    if (!open) return;
    setId(''); setTitle(''); setSummary('');
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            New skill
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            An empty entry in the catalog. Add its guidance and reference
            documents afterwards, or import a folder instead. Creating with an
            id that already exists updates that skill's title and summary.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="cockpit-field-label mb-2 block">Id</label>
            <input
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="jira"
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Jira Cloud"
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Summary</label>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={3}
              placeholder="The routing line — how an agent decides this skill is the one it needs."
              className="cockpit-textarea min-h-[76px]"
            />
          </div>
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !id.trim() || !title.trim() || !summary.trim()}
            onClick={() => run(
              () => onCreate({ id: id.trim(), title: title.trim(), summary: summary.trim() }),
              () => onOpenChange(false),
            )}
          >
            {busy ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Add / replace a document ── */

export function AddDocDialog({ skill, open, onOpenChange, onAdd }: {
  skill: SkillBase;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onAdd: (body: {
    doc_key: string; kind: string; title: string; content: string;
    source_url?: string | null; describes?: string | null;
  }) => Promise<unknown>;
}) {
  const [docKey, setDocKey] = useState('');
  const [kind, setKind] = useState('reference');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [describes, setDescribes] = useState('');
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setDocKey(''); setKind('reference'); setTitle(''); setContent('');
    setSourceUrl(''); setDescribes('');
  }, [open]);

  const kindOptions: InlineSelectOption[] = [
    { value: 'guidance', label: 'guidance' },
    { value: 'reference', label: 'reference' },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Add a document to <span className="font-mono">{skill.id}</span>
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Saving writes a NEW version of this <span className="font-mono">doc_key</span>{' '}
            and supersedes the current one — it is never an in-place edit, and
            the previous version stays readable in this document's history.
            Reusing an existing key is how you update a document.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[12rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Document key</label>
              <input
                value={docKey}
                onChange={(e) => setDocKey(e.target.value)}
                placeholder="guidance, or a reference slug like 'rest-v3-issues'"
                className="cockpit-input"
              />
            </div>
            <div>
              <label className="cockpit-field-label mb-2 block">Kind</label>
              {/* `inline` is REQUIRED inside a Radix Dialog — without it the
                  listbox renders through a sibling portal whose clicks the
                  dialog intercepts, so no option can be chosen. */}
              <InlineSelect
                value={kind}
                onChange={setKind}
                options={kindOptions}
                ariaLabel="Document kind"
                inline
              />
            </div>
          </div>
          <p className="cockpit-note">
            Guidance arrives whole in an agent's context when the skill loads.
            Reference documents are chunked and searched — only these are
            indexed, so material an agent should look up belongs here.
          </p>
          <div>
            <label className="cockpit-field-label mb-2 block">Title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="cockpit-input"
            />
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Content (markdown)</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={12}
              className="cockpit-textarea min-h-[16rem] font-mono text-[0.7rem]"
            />
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[14rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Source URL (optional)</label>
              <input
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                className="cockpit-input"
              />
            </div>
            <div className="min-w-[14rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Describes (optional)</label>
              <input
                value={describes}
                onChange={(e) => setDescribes(e.target.value)}
                placeholder="what this documents, and which version"
                className="cockpit-input"
              />
            </div>
          </div>
        </div>

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !docKey.trim() || !title.trim() || !content.trim()}
            onClick={() => run(
              () => onAdd({
                doc_key: docKey.trim(),
                kind,
                title: title.trim(),
                content,
                source_url: sourceUrl.trim() || null,
                describes: describes.trim() || null,
              }),
              () => onOpenChange(false),
            )}
          >
            {busy ? 'Saving…' : 'Save new version'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Import a document from a URL or pasted text ── */

export function ImportDocDialog({ skill, open, onOpenChange, onImport, onImportFile }: {
  skill: SkillBase;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onImport: (body: {
    skill_id: string; doc_key: string; title?: string;
    url?: string; content?: string; describes?: string;
  }) => Promise<unknown>;
  onImportFile: (body: {
    skill_id: string; doc_key: string; title?: string;
    describes?: string; file: File;
  }) => Promise<unknown>;
}) {
  const [mode, setMode] = useState<'url' | 'paste' | 'file'>('url');
  const [docKey, setDocKey] = useState('');
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [content, setContent] = useState('');
  const [describes, setDescribes] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setMode('url'); setDocKey(''); setTitle(''); setUrl(''); setContent(''); setDescribes('');
    setFile(null);
  }, [open]);

  const modeOptions: InlineSelectOption[] = [
    { value: 'url', label: 'Fetch a URL' },
    { value: 'paste', label: 'Paste content' },
    { value: 'file', label: 'File' },
  ];

  const ready = docKey.trim() && (
    mode === 'url' ? url.trim() : mode === 'paste' ? content.trim() : !!file
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Import a document into <span className="font-mono">{skill.id}</span>
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Fetching converts HTML to markdown server-side and records the URL
            as this document's <span className="font-mono">source_url</span>.
            Like adding a document by hand, this writes a NEW version and
            supersedes the current one for this <span className="font-mono">doc_key</span> —
            reuse an existing key to refresh it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[12rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Document key</label>
              <input
                value={docKey}
                onChange={(e) => setDocKey(e.target.value)}
                placeholder="guidance, or a reference slug like 'rest-v3-issues'"
                className="cockpit-input"
              />
            </div>
            <div>
              <label className="cockpit-field-label mb-2 block">Source</label>
              <InlineSelect
                value={mode}
                onChange={(v) => setMode(v as 'url' | 'paste' | 'file')}
                options={modeOptions}
                ariaLabel="Import source"
                inline
              />
            </div>
          </div>
          <div>
            <label className="cockpit-field-label mb-2 block">Title (optional)</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="defaults to the document key"
              className="cockpit-input"
            />
          </div>
          {mode === 'url' ? (
            <div>
              <label className="cockpit-field-label mb-2 block">URL</label>
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…"
                className="cockpit-input"
              />
            </div>
          ) : mode === 'paste' ? (
            <div>
              <label className="cockpit-field-label mb-2 block">Content (markdown)</label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={12}
                className="cockpit-textarea min-h-[16rem] font-mono text-[0.7rem]"
              />
            </div>
          ) : (
            <div>
              <label className="cockpit-field-label mb-2 block">File (PDF, docx, pptx, xlsx, …)</label>
              <input
                type="file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="cockpit-input"
              />
              <p className="cockpit-note mt-1">
                Converted to markdown server-side; title defaults to the filename.
              </p>
            </div>
          )}
          <div>
            <label className="cockpit-field-label mb-2 block">Describes (optional)</label>
            <input
              value={describes}
              onChange={(e) => setDescribes(e.target.value)}
              placeholder="what this documents, and which version"
              className="cockpit-input"
            />
          </div>
        </div>

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !ready}
            onClick={() => run(
              () => mode === 'file'
                ? onImportFile({
                    skill_id: skill.id,
                    doc_key: docKey.trim(),
                    title: title.trim() || undefined,
                    describes: describes.trim() || undefined,
                    file: file as File,
                  })
                : onImport({
                    skill_id: skill.id,
                    doc_key: docKey.trim(),
                    title: title.trim() || undefined,
                    url: mode === 'url' ? url.trim() : undefined,
                    content: mode === 'paste' ? content : undefined,
                    describes: describes.trim() || undefined,
                  }),
              () => onOpenChange(false),
            )}
          >
            {busy ? 'Importing…' : 'Import'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Import a whole documentation site ── */

export function ImportSiteDialog({ skill, open, onOpenChange, onImport }: {
  skill: SkillBase;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onImport: (body: {
    skill_id: string; url: string; doc_key_prefix?: string;
    max_pages?: number; path_prefix?: string; describes?: string; render?: boolean;
    filter_mode?: string;
  }) => Promise<SiteImportResult>;
}) {
  const [url, setUrl] = useState('');
  const [maxPages, setMaxPages] = useState('100');
  const [pathPrefix, setPathPrefix] = useState('');
  const [docKeyPrefix, setDocKeyPrefix] = useState('');
  const [describes, setDescribes] = useState('');
  const [render, setRender] = useState(false);
  const [llmFilter, setLlmFilter] = useState(false);
  const [result, setResult] = useState<SiteImportResult | null>(null);
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setUrl(''); setMaxPages('100'); setPathPrefix(''); setDocKeyPrefix('');
    setDescribes(''); setRender(false); setLlmFilter(false); setResult(null);
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Import a whole site into <span className="font-mono">{skill.id}</span>
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Tries the site's <span className="font-mono">llms-full.txt</span>{' '}
            first, falls back to its sitemap, and imports one reference
            document per page or section — never overwriting anything, only
            adding new versions of matching document keys.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="cockpit-field-label mb-2 block">Site URL</label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://docs.example.com/guide"
              className="cockpit-input"
            />
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[10rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Max pages</label>
              <input
                value={maxPages}
                onChange={(e) => setMaxPages(e.target.value)}
                inputMode="numeric"
                placeholder="100 (capped at 200)"
                className="cockpit-input"
              />
            </div>
            <div className="min-w-[14rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Path prefix (optional)</label>
              <input
                value={pathPrefix}
                onChange={(e) => setPathPrefix(e.target.value)}
                placeholder="defaults to the URL's own path"
                className="cockpit-input"
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[10rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Doc key prefix (optional)</label>
              <input
                value={docKeyPrefix}
                onChange={(e) => setDocKeyPrefix(e.target.value)}
                className="cockpit-input"
              />
            </div>
            <div className="min-w-[14rem] flex-1">
              <label className="cockpit-field-label mb-2 block">Describes (optional)</label>
              <input
                value={describes}
                onChange={(e) => setDescribes(e.target.value)}
                placeholder="what this documents, and which version"
                className="cockpit-input"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-[0.8rem] text-foreground">
            <input type="checkbox" checked={render} onChange={(e) => setRender(e.target.checked)} />
            Use browser rendering (for JS-only sites; requires cc-crawler)
          </label>
          {render && (
            <label className="flex items-center gap-2 text-[0.8rem] text-foreground">
              <input type="checkbox" checked={llmFilter} onChange={(e) => setLlmFilter(e.target.checked)} />
              LLM cleanup (one model call per page via LiteLLM; falls back to heuristic pruning)
            </label>
          )}
        </div>

        {result && (
          <p className="cockpit-note">
            Imported {result.imported.length} document{result.imported.length === 1 ? '' : 's'} via
            the <span className="font-mono">{result.rung}</span> rung
            {result.skipped.length > 0 && `, skipped ${result.skipped.length}`}
            {result.truncated && ' (truncated at the page limit)'}.
            {result.notes.map((n, i) => <span key={i} className="block">{n}</span>)}
          </p>
        )}
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Close
          </Button>
          <Button
            disabled={busy || !url.trim()}
            onClick={() => run(async () => { setResult(await onImport({
              skill_id: skill.id,
              url: url.trim(),
              doc_key_prefix: docKeyPrefix.trim() || undefined,
              max_pages: maxPages.trim() ? Number(maxPages.trim()) : undefined,
              path_prefix: pathPrefix.trim() || undefined,
              describes: describes.trim() || undefined,
              render,
              filter_mode: render && llmFilter ? 'llm' : undefined,
            })); })}
          >
            {busy ? 'Importing…' : 'Import site'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Grant to an agent ── */

export function GrantSkillDialog({ skill, holders, roster, open, onOpenChange, onGrant }: {
  skill: SkillBase;
  holders: SkillDetail['agents'];
  roster: RosterEntry[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onGrant: (agentId: string) => Promise<void>;
}) {
  const held = useMemo(() => new Set(holders), [holders]);
  // Retired agents are off the active roster, so they are not offered a new
  // skill. An existing holder that has since retired still shows as a chip.
  const available = useMemo(
    () => roster.filter((a) => !held.has(a.id) && a.status !== 'RETIRED'),
    [roster, held],
  );
  const [agentId, setAgentId] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setAgentId(available[0]?.id ?? ''); }, [open, available]);

  const options: InlineSelectOption[] = available.map((a) => ({
    value: a.id, label: a.name ? `${a.id} — ${a.name}` : a.id,
  }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Grant <span className="font-mono">{skill.id}</span> to an agent
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            A granted skill puts this material within the agent's reach: its
            guidance, plus one search tool scoped to this skill's own reference
            documents and nothing else. It grants no <em>gated</em> capability —
            no propose, no write — so it changes what the agent KNOWS and what
            it can look up, never what it may do to the world.
          </DialogDescription>
        </DialogHeader>

        {available.length === 0 ? (
          <p className="cockpit-note">
            Every active agent already holds this skill.
          </p>
        ) : (
          <div>
            <label className="cockpit-field-label mb-2 block">Agent</label>
            {/* `inline` — see the note in AddDocDialog. */}
            <InlineSelect
              value={agentId}
              onChange={setAgentId}
              options={options}
              ariaLabel="Agent to grant this skill to"
              inline
            />
          </div>
        )}
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !agentId}
            onClick={() => run(() => onGrant(agentId), () => onOpenChange(false))}
          >
            {busy ? 'Granting…' : 'Grant'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Revoke from an agent ── */

export function RevokeSkillDialog({ skill, agentId, open, onOpenChange, onRevoke }: {
  skill: SkillBase;
  agentId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onRevoke: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setReason(''); }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Revoke <span className="font-mono">{skill.id}</span> from{' '}
            <span className="font-mono">{agentId}</span>?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Revocation stamps the grant with a timestamp and a reason; the row
            stays on the record, so the history of who held what when is never
            lost. The agent stops loading this material on its next run.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">
            Reason (recorded on the revocation event)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why does this agent no longer need this skill?"
            className="cockpit-textarea min-h-[76px]"
          />
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !reason.trim()}
            onClick={() => run(() => onRevoke(reason.trim()), () => onOpenChange(false))}
          >
            {busy ? 'Revoking…' : 'Revoke'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Retire a skill ── */

export function RetireDocDialog({ skill, docKey, open, onOpenChange, onRetire }: {
  skill: SkillBase;
  docKey: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onRetire: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setReason(''); }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Retire <span className="font-mono">{docKey}</span> from{' '}
            <span className="font-mono">{skill.id}</span>?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            A status flip, not a deletion. New agent sessions stop receiving and
            finding this document; sessions already running keep the copy they
            loaded, exactly as a paused session keeps its charter. Every version
            stays readable under Retired documents. Importing the same doc key
            again later simply creates the next current version.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">
            Reason (recorded on the retirement event)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why is this document leaving the skill?"
            className="cockpit-textarea min-h-[76px]"
          />
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !reason.trim()}
            onClick={() => run(() => onRetire(reason.trim()), () => onOpenChange(false))}
          >
            {busy ? 'Retiring…' : 'Retire document'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RetireSkillDialog({ skill, open, onOpenChange, onRetire }: {
  skill: SkillBase;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onRetire: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setReason(''); }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Retire <span className="font-mono">{skill.id}</span>?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Retiring is a status flip, not a deletion: the skill stops being
            delivered to anyone while its documents, versions and grants stay on
            the record. Retiring does not revoke a single grant — every agent
            that holds it keeps the grant and simply stops receiving the skill,
            which is why it will show no holders while retired. Reactivate it
            and every one of those holders has it again, with nothing to
            re-grant.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">
            Reason (recorded on the retirement event)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why is this skill leaving the library?"
            className="cockpit-textarea min-h-[76px]"
          />
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !reason.trim()}
            onClick={() => run(() => onRetire(reason.trim()), () => onOpenChange(false))}
          >
            {busy ? 'Retiring…' : 'Retire'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
