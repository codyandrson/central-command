import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Users, RefreshCw, FileText, KeyRound, Activity, MessageSquareQuote,
  ChevronDown, BookOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useAgents } from './useAgents';
import { proposalStatusLabel } from './proposalStatus';
import {
  HireDialog, RetireDialog, GrantDialog, RevokeDialog, CharterEditor, RevertDialog,
  CoachDialog, SkillGrantDialog, SkillRevokeDialog,
} from './AgentDialogs';
import { AgentModelEffort } from './AgentModelEffort';
import type {
  AgentDetail, AgentRow, CharterVersion, Grant, PackInfo, SkillGrant,
} from './types';
import type { ProposalDetail } from '../decisions/types';
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

// (The `Unbuilt` scaffold chip lived here through stages 1–2. Every control it
// stood in for is real as of stage 3, so it is gone rather than kept "just in
// case" — a scaffold nobody can see is indistinguishable from a missing
// feature, and one nothing uses is just dead code.)

function Collapsible({ title, defaultOpen = false, children }: {
  title: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-border/40">
      <button
        onClick={() => setOpen((o) => !o)}
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

/* ── Roster list ── */

function RosterRow({ a, active, onSelect }: {
  a: AgentRow; active: boolean; onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`flex w-full flex-col gap-1 border-b border-border/25 px-4 py-2.5 text-left transition-colors ${
        active ? 'bg-primary/8' : 'hover:bg-muted/30'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-[0.8rem] font-semibold text-foreground">{a.name || a.id}</span>
        <StatusPill status={a.status} />
      </div>
      <div className="flex items-center gap-2 text-[0.667rem] text-muted-foreground">
        <span className="cockpit-badge">charter v{a.charter_version}</span>
        {(a.active_sessions ?? 0) > 0 && (
          <span className="cockpit-badge tabular-nums text-primary">
            {a.active_sessions} active
          </span>
        )}
        <span className="tabular-nums">
          {a.packs?.length ?? 0} pack{(a.packs?.length ?? 0) === 1 ? '' : 's'}
        </span>
        {/* `== null` on purpose — catches undefined too, so a payload missing
            the field reads as "no decisions yet" rather than "NaN% approved". */}
        {a.approval_rate == null ? (
          <span>no decisions yet</span>
        ) : (
          <span className="tabular-nums">{Math.round(a.approval_rate * 100)}% approved</span>
        )}
      </div>
      <div className="truncate text-[0.667rem] text-muted-foreground/80">{a.role}</div>
    </button>
  );
}

/* ── Detail sections ── */

function IdentityPanel({ a, onEditCharter, onSaveModel, onRetire, onReactivate, onCoach }: {
  a: AgentRow;
  onEditCharter: () => void;
  onSaveModel: (model: string | null, thinking: string | null) => Promise<void>;
  onRetire: () => void;
  onReactivate: () => void;
  onCoach: () => void;
}) {
  const lanes = [
    a.taskable && 'taskable',
    a.consultable && 'consultable',
    a.conversational && 'conversational',
  ].filter(Boolean) as string[];
  return (
    <div className="px-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-balance text-[1.05rem] font-semibold text-foreground">{a.name || a.id}</h2>
        <StatusPill status={a.status} />
        <span className="cockpit-badge">{a.id}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={onEditCharter}>Edit charter</Button>
          {a.status === 'RETIRED' ? (
            <Button size="sm" variant="outline" onClick={onReactivate}>Reactivate</Button>
          ) : (
            <Button size="sm" variant="outline" onClick={onRetire}>Retire</Button>
          )}
          {/* A retired agent is not coachable (it is off the active roster),
              but its charter and record stay readable — so the control is
              disabled and says why, rather than vanishing. */}
          <Button
            size="sm"
            variant="outline"
            onClick={onCoach}
            disabled={a.status === 'RETIRED'}
            title={a.status === 'RETIRED'
              ? 'Retired agents are not coachable — reactivate it first'
              : undefined}
          >
            Coach
          </Button>
        </div>
      </div>
      <p className="mt-1 text-[0.8rem] text-muted-foreground">{a.role}</p>
      {/* The SAME model/effort selectors as the chat header, saved on change —
          one consistent control everywhere the operator picks a model. */}
      <div className="mt-2">
        <AgentModelEffort agent={a} onSave={onSaveModel} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.667rem] text-muted-foreground">
        <span>lanes: {lanes.length ? lanes.join(' · ') : 'none'}</span>
        {a.template && <span>hired from: {a.template}</span>}
        {a.hired_by && <span>hired by: {a.hired_by}</span>}
        <span>created: {fmtDate(a.created_at)}</span>
      </div>
      {a.status === 'RETIRED' && (
        <p className="mt-2 rounded-md border border-border/50 bg-muted/30 px-3 py-2 text-[0.733rem] text-muted-foreground">
          Retired {fmtDate(a.retired_at)} — {a.retired_reason || 'no reason recorded'}
        </p>
      )}
      {a.deviations && (
        <p className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[0.733rem] text-foreground">
          <strong className="font-semibold">Recorded deviation from standard management:</strong>{' '}
          {a.deviations}
        </p>
      )}
    </div>
  );
}

/**
 * Collapsed-by-default link from a coached charter version back to the
 * proposal that produced it — the operator-set rule that anything reviewed
 * renders its recorded source inline or one click away. Fetches lazily, on
 * first expand, via the same `proposals.get` RPC the Decisions Inbox uses.
 */
function ProposalLink({ proposalId, getProposal }: {
  proposalId: string;
  getProposal: (id: string) => Promise<ProposalDetail>;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || detail || error) return;
    getProposal(proposalId).then(setDetail, (e: unknown) => {
      setError(e instanceof Error ? e.message : String(e));
    });
  }, [open, detail, error, proposalId, getProposal]);

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-[0.7rem] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
      >
        From proposal {proposalId}
      </button>
      {open && (
        <div className="mt-1.5 rounded-lg border border-border/40 bg-muted/20 p-2.5 text-[0.7rem]">
          {error && <p className="text-destructive">{error}</p>}
          {!error && !detail && <p className="text-muted-foreground">Loading…</p>}
          {detail && (
            <>
              <p className="mb-1.5 text-foreground">{detail.intent}</p>
              {(detail.evidence ?? []).map((e, i) => (
                <div key={i} className="mb-1 rounded-md border border-border/30 px-2 py-1">
                  <span className="cockpit-badge mr-2">{e.kind}</span>
                  {e.claim && <span className="text-muted-foreground">{e.claim}</span>}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function CharterPanel({ charter, onRevert, getProposal }: {
  charter: AgentDetail['charter'];
  onRevert: (version: number) => void;
  getProposal: (id: string) => Promise<ProposalDetail>;
}) {
  const { current, versions } = charter;
  return (
    <div className="px-4">
      <div className="mb-2 flex items-center gap-2 text-[0.733rem] text-muted-foreground">
        <span className="cockpit-badge">v{current.version}</span>
        <span>by {current.created_by}</span>
        <span>{fmtDate(current.created_at)}</span>
        {current.version === 0 && (
          <span className="text-muted-foreground/80">— built-in code charter, no governed version yet</span>
        )}
      </div>
      {current.rationale && (
        <p className="mb-2 text-[0.733rem] italic text-muted-foreground">{current.rationale}</p>
      )}
      <pre className="max-h-[22rem] overflow-auto whitespace-pre-wrap cockpit-wrap rounded-lg border border-border/40 bg-muted/20 p-3 text-[0.7rem] leading-relaxed text-foreground">
        {current.content}
      </pre>
      {current.proposal_id && (
        <ProposalLink proposalId={current.proposal_id} getProposal={getProposal} />
      )}
      {versions.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {versions.map((v: CharterVersion) => (
            <Collapsible
              key={v.version}
              title={
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="cockpit-badge">v{v.version}</span>
                  <span className="text-muted-foreground">{v.status}</span>
                  <span className="text-muted-foreground">{v.created_by}</span>
                  <span className="text-muted-foreground">{fmtDate(v.created_at)}</span>
                  <span className="min-w-0 wrap-anywhere text-muted-foreground/80">{v.rationale || ''}</span>
                </span>
              }
            >
              <pre className="max-h-[18rem] overflow-auto whitespace-pre-wrap cockpit-wrap text-[0.7rem] leading-relaxed text-foreground">
                {v.content}
              </pre>
              {v.proposal_id && (
                <ProposalLink proposalId={v.proposal_id} getProposal={getProposal} />
              )}
              {v.status !== 'CURRENT' && (
                <div className="mt-2">
                  <Button size="sm" variant="outline" onClick={() => onRevert(v.version)}>
                    Revert to this version
                  </Button>
                </div>
              )}
            </Collapsible>
          ))}
        </div>
      )}
    </div>
  );
}

function GrantsPanel({ grants, packs, onGrant, onRevoke }: {
  grants: Grant[];
  packs: PackInfo[];
  onGrant: () => void;
  onRevoke: (pack: string) => void;
}) {
  const catalog = new Map(packs.map((p) => [p.name, p]));
  const active = grants.filter((g) => !g.revoked_at);
  const revoked = grants.filter((g) => g.revoked_at);
  return (
    <div className="space-y-2 px-4">
      {active.length === 0 && (
        <p className="text-[0.733rem] text-muted-foreground">
          No active grants — this agent holds no tools and can only answer in text.
        </p>
      )}
      {active.map((g) => {
        const p = catalog.get(g.pack);
        return (
          <Collapsible
            key={g.pack}
            title={
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="cockpit-badge">{g.pack}</span>
                <span className="min-w-0 wrap-anywhere text-muted-foreground">{p?.description || ''}</span>
              </span>
            }
          >
            <div className="space-y-1.5 text-[0.7rem]">
              <div>
                <span className="text-muted-foreground">tools: </span>
                {p?.tools.length ? p.tools.join(', ') : <span className="text-muted-foreground">none</span>}
              </div>
              <div>
                <span className="text-muted-foreground">can propose: </span>
                {p?.capabilities.length
                  ? p.capabilities.join(', ')
                  : <span className="text-muted-foreground">nothing — read-only pack</span>}
              </div>
              <div className="text-muted-foreground">
                granted by {g.granted_by} · {fmtDate(g.granted_at)}
              </div>
              <Button size="sm" variant="outline" onClick={() => onRevoke(g.pack)}>
                Revoke
              </Button>
            </div>
          </Collapsible>
        );
      })}
      <div className="pt-1">
        <Button size="sm" variant="outline" onClick={onGrant}>Grant a pack</Button>
      </div>
      {revoked.length > 0 && (
        <div className="pt-2">
          <div className="pb-1 text-[0.667rem] uppercase tracking-[0.14em] text-muted-foreground">
            Revoked — kept on the record, never deleted
          </div>
          {revoked.map((g) => (
            <div key={`${g.pack}-${g.revoked_at}`} className="flex min-w-0 flex-wrap items-center gap-2 py-0.5 text-[0.7rem] text-muted-foreground">
              <span className="cockpit-badge line-through">{g.pack}</span>
              <span>revoked {fmtDate(g.revoked_at)}</span>
              <span className="min-w-0 wrap-anywhere">{g.revoked_reason || 'no reason recorded'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The other half of the capability surface: packs are what the agent can DO,
 * skills are what it KNOWS. Same visual grammar as `GrantsPanel` on purpose —
 * one relation, one write path, and the operator reads them the same way.
 *
 * The amber states are the reason this panel is worth building. A grant row can
 * exist, look healthy, and deliver NOTHING, in two independent ways; both are
 * stated from what `runtime/skills.py` actually does, and
 * `tests/test_agent_skills_panel.py` holds the `has_guidance` flag to the
 * runtime's real behaviour so this copy cannot quietly become false.
 */
function SkillsPanel({ skills, onGrant, onRevoke }: {
  skills: SkillGrant[];
  onGrant: () => void;
  onRevoke: (skillId: string) => void;
}) {
  const active = skills.filter((s) => !s.revoked_at);
  const revoked = skills.filter((s) => s.revoked_at);
  return (
    <div className="space-y-2 px-4">
      {active.length === 0 && (
        <p className="text-[0.733rem] text-muted-foreground">
          No skills granted — this agent works from its charter alone. It is told
          which skills the library holds, so it can declare a missing_knowledge
          gap rather than guess.
        </p>
      )}
      {active.map((s) => {
        const retired = s.status === 'RETIRED';
        return (
          <Collapsible
            key={s.skill_id}
            title={
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="cockpit-badge">{s.skill_id}</span>
                <span className="min-w-0 wrap-anywhere text-muted-foreground">{s.title}</span>
                {(retired || !s.has_guidance) && (
                  <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-[0.08em] text-amber-600 dark:text-amber-400">
                    not delivered
                  </span>
                )}
              </span>
            }
          >
            <div className="space-y-1.5 text-[0.7rem]">
              <div className="text-foreground">{s.summary}</div>
              <div className="text-muted-foreground">
                {s.reference_count} ref{s.reference_count === 1 ? '' : 's'}
                {' · '}granted by {s.granted_by} · {fmtDate(s.granted_at)}
              </div>
              {retired && (
                <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[0.7rem] text-foreground">
                  <strong className="font-semibold">Retired skill — not delivered.</strong>{' '}
                  Retiring a skill never touches its grants, so the record still
                  says this agent holds it. But the runtime skips grants on
                  retired skills, and the agent's skills catalog does not name
                  this skill at all — not even as something it could ask for.
                  Reactivate the skill on the Skills page, or revoke this grant.
                </p>
              )}
              {!retired && !s.has_guidance && (
                <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[0.7rem] text-foreground">
                  <strong className="font-semibold">
                    No guidance document — not delivered, and not searchable either.
                  </strong>{' '}
                  The runtime skips a granted skill with no current guidance
                  document entirely rather than falling back to
                  references-only, so this agent gets neither the guidance nor a
                  search over its {s.reference_count} reference document
                  {s.reference_count === 1 ? '' : 's'}. Its skills catalog still
                  names this skill and tells it to load the skill before working
                  in the subject — so the agent can ask for something it cannot
                  get. Add a guidance document on the Skills page, or revoke this
                  grant.
                </p>
              )}
              <Button size="sm" variant="outline" onClick={() => onRevoke(s.skill_id)}>
                Revoke
              </Button>
            </div>
          </Collapsible>
        );
      })}
      <div className="pt-1">
        <Button size="sm" variant="outline" onClick={onGrant}>Grant a skill</Button>
      </div>
      {revoked.length > 0 && (
        <div className="pt-2">
          <div className="pb-1 text-[0.667rem] uppercase tracking-[0.14em] text-muted-foreground">
            Revoked — kept on the record, never deleted
          </div>
          {revoked.map((s) => (
            <div
              key={`${s.skill_id}-${s.revoked_at}`}
              className="flex min-w-0 flex-wrap items-center gap-2 py-0.5 text-[0.7rem] text-muted-foreground"
            >
              <span className="cockpit-badge line-through">{s.skill_id}</span>
              <span>revoked {fmtDate(s.revoked_at)}</span>
              <span className="min-w-0 wrap-anywhere">{s.revoked_reason || 'no reason recorded'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RecordPanel({ detail }: { detail: AgentDetail }) {
  const { agent, proposals, sessions } = detail;
  const t = agent.proposals;
  // Degrade honestly rather than crash: a missing tally is a broken payload,
  // and the operator should be told that — not shown a fabricated 0, and not
  // shown a blank page where the rest of the profile was still true.
  if (!t) {
    return (
      <p className="px-4 text-[0.733rem] text-destructive">
        Outcome record unavailable — the roster payload carried no proposal tally
        for this agent. The charter and capabilities above are unaffected.
      </p>
    );
  }
  return (
    <div className="px-4">
      <div className="mb-3 flex flex-wrap gap-4 text-[0.733rem]">
        <span className="text-muted-foreground">
          proposals: <span className="tabular-nums text-foreground">{t.total}</span>
        </span>
        <span className="text-muted-foreground">
          executed: <span className="tabular-nums text-foreground">{t.EXECUTED ?? 0}</span>
        </span>
        <span className="text-muted-foreground">
          rejected: <span className="tabular-nums text-foreground">{t.REJECTED ?? 0}</span>
        </span>
        <span className="text-muted-foreground">
          failed: <span className="tabular-nums text-foreground">{t.FAILED ?? 0}</span>
        </span>
        <span className="text-muted-foreground">
          approval rate:{' '}
          <span className="tabular-nums text-foreground">
            {agent.approval_rate == null ? 'n/a' : `${Math.round(agent.approval_rate * 100)}%`}
          </span>
        </span>
        <span className="text-muted-foreground">
          sessions: <span className="tabular-nums text-foreground">{sessions.length}</span>
        </span>
      </div>
      {proposals.length === 0 ? (
        <p className="text-[0.733rem] text-muted-foreground">This agent has never proposed anything.</p>
      ) : (
        <div className="space-y-1">
          {proposals.slice(0, 12).map((p) => (
            <div key={p.id} className="flex items-start gap-2 border-b border-border/20 py-1.5 text-[0.7rem]">
              <span className="cockpit-badge shrink-0" title={p.status}>
                {proposalStatusLabel(p.status)}
              </span>
              <span className="min-w-0 flex-1 text-foreground">{p.intent}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{fmtDate(p.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SignalsPanel({ signals, onCoach }: {
  signals: AgentDetail['signals'];
  onCoach: () => void;
}) {
  const [filter, setFilter] = useState('');
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return signals;
    return signals.filter((s) =>
      s.kind.toLowerCase().includes(q) ||
      (s.intent ?? '').toLowerCase().includes(q) ||
      s.feedback.toLowerCase().includes(q)
    );
  }, [signals, filter]);

  return (
    <div className="px-4">
      {signals.length === 0 ? (
        <p className="text-[0.733rem] text-muted-foreground">
          No recorded coaching signal standing against this agent.
        </p>
      ) : (
        <>
          <p className="mb-2 text-[0.733rem] text-muted-foreground">
            Recorded feedback that could feed a coaching session. You curate which
            of these a session sees — nothing is fed unchosen.
          </p>
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter signals (kind, intent, feedback)…"
            className="cockpit-input mb-2"
          />
          <div className="space-y-1">
            {filtered.map((s) => (
              <div key={s.event_id} className="border-b border-border/20 py-1.5 text-[0.7rem]">
                <div className="flex items-center gap-2">
                  <span className="cockpit-badge">{s.kind}</span>
                  <span className="tabular-nums text-muted-foreground">event {s.event_id}</span>
                  {s.at && <span className="tabular-nums text-muted-foreground">{fmtDate(s.at)}</span>}
                </div>
                {s.intent && <div className="mt-0.5 text-muted-foreground">on: {s.intent}</div>}
                <div className="mt-0.5 text-foreground">“{s.feedback}”</div>
              </div>
            ))}
          </div>
        </>
      )}
      <div className="pt-2">
        <Button size="sm" variant="outline" onClick={onCoach}>
          Start a coaching session
        </Button>
      </div>
    </div>
  );
}

/* ── The view ── */

type DialogState =
  | null
  | { kind: 'hire' }
  | { kind: 'retire' }
  | { kind: 'grant' }
  | { kind: 'revoke'; pack: string }
  | { kind: 'grantSkill' }
  | { kind: 'revokeSkill'; skillId: string }
  | { kind: 'charter' }
  | { kind: 'coach' }
  | { kind: 'revert'; version: number };

export function AgentsView({ onOpenDecisions, onOpenTask }: {
  onOpenDecisions?: () => void;
  /** Coaching runs on the task board now, so the Coach dialog can hand off to
   *  it — undefined when the board is hidden. */
  onOpenTask?: (taskId: string) => void;
} = {}) {
  const {
    agents, detail, loading, detailLoading, error, refresh, loadDetail,
    hire, retire, reactivate, grant, revoke, grantSkill, revokeSkill,
    saveCharter, revertCharter, coach, getProposal, setModel,
  } = useAgents();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const closeDialog = useCallback(() => setDialog(null), []);

  // Select the first agent once the roster arrives, but never re-select on a
  // later refresh — that would yank the operator out of what they're reading.
  useEffect(() => {
    if (selectedId || agents.length === 0) return;
    setSelectedId(agents[0].id);
  }, [agents, selectedId]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const select = useCallback((id: string) => setSelectedId(id), []);

  return (
    <>
    {/* The list/detail boundary is draggable now, so the side-by-side test can no
        longer be a container query — ListDetailSplit measures it and renders the
        plain stacked column below the breakpoint, exactly as this did before. */}
    <ListDetailSplit
      id="cc-agents"
      aside={<>
        <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
          <Users size={14} className="text-primary" />
          <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Agents</span>
          <span className="cockpit-badge tabular-nums">{agents.length}</span>
          <button
            onClick={() => refresh()}
            title="Refresh"
            aria-label="Refresh roster"
            className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && <p className="px-4 py-2 text-[0.733rem] text-destructive">{error}</p>}
          {agents.length === 0 && !loading && (
            <p className="px-4 py-3 text-[0.733rem] text-muted-foreground">No agents on the roster.</p>
          )}
          {agents.map((a) => (
            <RosterRow key={a.id} a={a} active={a.id === selectedId} onSelect={() => select(a.id)} />
          ))}
        </div>
        <div className="border-t border-border/40 px-4 py-3">
          <Button size="sm" variant="outline" onClick={() => setDialog({ kind: 'hire' })}>
            Hire an agent
          </Button>
        </div>
      </>}
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!detail && detailLoading && (
          <p className="px-4 py-4 text-[0.733rem] text-muted-foreground">Loading…</p>
        )}
        {!detail && !detailLoading && (
          <p className="px-4 py-4 text-[0.733rem] text-muted-foreground">Select an agent.</p>
        )}
        {detail && (
          <div className="pb-8 pt-4">
            <IdentityPanel
              a={detail.agent}
              onEditCharter={() => setDialog({ kind: 'charter' })}
              onSaveModel={(modelId, thinking) => setModel(detail.agent.id, modelId, thinking)}
              onRetire={() => setDialog({ kind: 'retire' })}
              onReactivate={() => reactivate(detail.agent.id)}
              onCoach={() => setDialog({ kind: 'coach' })}
            />
            <SectionLabel icon={<FileText size={11} />}>Charter</SectionLabel>
            <CharterPanel
              charter={detail.charter}
              onRevert={(version) => setDialog({ kind: 'revert', version })}
              getProposal={getProposal}
            />
            <SectionLabel icon={<KeyRound size={11} />} count={detail.grants.filter((g) => !g.revoked_at).length}>
              Capabilities
            </SectionLabel>
            <GrantsPanel
              grants={detail.grants}
              packs={detail.packs}
              onGrant={() => setDialog({ kind: 'grant' })}
              onRevoke={(pack) => setDialog({ kind: 'revoke', pack })}
            />
            <SectionLabel
              icon={<BookOpen size={11} />}
              count={(detail.skills ?? []).filter((s) => !s.revoked_at).length}
            >
              Skills
            </SectionLabel>
            <SkillsPanel
              skills={detail.skills ?? []}
              onGrant={() => setDialog({ kind: 'grantSkill' })}
              onRevoke={(skillId) => setDialog({ kind: 'revokeSkill', skillId })}
            />
            <SectionLabel icon={<Activity size={11} />}>Record</SectionLabel>
            <RecordPanel detail={detail} />
            <SectionLabel icon={<MessageSquareQuote size={11} />} count={detail.signals.length}>
              Coaching signals
            </SectionLabel>
            <SignalsPanel
              signals={detail.signals}
              onCoach={() => setDialog({ kind: 'coach' })}
            />
          </div>
        )}
      </div>
    </ListDetailSplit>

      <HireDialog
        open={dialog?.kind === 'hire'}
        onOpenChange={(v) => !v && closeDialog()}
        onHire={async (body) => { await hire(body); }}
      />
      {detail && (
        <>
          <RetireDialog
            agent={detail.agent}
            open={dialog?.kind === 'retire'}
            onOpenChange={(v) => !v && closeDialog()}
            onRetire={(reason) => retire(detail.agent.id, reason)}
          />
          <GrantDialog
            agent={detail.agent}
            packs={detail.packs}
            grants={detail.grants}
            open={dialog?.kind === 'grant'}
            onOpenChange={(v) => !v && closeDialog()}
            onGrant={(pack) => grant(detail.agent.id, pack)}
          />
          <RevokeDialog
            agent={detail.agent}
            pack={dialog?.kind === 'revoke' ? dialog.pack : ''}
            open={dialog?.kind === 'revoke'}
            onOpenChange={(v) => !v && closeDialog()}
            onRevoke={(reason) =>
              revoke(detail.agent.id, dialog?.kind === 'revoke' ? dialog.pack : '', reason)}
          />
          <SkillGrantDialog
            agent={detail.agent}
            catalog={detail.skill_catalog ?? []}
            skills={detail.skills ?? []}
            open={dialog?.kind === 'grantSkill'}
            onOpenChange={(v) => !v && closeDialog()}
            onGrant={(skillId) => grantSkill(detail.agent.id, skillId)}
          />
          <SkillRevokeDialog
            agent={detail.agent}
            skillId={dialog?.kind === 'revokeSkill' ? dialog.skillId : ''}
            open={dialog?.kind === 'revokeSkill'}
            onOpenChange={(v) => !v && closeDialog()}
            onRevoke={(reason) =>
              revokeSkill(
                detail.agent.id,
                dialog?.kind === 'revokeSkill' ? dialog.skillId : '',
                reason,
              )}
          />
          <CharterEditor
            agent={detail.agent}
            current={detail.charter.current}
            open={dialog?.kind === 'charter'}
            onOpenChange={(v) => !v && closeDialog()}
            onSave={(content, rationale) =>
              saveCharter(detail.agent.id, content, rationale)}
          />
          <CoachDialog
            agent={detail.agent}
            signals={detail.signals}
            open={dialog?.kind === 'coach'}
            onOpenChange={(v) => !v && closeDialog()}
            onCoach={(signalIds, note) => coach(detail.agent.id, signalIds, note)}
            onOpenDecisions={onOpenDecisions}
            onOpenTask={onOpenTask}
          />
          <RevertDialog
            agent={detail.agent}
            version={dialog?.kind === 'revert' ? dialog.version : 0}
            currentVersion={detail.charter.current.version}
            open={dialog?.kind === 'revert'}
            onOpenChange={(v) => !v && closeDialog()}
            onRevert={() => revertCharter(
              detail.agent.id, dialog?.kind === 'revert' ? dialog.version : 0)}
          />
        </>
      )}
    </>
  );
}
