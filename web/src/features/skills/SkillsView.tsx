import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  BookOpen, RefreshCw, FileText, Users, AlertTriangle, ChevronDown, FolderInput,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useSkills } from './useSkills';
import {
  ImportDialog, CreateSkillDialog, AddDocDialog, ImportDocDialog, ImportSiteDialog,
  GrantSkillDialog, RevokeSkillDialog, RetireSkillDialog, RetireDocDialog,
} from './SkillDialogs';
import type { Gap, SkillDoc, SkillRow } from './types';
import { ListDetailSplit } from '@/components/Splitter';

/* ── Small shared pieces ── */

function StatusPill({ status }: { status: string }) {
  const retired = status === 'RETIRED';
  return (
    <span
      className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-[0.08em] ${
        retired
          ? 'border-muted-foreground/30 bg-muted/40 text-muted-foreground'
          : 'border-green/30 bg-green/8 text-green'
      }`}
    >
      {status}
    </span>
  );
}

function SectionLabel({ icon, children, count }: {
  icon: React.ReactNode; children: React.ReactNode; count?: number;
}) {
  return (
    <div className="flex items-center gap-2 px-4 pt-5 pb-2 text-[0.667rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
      {icon}
      {children}
      {count !== undefined && (
        <span className="rounded-full bg-muted px-1.5 py-0.5 tabular-nums">{count}</span>
      )}
    </div>
  );
}

function Collapsible({ title, defaultOpen = false, onOpen, children }: {
  title: React.ReactNode;
  defaultOpen?: boolean;
  /** Called each time it transitions closed → open. Lets a disclosure fetch
   *  its contents only when the operator actually asks for them. */
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  // The `onOpen` side effect fires HERE, not inside the `setOpen` updater:
  // StrictMode double-invokes updaters in dev, which would fire two fetches per
  // disclosure. `open` is the current value, so the transition test is exact.
  const toggle = useCallback(() => {
    if (!open) onOpen?.();
    setOpen(!open);
  }, [open, onOpen]);
  return (
    <div className="rounded-lg border border-border/40">
      <button
        onClick={toggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.733rem] text-foreground hover:bg-muted/30"
      >
        <ChevronDown
          size={12}
          className={`shrink-0 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`}
        />
        {title}
      </button>
      {open && <div className="border-t border-border/40 px-3 py-2">{children}</div>}
    </div>
  );
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toISOString().slice(0, 16).replace('T', ' ');
}

/** The freshness claim a document carries. `describes` is the load-bearing one
 *  — it is what an agent weighs before trusting the material, and what a
 *  `stale_knowledge` gap contradicts. A document with none says so plainly
 *  rather than looking equally trustworthy. */
function DocBadges({ doc }: { doc: SkillDoc }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[0.667rem] text-muted-foreground">
      <span className="cockpit-badge">v{doc.version}</span>
      {doc.describes
        ? <span className="cockpit-badge">describes: {doc.describes}</span>
        : <span className="text-muted-foreground/70">no freshness claim recorded</span>}
      <span>captured {fmtDate(doc.captured_at)}</span>
      {doc.source_url && (
        <a
          href={doc.source_url}
          target="_blank"
          rel="noreferrer"
          className="underline decoration-dotted hover:text-foreground"
        >
          source
        </a>
      )}
      <span>by {doc.added_by}</span>
      <span className="font-mono text-muted-foreground/70">{doc.id}</span>
    </div>
  );
}

/* ── Documents ── */

function DocCard({ doc, loadHistory, onRetire }: {
  doc: SkillDoc;
  loadHistory: (docKey: string) => Promise<SkillDoc[]>;
  /** Absent for guidance (refused server-side) and for already-retired docs. */
  onRetire?: () => void;
}) {
  const [versions, setVersions] = useState<SkillDoc[] | null>(null);
  const [historyError, setHistoryError] = useState('');

  // Fetched on disclosure, and re-fetched each time it is reopened so a
  // version added since cannot go unseen. `doc.id` changes when a new version
  // supersedes this one, which clears the stale list.
  useEffect(() => { setVersions(null); setHistoryError(''); }, [doc.id]);

  const fetchHistory = useCallback(() => {
    loadHistory(doc.doc_key)
      .then((v) => { setVersions(v); setHistoryError(''); })
      .catch((e: unknown) => {
        setHistoryError(e instanceof Error ? e.message : String(e));
        setVersions(null);
      });
  }, [doc.doc_key, loadHistory]);

  return (
    <div className="space-y-2 rounded-lg border border-border/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="cockpit-badge">{doc.kind}</span>
        <span className="text-[0.8rem] font-semibold text-foreground">{doc.title}</span>
        <span className="font-mono text-[0.667rem] text-muted-foreground">{doc.doc_key}</span>
        {onRetire && (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-6 px-2 text-[0.667rem] text-muted-foreground"
            title="New sessions stop receiving and finding this document; running sessions keep the copy they loaded, and every version stays in history."
            onClick={onRetire}
          >
            Retire
          </Button>
        )}
      </div>
      {doc.retired_at && (
        <p className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-[0.7rem] text-muted-foreground">
          Retired {fmtDate(doc.retired_at)} — {doc.retired_reason || 'no reason recorded'}.
          Importing <span className="font-mono">{doc.doc_key}</span> again creates
          the next current version.
        </p>
      )}
      <DocBadges doc={doc} />
      <Collapsible
        title={<span className="text-muted-foreground">Content</span>}
        defaultOpen={doc.kind === 'guidance'}
      >
        <pre className="max-h-[22rem] overflow-auto whitespace-pre-wrap cockpit-wrap text-[0.7rem] leading-relaxed text-foreground">
          {doc.content}
        </pre>
      </Collapsible>
      <Collapsible
        title={<span className="text-muted-foreground">Version history</span>}
        onOpen={fetchHistory}
      >
        {historyError && <p className="text-[0.7rem] text-destructive">{historyError}</p>}
        {!historyError && versions === null && (
          <p className="text-[0.7rem] text-muted-foreground">Loading…</p>
        )}
        {versions?.map((v) => (
          <Collapsible
            key={v.id}
            title={
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="cockpit-badge">v{v.version}</span>
                {v.is_current && <span className="text-green">current</span>}
                <span className="text-muted-foreground">{fmtDate(v.created_at)}</span>
                <span className="min-w-0 wrap-anywhere text-muted-foreground/80">
                  {v.describes || 'no freshness claim'}
                </span>
              </span>
            }
          >
            <pre className="max-h-[18rem] overflow-auto whitespace-pre-wrap cockpit-wrap text-[0.7rem] leading-relaxed text-foreground">
              {v.content}
            </pre>
          </Collapsible>
        ))}
        {versions?.length === 0 && (
          <p className="text-[0.7rem] text-muted-foreground">No versions recorded.</p>
        )}
      </Collapsible>
    </div>
  );
}

/* ── Gaps ── */

/** One declared gap. A `stale_knowledge` row puts the document's claim beside
 *  what the agent observed, because the whole point of that kind is that the
 *  two are comparable at a glance. */
function GapRow({ g, showSubject = true }: { g: Gap; showSubject?: boolean }) {
  return (
    <div className="border-b border-border/20 py-1.5 text-[0.7rem]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="cockpit-badge">{g.agent_id || 'unattributed'}</span>
        <span className="text-muted-foreground">{g.kind}</span>
        {showSubject && <span className="font-mono text-muted-foreground">{g.subject}</span>}
        <span className="ml-auto tabular-nums text-muted-foreground">{fmtDate(g.at)}</span>
      </div>
      <div className="mt-0.5 text-foreground">{g.need}</div>
      {g.kind === 'stale_knowledge' && (
        <div className="mt-1 grid gap-1 sm:grid-cols-2">
          <div className="rounded-md border border-border/40 bg-muted/20 px-2 py-1">
            <div className="text-[0.6rem] uppercase tracking-[0.12em] text-muted-foreground">
              doc says {g.doc_id && <span className="font-mono">({g.doc_id})</span>}
            </div>
            <div className="text-foreground">{g.doc_says}</div>
          </div>
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1">
            <div className="text-[0.6rem] uppercase tracking-[0.12em] text-muted-foreground">
              observed
            </div>
            <div className="text-foreground">{g.observed}</div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Catalog list ── */

function CatalogRow({ s, active, onSelect }: {
  s: SkillRow;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`flex w-full flex-col gap-1 border-b border-border/25 px-4 py-2.5 text-left transition-colors ${
        active ? 'bg-primary/8' : 'hover:bg-muted/30'
      }`}
    >
      <div className="flex items-center gap-2">
        {/* Retired rows stay in the catalog and stay clickable — the Agents
            roster shows retired agents the same way, and a status you cannot
            select is a status you cannot undo. Dimmed, not hidden. */}
        <span className={`truncate text-[0.8rem] font-semibold ${
          s.status === 'RETIRED' ? 'text-muted-foreground' : 'text-foreground'
        }`}>
          {s.title || s.id}
        </span>
        {s.status !== 'ACTIVE' && <StatusPill status={s.status} />}
      </div>
      <div className="line-clamp-2 text-[0.7rem] text-muted-foreground">{s.summary}</div>
      <div className="flex items-center gap-2 text-[0.667rem] text-muted-foreground">
        <span className="cockpit-badge tabular-nums">{s.reference_count} refs</span>
        {/* Computed by the catalog query under the same predicates the detail
            pane uses, so this number and the Held-by section always agree. */}
        <span className="tabular-nums">
          held by {s.holder_count} agent{s.holder_count === 1 ? '' : 's'}
        </span>
      </div>
    </button>
  );
}

/* ── The view ── */

type DialogState =
  | null
  | { kind: 'import' }
  | { kind: 'create' }
  | { kind: 'add-doc' }
  | { kind: 'import-doc' }
  | { kind: 'import-site' }
  | { kind: 'grant' }
  | { kind: 'revoke'; agentId: string }
  | { kind: 'retire' }
  | { kind: 'retire-doc'; docKey: string };

export function SkillsView() {
  const {
    skills, detail, gaps, roster, loading, detailLoading, error,
    refresh, loadDetail,
    createSkill, addDoc, retireDoc, retireSkill, reactivateSkill, grant, revoke,
    importFolder, importDoc, importFile, importSite, history,
  } = useSkills();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const closeDialog = useCallback(() => setDialog(null), []);

  // Select the first skill once the catalog arrives, but never re-select on a
  // later refresh — that would yank the operator out of what they're reading.
  useEffect(() => {
    if (selectedId || skills.length === 0) return;
    setSelectedId(skills[0].id);
  }, [skills, selectedId]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const select = useCallback((id: string) => setSelectedId(id), []);
  const loadHistory = useCallback(
    (docKey: string) => history(detail?.skill.id ?? '', docKey),
    [history, detail?.skill.id],
  );

  const guidance = detail?.docs.filter((d) => d.kind === 'guidance') ?? [];
  // The detail payload carries retired doc_keys too (flagged by retired_at on
  // a non-current row) so their history keeps a click-target — split them out
  // rather than interleaving dead material with live references.
  const references = detail?.docs.filter((d) => d.kind !== 'guidance' && !d.retired_at) ?? [];
  const retiredDocs = detail?.docs.filter((d) => d.kind !== 'guidance' && d.retired_at) ?? [];
  // `subject` is FREE TEXT, not a skill id — real Claude wrote
  // "Confluence Cloud REST API integration" for a gap about the confluence
  // skill. The original `g.subject === detail.skill.id` therefore matched
  // nothing while asserting nobody had declared a gap naming the skill, with
  // the matching gap visible in the queue directly below. Match loosely, in
  // both directions, against the id AND the title; the whole-team queue below
  // stays the authoritative surface, which is why a loose match here is safe.
  const skillGaps = useMemo(() => {
    if (!detail) return [];
    const needles = [detail.skill.id, detail.skill.title]
      .map((s) => (s ?? '').toLowerCase())
      .filter(Boolean);
    return gaps.filter((g) => {
      const subject = (g.subject ?? '').toLowerCase();
      if (!subject) return false;
      return needles.some((n) => subject.includes(n) || n.includes(subject));
    });
  }, [detail, gaps]);

  return (
    <>
    {/* The list/detail boundary is draggable now, so the side-by-side test can no
        longer be a container query — ListDetailSplit measures it and renders the
        plain stacked column below the breakpoint, exactly as this did before. */}
    <ListDetailSplit
      id="cc-skills"
      aside={<>
        <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
          <BookOpen size={14} className="text-primary" />
          <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Skills</span>
          <span className="cockpit-badge tabular-nums">{skills.length}</span>
          <button
            onClick={() => refresh()}
            title="Refresh"
            aria-label="Refresh skills catalog"
            className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex items-center gap-1.5 border-b border-border/40 px-4 py-2">
          <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'import' })}>
            <FolderInput size={12} className="mr-1" />
            Import folder
          </Button>
          <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'create' })}>
            New skill
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && <p className="px-4 py-2 text-[0.733rem] text-destructive">{error}</p>}
          {skills.length === 0 && !loading && (
            <p className="px-4 py-3 text-[0.733rem] text-muted-foreground">
              The library holds nothing yet. Import a skill folder to start it.
            </p>
          )}
          {skills.map((s) => (
            <CatalogRow
              key={s.id}
              s={s}
              active={s.id === selectedId}
              onSelect={() => select(s.id)}
            />
          ))}
        </div>
      </>}
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!detail && detailLoading && (
          <p className="px-4 py-4 text-[0.733rem] text-muted-foreground">Loading…</p>
        )}
        {!detail && !detailLoading && (
          <p className="px-4 py-4 text-[0.733rem] text-muted-foreground">Select a skill.</p>
        )}
        {detail && (
          <div className="pb-8 pt-4">
            <div className="px-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-balance text-[1.05rem] font-semibold text-foreground">
                  {detail.skill.title || detail.skill.id}
                </h2>
                <StatusPill status={detail.skill.status} />
                <span className="cockpit-badge font-mono">{detail.skill.id}</span>
                <div className="ml-auto flex items-center gap-1.5">
                  <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'add-doc' })}>
                    Add document
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'import-doc' })}>
                    Import from URL
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'import-site' })}>
                    Import site
                  </Button>
                  {detail.skill.status === 'RETIRED' ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => reactivateSkill(detail.skill.id)}
                      title="Puts the skill back in the library. Every agent that held it when it was retired holds it again — the grants were never removed."
                    >
                      Reactivate
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDialog({ kind: 'retire' })}
                    >
                      Retire
                    </Button>
                  )}
                </div>
              </div>
              <p className="mt-1 text-[0.8rem] text-muted-foreground">{detail.skill.summary}</p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.667rem] text-muted-foreground">
                <span className="tabular-nums">{references.length} reference documents</span>
                <span>created {fmtDate(detail.skill.created_at)}</span>
              </div>
              {detail.skill.status === 'RETIRED' && (
                <p className="mt-2 rounded-md border border-border/50 bg-muted/30 px-3 py-2 text-[0.733rem] text-muted-foreground">
                  Retired {fmtDate(detail.skill.retired_at)} —{' '}
                  {detail.skill.retired_reason || 'no reason recorded'}.
                  {' '}No agent receives it while it is retired, and its grants
                  are still on the record: reactivating restores every holder it
                  had, with nothing to re-grant.
                </p>
              )}
            </div>

            <SectionLabel icon={<FileText size={11} />}>Guidance</SectionLabel>
            <div className="space-y-2 px-4">
              {guidance.length === 0 ? (
                /* Copy verified against `runtime/skills.py:105-140` and
                   `:151-190`: no current guidance doc means `capabilities_for`
                   SKIPS the skill entirely (a log.warning and `continue`) —
                   the agent gets no instructions AND no reference search. But
                   `catalog_line` is built from grants, not from documents, so
                   the skill is still named to the model. This state is broken,
                   not half-working, and the operator must not read it as a
                   partial win. */
                <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[0.733rem] text-foreground">
                  <strong className="font-semibold">No guidance document — this skill is broken, not partial.</strong>{' '}
                  A skill with no current guidance is skipped entirely when an
                  agent's capabilities are assembled: no instructions, and no
                  reference search either, however many reference documents it
                  holds. It is still named in the agent's skills catalog, so the
                  model sees it listed and finds nothing behind it. Add a
                  guidance document, or retire the skill.
                </p>
              ) : (
                guidance.map((d) => <DocCard key={d.id} doc={d} loadHistory={loadHistory} />)
              )}
            </div>

            <SectionLabel icon={<FileText size={11} />} count={references.length}>
              Reference documents
            </SectionLabel>
            <div className="space-y-2 px-4">
              {references.length === 0 ? (
                <p className="text-[0.733rem] text-muted-foreground">
                  No reference documents — nothing here is searchable yet.
                </p>
              ) : (
                references.map((d) => (
                  <DocCard
                    key={d.id}
                    doc={d}
                    loadHistory={loadHistory}
                    onRetire={() => setDialog({ kind: 'retire-doc', docKey: d.doc_key })}
                  />
                ))
              )}
              {retiredDocs.length > 0 && (
                /* Display only, like the catalog's History toggle: these keys
                   are invisible to new agent sessions; the rows exist so their
                   version history keeps a click-target. */
                <Collapsible
                  title={
                    <span className="text-muted-foreground">
                      Retired documents ({retiredDocs.length})
                    </span>
                  }
                >
                  <div className="space-y-2">
                    {retiredDocs.map((d) => (
                      <DocCard key={d.id} doc={d} loadHistory={loadHistory} />
                    ))}
                  </div>
                </Collapsible>
              )}
            </div>

            <SectionLabel icon={<Users size={11} />} count={detail.agents.length}>
              Held by
            </SectionLabel>
            <div className="space-y-2 px-4">
              {detail.agents.length === 0 ? (
                /* A retired skill ALWAYS lists no holders — `list_agent_skills`
                   drops grants on a non-ACTIVE skill — so the ordinary "nobody
                   holds this" line would assert something the payload cannot
                   tell us. Say what is actually known in each case. */
                <p className="text-[0.733rem] text-muted-foreground">
                  {detail.skill.status === 'RETIRED'
                    ? 'While a skill is retired no agent receives it, and holders are not listed. Any grants it had are still recorded and come back when it is reactivated.'
                    : 'No agent holds this skill, so nothing in the team can read it yet.'}
                </p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {detail.agents.map((id) => (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/20 px-2 py-0.5 text-[0.7rem] text-foreground"
                    >
                      <span className="font-mono">{id}</span>
                      <button
                        onClick={() => setDialog({ kind: 'revoke', agentId: id })}
                        className="text-muted-foreground transition-colors hover:text-destructive"
                        aria-label={`Revoke ${detail.skill.id} from ${id}`}
                        title={`Revoke from ${id}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="pt-1">
                {/* Granting a RETIRED skill writes a row that delivers nothing
                    and shows nothing — the holder list above is computed with
                    `s.status = 'ACTIVE'`. Refuse it visibly instead. */}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setDialog({ kind: 'grant' })}
                  disabled={detail.skill.status === 'RETIRED'}
                  title={detail.skill.status === 'RETIRED'
                    ? 'A retired skill is delivered to nobody — reactivate it first'
                    : undefined}
                >
                  Grant to an agent
                </Button>
              </div>
            </div>

            <SectionLabel icon={<AlertTriangle size={11} />} count={skillGaps.length}>
              Gaps mentioning this skill
            </SectionLabel>
            <div className="px-4">
              {skillGaps.length === 0 ? (
                <p className="text-[0.733rem] text-muted-foreground">
                  No declared gap mentions this skill's name. Gap subjects are
                  free text, so read the whole-team queue below before
                  concluding there is none.
                </p>
              ) : (
                skillGaps.map((g, i) => (
                  <GapRow key={`${g.at}-${i}`} g={g} showSubject={false} />
                ))
              )}
            </div>
          </div>
        )}

        {/* OUTSIDE the `detail &&` block on purpose. This queue spans all
            skills, and a `missing_knowledge` gap names a subject the library
            may not hold AT ALL — so the empty-library case, where there is no
            skill to select and no detail to load, is exactly when the operator
            most needs to read it. Nesting it under a selection would hide it
            precisely then, and would also blank it whenever `skills.detail`
            fails. It depends only on `gaps`, so it renders whenever the page
            does. */}
        <div className="pb-8">
          <SectionLabel icon={<AlertTriangle size={11} />} count={gaps.length}>
            Declared gaps — the whole team
          </SectionLabel>
          <div className="px-4">
            {gaps.length === 0 ? (
              <p className="text-[0.733rem] text-muted-foreground">
                No agent has declared a gap. That is either a well-stocked
                library or a team that is guessing — the record cannot tell
                you which.
              </p>
            ) : (
              gaps.map((g, i) => <GapRow key={`all-${g.at}-${i}`} g={g} />)
            )}
          </div>
        </div>
      </div>
    </ListDetailSplit>

      <ImportDialog
        open={dialog?.kind === 'import'}
        onOpenChange={(v) => !v && closeDialog()}
        onImport={async (body) => {
          const res = await importFolder(body);
          setSelectedId(res.skill_id);
          return res;
        }}
      />
      <CreateSkillDialog
        open={dialog?.kind === 'create'}
        onOpenChange={(v) => !v && closeDialog()}
        onCreate={async (body) => { await createSkill(body); setSelectedId(body.id); }}
      />
      {detail && (
        <>
          <AddDocDialog
            skill={detail.skill}
            open={dialog?.kind === 'add-doc'}
            onOpenChange={(v) => !v && closeDialog()}
            onAdd={(body) => addDoc(detail.skill.id, body)}
          />
          <ImportDocDialog
            skill={detail.skill}
            open={dialog?.kind === 'import-doc'}
            onOpenChange={(v) => !v && closeDialog()}
            onImport={(body) => importDoc(body)}
            onImportFile={(body) => importFile(body)}
          />
          <ImportSiteDialog
            skill={detail.skill}
            open={dialog?.kind === 'import-site'}
            onOpenChange={(v) => !v && closeDialog()}
            onImport={(body) => importSite(body)}
          />
          <GrantSkillDialog
            skill={detail.skill}
            holders={detail.agents}
            roster={roster}
            open={dialog?.kind === 'grant'}
            onOpenChange={(v) => !v && closeDialog()}
            onGrant={(agentId) => grant(agentId, detail.skill.id)}
          />
          <RevokeSkillDialog
            skill={detail.skill}
            agentId={dialog?.kind === 'revoke' ? dialog.agentId : ''}
            open={dialog?.kind === 'revoke'}
            onOpenChange={(v) => !v && closeDialog()}
            onRevoke={(reason) =>
              revoke(dialog?.kind === 'revoke' ? dialog.agentId : '', detail.skill.id, reason)}
          />
          <RetireSkillDialog
            skill={detail.skill}
            open={dialog?.kind === 'retire'}
            onOpenChange={(v) => !v && closeDialog()}
            onRetire={(reason) => retireSkill(detail.skill.id, reason)}
          />
          <RetireDocDialog
            skill={detail.skill}
            docKey={dialog?.kind === 'retire-doc' ? dialog.docKey : ''}
            open={dialog?.kind === 'retire-doc'}
            onOpenChange={(v) => !v && closeDialog()}
            onRetire={(reason) =>
              retireDoc(detail.skill.id, dialog?.kind === 'retire-doc' ? dialog.docKey : '', reason)}
          />
        </>
      )}
    </>
  );
}
