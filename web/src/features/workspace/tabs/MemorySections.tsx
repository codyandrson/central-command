/**
 * The sections of the workspace Memory tab. Each one renders from its own
 * read (`useAgentLearned`) and owns its own loading, error and empty state, so
 * one failing never blanks the others. Everything here is read-only: the
 * operator changes what an agent has learned by deciding its proposals.
 */

import type { ReactNode } from 'react';
import type {
  AgentNote, CharterHistory, CharterVersionRow, EpisodesPayload, GraphEpisode,
  LearnedPayload, LearnedSkillDoc, NotesPayload, PendingProposal, Section,
} from '../hooks/useAgentLearned';

export type LearnedTarget = 'inbox' | 'graph' | 'skills' | 'agents';

/** Where a row's link may take the operator; absent callbacks hide the links. */
export interface LearnedNav {
  onNavigate?: (target: LearnedTarget) => void;
  onOpenSession?: (sessionId: string, agentId: string) => void;
}

const fmt = (n: number) => n.toLocaleString('en-US');
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : '');

const LINK_CLS = 'bg-transparent border-0 p-0 text-[0.667rem] text-purple hover:underline cursor-pointer focus-visible:ring-2 focus-visible:ring-purple/50 focus-visible:ring-offset-0';

function LinkButton({ onClick, children, label }: { onClick: () => void; children: ReactNode; label?: string }) {
  return (
    <button type="button" onClick={onClick} className={LINK_CLS} aria-label={label}>
      {children}
    </button>
  );
}

function Badge({ children, tone }: { children: ReactNode; tone?: 'warn' | 'muted' }) {
  const cls = tone === 'warn'
    ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
    : 'bg-muted/30 text-muted-foreground';
  return (
    <span className={`text-[0.6rem] px-1 py-px rounded-sm leading-tight uppercase tracking-wider ${cls}`}>
      {children}
    </span>
  );
}

interface SectionShellProps {
  id: string;
  title: string;
  count?: number;
  link?: ReactNode;
  children: ReactNode;
}

function SectionShell({ id, title, count, link, children }: SectionShellProps) {
  return (
    <section aria-labelledby={`memory-section-${id}`} className="border-b border-border/40 pb-1">
      <div className="flex items-center gap-2 px-3 pt-3 pb-1">
        <h3
          id={`memory-section-${id}`}
          className="m-0 flex-1 text-[0.667rem] font-normal uppercase tracking-wider text-muted-foreground"
        >
          {title}{count !== undefined ? ` (${count})` : ''}
        </h3>
        {link}
      </div>
      {children}
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <div className="px-3 py-1.5 text-[0.667rem] text-muted-foreground/80 leading-snug">{children}</div>;
}

/** Loading / error / empty handling shared by every section. */
function Body<T>({ section, empty, children }: {
  section: Section<T>;
  /** True when the loaded data holds nothing to list. */
  empty: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (section.error) {
    return (
      <div role="alert" className="mx-3 my-1 flex items-center gap-2 rounded-sm bg-red/10 px-2 py-1.5 text-[0.667rem] text-red">
        <span className="min-w-0 flex-1 wrap-anywhere">{section.error}</span>
        <button type="button" onClick={section.refresh} className="shrink-0 bg-transparent border-0 p-0 text-[0.667rem] text-red underline cursor-pointer">
          Retry
        </button>
      </div>
    );
  }
  if (section.data === null) {
    return <Quiet>{section.loading ? 'Loading…' : 'Nothing to show.'}</Quiet>;
  }
  if (empty(section.data)) return null;
  return <>{children(section.data)}</>;
}

// --- Pending -----------------------------------------------------------------

const PENDING_STATUS: Record<string, string> = { AWAITING_HUMAN: 'awaiting you', PROPOSED: 'proposed' };

function PendingRow({ p }: { p: PendingProposal }) {
  return (
    <div className="border-b border-border/40 px-3 py-2" data-testid="pending-row">
      {p.actions.map((a, i) => (
        <div key={i} className="text-[0.733rem] text-foreground leading-snug wrap-anywhere">{a.label}</div>
      ))}
      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[0.667rem] text-muted-foreground/70">
        <Badge tone="warn">{PENDING_STATUS[p.status] ?? p.status.toLowerCase()}</Badge>
        <span>{day(p.created_at)}</span>
        <span className="wrap-anywhere">{p.intent}</span>
      </div>
    </div>
  );
}

/** Rendered only when something is pending (or the read failed). */
export function PendingSection({ learned, nav }: { learned: Section<LearnedPayload>; nav: LearnedNav }) {
  const pending = learned.data?.pending ?? [];
  if (!learned.error && pending.length === 0) return null;
  return (
    <SectionShell
      id="pending"
      title="Waiting for your decision"
      count={learned.error ? undefined : pending.length}
      link={nav.onNavigate && (
        <LinkButton onClick={() => nav.onNavigate!('inbox')}>Open Decisions Inbox</LinkButton>
      )}
    >
      <Body section={learned} empty={(d) => d.pending.length === 0}>
        {(d) => <>{d.pending.map((p) => <PendingRow key={p.id} p={p} />)}</>}
      </Body>
    </SectionShell>
  );
}

// --- Notes -------------------------------------------------------------------

function Meter({ label, used, budget }: { label: string; used: number; budget: number }) {
  const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 100;
  const over = used > budget;
  return (
    <div className="px-3 pt-1.5 pb-1">
      <div className="flex items-baseline justify-between gap-2 text-[0.667rem] text-muted-foreground">
        <span>{label}</span>
        <span className={over ? 'text-amber-600 dark:text-amber-400' : ''}>
          {`${fmt(used)} of ${fmt(budget)} characters`}
        </span>
      </div>
      <div
        role="meter"
        aria-label={`${label} usage`}
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={used}
        className="mt-1 h-1 overflow-hidden rounded-full bg-muted/40"
      >
        <div
          className={`h-full rounded-full ${over ? 'bg-amber-500' : 'bg-purple/60'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function NoteRow({ note, nav, showDrafter }: { note: AgentNote; nav: LearnedNav; showDrafter: boolean }) {
  const retired = note.retired_at !== null;
  return (
    <div className={`border-b border-border/40 px-3 py-2 ${retired ? 'opacity-70' : ''}`} data-testid="note-row">
      <div className="text-[0.733rem] text-foreground leading-snug wrap-anywhere">{note.text}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.667rem] text-muted-foreground/70">
        <span>{retired ? `added ${day(note.created_at)}, retired ${day(note.retired_at)}` : day(note.created_at)}</span>
        {showDrafter && <span>proposed by {note.agent_id}</span>}
        {note.proposal_id && <code className="text-[0.6rem]" title="The proposal that wrote this note">{note.proposal_id}</code>}
        {note.retired_by && <code className="text-[0.6rem]" title="The proposal that retired this note">{`retired by ${note.retired_by}`}</code>}
        {nav.onOpenSession && note.source_session_id && (
          <LinkButton onClick={() => nav.onOpenSession!(note.source_session_id!, note.agent_id)}>
            Open source session
          </LinkButton>
        )}
      </div>
      {note.source_quote && (
        <details className="mt-1 text-[0.667rem] text-muted-foreground">
          <summary className="cursor-pointer select-none">Grounded in</summary>
          <blockquote className="m-0 mt-1 border-l-2 border-border/60 pl-2 italic leading-snug wrap-anywhere">
            {note.source_quote}
          </blockquote>
        </details>
      )}
    </div>
  );
}

function NoteGroup({ title, notes, used, budget, nav, showDrafter, emptyText }: {
  title: string; notes: AgentNote[]; used: number; budget: number; nav: LearnedNav;
  showDrafter: boolean; emptyText: string;
}) {
  const active = notes.filter((n) => n.retired_at === null);
  return (
    <div data-testid={`notes-group-${showDrafter ? 'operator' : 'private'}`}>
      <Meter label={title} used={used} budget={budget} />
      {active.length === 0
        ? <Quiet>{emptyText}</Quiet>
        : active.map((n) => <NoteRow key={n.id} note={n} nav={nav} showDrafter={showDrafter} />)}
    </div>
  );
}

function NotesBody({ data, nav }: { data: NotesPayload; nav: LearnedNav }) {
  if (!data.rendered) {
    return <Quiet>This agent is shown no notes: it re-derives decisions independently, so nothing standing is put in front of it.</Quiet>;
  }
  const retired = [...data.operator, ...data.private]
    .filter((n) => n.retired_at !== null)
    .sort((a, b) => (b.retired_at ?? '').localeCompare(a.retired_at ?? ''));
  return (
    <>
      <NoteGroup
        title="Operator preferences" notes={data.operator} used={data.usage.operator}
        budget={data.budgets.operator} nav={nav} showDrafter
        emptyText="No operator preferences yet. They are shown to every agent except the auditors."
      />
      <NoteGroup
        title="This agent's notes" notes={data.private} used={data.usage.private}
        budget={data.budgets.private} nav={nav} showDrafter={false}
        emptyText="This agent has no notes of its own yet."
      />
      {retired.length > 0 && (
        <details className="mt-1 border-t border-border/40" data-testid="notes-retired">
          <summary className="cursor-pointer select-none px-3 py-1.5 text-[0.667rem] uppercase tracking-wider text-muted-foreground">
            {`Retired (${retired.length})`}
          </summary>
          {retired.map((n) => (
            <div key={n.id}>
              <div className="px-3 pt-1.5"><Badge tone="muted">{n.scope === 'operator' ? 'operator' : 'private'}</Badge></div>
              <NoteRow note={n} nav={nav} showDrafter={n.scope === 'operator'} />
            </div>
          ))}
        </details>
      )}
      <Quiet>
        To add a note, tell the agent in chat (for a preference: &ldquo;remember that I always&hellip;&rdquo;);
        to remove one, say &ldquo;forget&hellip;&rdquo;. The agent proposes the change and you approve it in the Decisions Inbox.
      </Quiet>
    </>
  );
}

export function NotesSection({ notes, nav }: { notes: Section<NotesPayload>; nav: LearnedNav }) {
  return (
    <SectionShell id="notes" title="Notes">
      <Body section={notes} empty={() => false}>
        {(d) => <NotesBody data={d} nav={nav} />}
      </Body>
    </SectionShell>
  );
}

// --- Graph episodes ----------------------------------------------------------

function EpisodeRow({ e }: { e: GraphEpisode }) {
  return (
    <div className="border-b border-border/40 px-3 py-2" data-testid="episode-row">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className="text-[0.733rem] font-medium text-foreground leading-tight wrap-anywhere">{e.name}</span>
        {e.created_at && <span className="text-[0.667rem] text-muted-foreground/70">{day(e.created_at)}</span>}
      </div>
      <div className="mt-0.5 line-clamp-2 text-[0.667rem] text-muted-foreground leading-snug wrap-anywhere">{e.content}</div>
    </div>
  );
}

export function EpisodesSection({ episodes, loadMore, nav }: {
  episodes: Section<EpisodesPayload>; loadMore: () => void; nav: LearnedNav;
}) {
  const d = episodes.data;
  return (
    <SectionShell
      id="episodes"
      title="Private graph episodes"
      count={d ? d.total : undefined}
      link={nav.onNavigate && <LinkButton onClick={() => nav.onNavigate!('graph')}>Open graph</LinkButton>}
    >
      <Body section={episodes} empty={(p) => p.episodes.length === 0}>
        {(p) => (
          <>
            {p.episodes.map((e) => <EpisodeRow key={e.uuid} e={e} />)}
            {p.has_more && (
              <div className="flex items-center gap-2 px-3 py-1.5 text-[0.667rem] text-muted-foreground">
                <span>{`Showing ${p.episodes.length} of ${p.total}`}</span>
                <LinkButton onClick={loadMore}>Load more</LinkButton>
              </div>
            )}
          </>
        )}
      </Body>
      {d && d.episodes.length === 0 && !episodes.error && (
        <Quiet>No approved episodes in this agent&apos;s private partition. Shared facts live in the graph and are not listed here.</Quiet>
      )}
    </SectionShell>
  );
}

// --- Skill documents ---------------------------------------------------------

function SkillDocRow({ d }: { d: LearnedSkillDoc }) {
  return (
    <div className="border-b border-border/40 px-3 py-2" data-testid="skill-doc-row">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="text-[0.733rem] font-medium text-foreground leading-tight wrap-anywhere">{d.skill_title}</span>
        <Badge tone="muted">{d.doc_key}</Badge>
        <span className="text-[0.667rem] text-muted-foreground">{`v${d.version}`}</span>
        {!d.is_current && <Badge tone="muted">superseded</Badge>}
        {d.retired_at && <Badge tone="warn">retired</Badge>}
        {d.skill_status === 'RETIRED' && <Badge tone="warn">skill retired</Badge>}
      </div>
      <div className="mt-0.5 text-[0.667rem] text-muted-foreground/70">
        {day(d.created_at)}{d.describes ? ` · ${d.describes}` : ''}
      </div>
    </div>
  );
}

export function SkillDocsSection({ learned, nav }: { learned: Section<LearnedPayload>; nav: LearnedNav }) {
  const docs = learned.data?.skill_docs;
  return (
    <SectionShell
      id="skills"
      title="Skill documents it proposed"
      count={docs?.length}
      link={nav.onNavigate && <LinkButton onClick={() => nav.onNavigate!('skills')}>Open Skills</LinkButton>}
    >
      <Body section={learned} empty={(p) => p.skill_docs.length === 0}>
        {(p) => <>{p.skill_docs.map((d) => <SkillDocRow key={d.id} d={d} />)}</>}
      </Body>
      {docs && docs.length === 0 && !learned.error && (
        <Quiet>No approved skill documents from this agent yet. Corrections it proposes to a skill it used land here once you approve them.</Quiet>
      )}
    </SectionShell>
  );
}

// --- Charter -----------------------------------------------------------------

const CHARTER_SHOWN = 5;

function CharterRow({ v, current }: { v: CharterVersionRow; current: boolean }) {
  return (
    <div className="border-b border-border/40 px-3 py-2" data-testid="charter-row">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-[0.733rem] text-foreground">
        <span className="font-medium">{`v${v.version}`}</span>
        {current && <Badge>current</Badge>}
        <span className="text-[0.667rem] text-muted-foreground">by {v.created_by}</span>
        <span className="text-[0.667rem] text-muted-foreground/70">{day(v.created_at)}</span>
      </div>
      {v.rationale && (
        <div className="mt-0.5 text-[0.667rem] text-muted-foreground leading-snug wrap-anywhere">{v.rationale}</div>
      )}
    </div>
  );
}

function CharterBody({ data }: { data: CharterHistory }) {
  if (data.versions.length === 0) {
    return <Quiet>{`No governed charter version yet: this agent runs on its built-in charter (v${data.current.version}).`}</Quiet>;
  }
  const shown = data.versions.slice(0, CHARTER_SHOWN);
  const rest = data.versions.slice(CHARTER_SHOWN);
  return (
    <>
      {shown.map((v) => <CharterRow key={v.version} v={v} current={v.version === data.current.version} />)}
      {rest.length > 0 && (
        <details>
          <summary className="cursor-pointer select-none px-3 py-1.5 text-[0.667rem] text-muted-foreground">
            {`Earlier versions (${rest.length})`}
          </summary>
          {rest.map((v) => <CharterRow key={v.version} v={v} current={v.version === data.current.version} />)}
        </details>
      )}
    </>
  );
}

export function CharterSection({ charter, nav }: { charter: Section<CharterHistory>; nav: LearnedNav }) {
  const d = charter.data;
  return (
    <SectionShell
      id="charter"
      title="Charter versions"
      count={d && d.versions.length > 0 ? d.versions.length : undefined}
      link={nav.onNavigate && <LinkButton onClick={() => nav.onNavigate!('agents')}>Open Agents</LinkButton>}
    >
      <Body section={charter} empty={() => false}>
        {(p) => <CharterBody data={p} />}
      </Body>
    </SectionShell>
  );
}
