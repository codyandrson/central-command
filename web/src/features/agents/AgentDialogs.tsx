import { useState, useEffect, useMemo } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { InlineSelectOption } from '@/components/ui/InlineSelect';
import { useGateway } from '@/contexts/GatewayContext';
import type {
  AgentRow, CharterVersion, CoachingSignal, Grant, PackInfo,
  SkillCatalogEntry, SkillGrant,
} from './types';

/**
 * The Agents page's write actions.
 *
 * Every one is a DIRECT operator action with an event record (D24/D25), not a
 * proposal: the approval gate governs what AGENTS want to do, the operator
 * governs the team. What these dialogs owe the operator is therefore not a
 * gate but an honest account of what the action does to the record — which
 * things are kept forever, and which are reversible.
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

/* ── Retire / Reactivate ── */

export function RetireDialog({ agent, open, onOpenChange, onRetire }: {
  agent: AgentRow;
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
            Retire {agent.name || agent.id}?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Retiring is a status flip, not a deletion. Its sessions, charter
            versions, capability grants and every event stay keyed to{' '}
            <span className="font-mono">{agent.id}</span> forever, and the id
            stays reserved. It stops being taskable, and you can reactivate it
            later.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">
            Reason (recorded on the retirement event)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why is this agent leaving the team?"
            rows={3}
            className="cockpit-textarea min-h-[88px]"
          />
          <p className="cockpit-note mt-2">
            Required — a retirement with no recorded reason is a gap in the record.
          </p>
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

/* ── Grant a capability pack ── */

export function GrantDialog({ agent, packs, grants, open, onOpenChange, onGrant }: {
  agent: AgentRow;
  packs: PackInfo[];
  grants: Grant[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onGrant: (pack: string) => Promise<void>;
}) {
  const held = useMemo(
    () => new Set(grants.filter((g) => !g.revoked_at).map((g) => g.pack)),
    [grants],
  );
  const available = useMemo(
    () => packs.filter((p) => !held.has(p.name)),
    [packs, held],
  );
  const [pack, setPack] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setPack(available[0]?.name ?? ''); }, [open, available]);

  const chosen = available.find((p) => p.name === pack);
  const options: InlineSelectOption[] = available.map((p) => ({
    value: p.name, label: p.name,
  }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Grant a capability pack
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            A pack is a named bundle of tools plus the gated capabilities they
            let {agent.name || agent.id} <em>propose</em> — never perform.
            Its charter's capability list is generated from its grants at run
            start, so this takes effect on its next run with no deploy.
          </DialogDescription>
        </DialogHeader>

        {available.length === 0 ? (
          <p className="cockpit-note">
            This agent already holds every pack in the catalog.
          </p>
        ) : (
          <>
            <div>
              <label className="cockpit-field-label mb-2 block">Pack</label>
              {/* `inline` is REQUIRED inside a Radix Dialog: without it the
                  listbox renders through a sibling portal whose clicks the
                  dialog intercepts, so the menu opens and looks fine but no
                  option can ever be chosen. */}
              <InlineSelect
                value={pack}
                onChange={setPack}
                options={options}
                ariaLabel="Capability pack to grant"
                inline
              />
            </div>
            {chosen && (
              <div className="rounded-lg border border-border/50 bg-muted/20 p-3 text-[0.733rem]">
                <p className="text-muted-foreground">{chosen.description}</p>
                <p className="mt-2">
                  <span className="text-muted-foreground">tools: </span>
                  {chosen.tools.length ? chosen.tools.join(', ') : 'none'}
                </p>
                <p className="mt-1">
                  <span className="text-muted-foreground">can propose: </span>
                  {chosen.capabilities.length
                    ? chosen.capabilities.join(', ')
                    : 'nothing — read-only pack'}
                </p>
              </div>
            )}
          </>
        )}
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !pack}
            onClick={() => run(() => onGrant(pack), () => onOpenChange(false))}
          >
            {busy ? 'Granting…' : 'Grant'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Grant / revoke a SKILL ── */

/**
 * Skills are the other half of the capability surface: packs are what an agent
 * can DO, skills are what it KNOWS.
 *
 * The picker offers ACTIVE skills the agent does not already hold. Retired ones
 * are excluded at the source — the detail payload's `skill_catalog` is
 * ACTIVE-only, because `grant_skill_route` refuses a retired skill with a 409
 * and an option that can only fail is not an option.
 */
export function SkillGrantDialog({
  agent, catalog, skills, open, onOpenChange, onGrant,
}: {
  agent: AgentRow;
  catalog: SkillCatalogEntry[];
  skills: SkillGrant[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onGrant: (skillId: string) => Promise<void>;
}) {
  const held = useMemo(
    () => new Set(skills.filter((s) => !s.revoked_at).map((s) => s.skill_id)),
    [skills],
  );
  const available = useMemo(
    () => catalog.filter((s) => s.status === 'ACTIVE' && !held.has(s.id)),
    [catalog, held],
  );
  const [skillId, setSkillId] = useState('');
  const { busy, error, run } = useAsyncAction();
  useEffect(() => { if (open) setSkillId(available[0]?.id ?? ''); }, [open, available]);

  const chosen = available.find((s) => s.id === skillId);
  const options: InlineSelectOption[] = available.map((s) => ({
    value: s.id, label: s.id,
  }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Grant a skill
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            A skill is curated subject knowledge. {agent.name || agent.id} sees a
            one-line catalog entry for it and loads the guidance only when it
            decides the subject is relevant, so granting one costs almost nothing
            until it is used. Takes effect on the agent's next run.
          </DialogDescription>
        </DialogHeader>

        {available.length === 0 ? (
          <p className="cockpit-note">
            No skill left to grant — this agent already holds every active skill
            in the library.
          </p>
        ) : (
          <>
            <div>
              <label className="cockpit-field-label mb-2 block">Skill</label>
              {/* `inline` is REQUIRED inside a Radix Dialog — see GrantDialog. */}
              <InlineSelect
                value={skillId}
                onChange={setSkillId}
                options={options}
                ariaLabel="Skill to grant"
                inline
              />
            </div>
            {chosen && (
              <div className="rounded-lg border border-border/50 bg-muted/20 p-3 text-[0.733rem]">
                <p className="font-semibold text-foreground">{chosen.title}</p>
                <p className="mt-1 text-muted-foreground">{chosen.summary}</p>
                <p className="mt-2 text-muted-foreground">
                  {chosen.reference_count} searchable reference document
                  {chosen.reference_count === 1 ? '' : 's'} ·{' '}
                  {chosen.holder_count} agent{chosen.holder_count === 1 ? '' : 's'} hold
                  {chosen.holder_count === 1 ? 's' : ''} this
                </p>
              </div>
            )}
          </>
        )}
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !skillId}
            onClick={() => run(() => onGrant(skillId), () => onOpenChange(false))}
          >
            {busy ? 'Granting…' : 'Grant'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function SkillRevokeDialog({ skillId, agent, open, onOpenChange, onRevoke }: {
  skillId: string;
  agent: AgentRow;
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
            Revoke <span className="font-mono">{skillId}</span>?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            {agent.name || agent.id} stops receiving this subject on its next
            run. Revocation is a timestamp, never a delete — the grant stays on
            the record with its reason, which is what stops an idempotent schema
            seed from quietly handing the skill back.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">Reason</label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why is this knowledge being taken away?"
            className="cockpit-input"
          />
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy}
            onClick={() => run(() => onRevoke(reason.trim()), () => onOpenChange(false))}
          >
            {busy ? 'Revoking…' : 'Revoke'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Revoke ── */

export function RevokeDialog({ pack, agent, open, onOpenChange, onRevoke }: {
  pack: string;
  agent: AgentRow;
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
            Revoke <span className="font-mono">{pack}</span>?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            {agent.name || agent.id} loses these tools on its next run.
            Revocation is a timestamp, never a delete — the grant stays on the
            record with its reason, which is what stops an idempotent schema
            seed from quietly handing the pack back.
          </DialogDescription>
        </DialogHeader>
        <div>
          <label className="cockpit-field-label mb-2 block">Reason</label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why is this capability being taken away?"
            className="cockpit-input"
          />
        </div>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy}
            onClick={() => run(() => onRevoke(reason.trim()), () => onOpenChange(false))}
          >
            {busy ? 'Revoking…' : 'Revoke'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Charter editing ── */

export function CharterEditor({ agent, current, open, onOpenChange, onSave }: {
  agent: AgentRow;
  current: CharterVersion;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSave: (content: string, rationale: string) => Promise<{ version: number; warnings?: unknown[] }>;
}) {
  const [content, setContent] = useState(current.content);
  const [rationale, setRationale] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saved, setSaved] = useState<number | null>(null);
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setContent(current.content);
    setRationale('');
    setWarnings([]);
    setSaved(null);
  }, [open, current.content]);

  const unchanged = content.trim() === current.content.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Edit {agent.name || agent.id}'s charter
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Saving creates version {current.version + 1}. History is append-only
            — v{current.version} is not overwritten and stays revertible. A
            resumed run keeps the charter it was proposed under, so this takes
            effect on the next run, never on a pending proposal.
          </DialogDescription>
        </DialogHeader>

        {saved !== null ? (
          // Reached ONLY when the save produced policy warnings. A clean save
          // closes the dialog outright — an extra click to dismiss "it worked"
          // is friction, and it trains you to dismiss this panel unread, which
          // is precisely when it matters.
          <div className="space-y-3">
            <p className="cockpit-note" data-tone="info">Saved as v{saved}.</p>
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
              <p className="text-[0.8rem] font-semibold text-foreground">
                Policy warnings — saved, but read these
              </p>
              <p className="mt-1 text-[0.733rem] text-muted-foreground">
                This charter names a capability the registry does not have. An
                agent coached to use one cannot: it produces a proposal that
                fails at execution, which is exactly the charter-v2 incident.
              </p>
              <ul className="mt-2 space-y-1 text-[0.733rem] text-foreground">
                {warnings.map((w, i) => <li key={i}>• {w}</li>)}
              </ul>
            </div>
          </div>
        ) : (
          <>
            <div>
              <label className="cockpit-field-label mb-2 block">Charter</label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={18}
                spellCheck={false}
                className="cockpit-textarea min-h-[24rem] font-mono text-[0.7rem] leading-relaxed"
              />
            </div>
            <div>
              <label className="cockpit-field-label mb-2 block">
                Rationale (recorded with the version)
              </label>
              <input
                type="text"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Why is this changing?"
                className="cockpit-input"
              />
              <p className="cockpit-note mt-2">
                Required here. A charter version whose reason is not on the
                record cannot be judged later — by you or by the agent it
                governs.
              </p>
            </div>
          </>
        )}

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          {saved !== null ? (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
                Cancel
              </Button>
              <Button
                disabled={busy || !content.trim() || !rationale.trim() || unchanged}
                title={unchanged ? 'No changes to save' : undefined}
                onClick={() => run(async () => {
                  const res = await onSave(content, rationale.trim());
                  const flags = (res.warnings ?? []).map((w) =>
                    typeof w === 'string' ? w : JSON.stringify(w));
                  if (flags.length === 0) {
                    onOpenChange(false);  // clean save: nothing to read, get out of the way
                    return;
                  }
                  setWarnings(flags);
                  setSaved(res.version);
                })}
              >
                {busy ? 'Saving…' : `Save as v${current.version + 1}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Revert ── */

export function RevertDialog({ agent, version, currentVersion, open, onOpenChange, onRevert }: {
  agent: AgentRow;
  version: number;
  currentVersion: number;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onRevert: () => Promise<{ version: number; restored: number }>;
}) {
  const { busy, error, run } = useAsyncAction();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Revert to v{version}?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            This does not roll history back. It copies v{version}'s content
            forward as a NEW version {currentVersion + 1}, so the revert itself
            is on the record — you will be able to see that it happened, and
            when. {agent.name || agent.id} runs on it from its next run.
          </DialogDescription>
        </DialogHeader>
        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy}
            onClick={() => run(() => onRevert(), () => onOpenChange(false))}
          >
            {busy ? 'Reverting…' : `Revert as v${currentVersion + 1}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Coach (D5) ── */

/** What starting a coaching session returns. NOT the run's outcome: a coach
 *  run takes minutes, so the backend launches it behind a task record and
 *  returns as soon as it is running. The outcome lands on the task (Review
 *  with a proposal, or Done with a text answer), never back on this dialog. */
type CoachResult = { task_id: string; target?: string };

/**
 * The coaching loop's operator surface.
 *
 * Curation IS the job here, and it decides the outcome — the 2026-07-25
 * sessions turned on which signals were fed, not on the model. So nothing is
 * selected by default: a select-all default would make the operator's most
 * consequential choice the one they never made.
 *
 * This dialog starts a real agent run and ends in a PROPOSAL, never a change.
 */
export function CoachDialog({
  agent, signals, open, onOpenChange, onCoach, onOpenDecisions, onOpenTask,
}: {
  agent: AgentRow;
  signals: CoachingSignal[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCoach: (signalIds: number[], note: string) => Promise<CoachResult>;
  onOpenDecisions?: () => void;
  onOpenTask?: (taskId: string) => void;
}) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [note, setNote] = useState('');
  const [result, setResult] = useState<CoachResult | null>(null);
  const [filter, setFilter] = useState('');
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setSelected(new Set());
    setNote('');
    setResult(null);
    setFilter('');
  }, [open]);

  const filteredSignals = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return signals;
    return signals.filter((s) =>
      s.kind.toLowerCase().includes(q) ||
      (s.intent ?? '').toLowerCase().includes(q) ||
      s.feedback.toLowerCase().includes(q)
    );
  }, [signals, filter]);

  const toggle = (id: number) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of signals) {
      if (selected.has(s.event_id)) counts.set(s.kind, (counts.get(s.kind) ?? 0) + 1);
    }
    return counts;
  }, [signals, selected]);

  const canRun = selected.size > 0 || note.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Coach {agent.name || agent.id}
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            You choose what this session sees; the coach reads it and drafts an
            edit to {agent.name || agent.id}'s charter; you review the result
            as a diff in the Decisions Inbox. Nothing changes the charter
            without your approval. Your note is written to the log before the
            run, so the coach can cite it and the citation can be checked.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <p className="cockpit-note" data-tone="info">
              Coaching session started —{' '}
              <span className="font-mono">{result.task_id}</span>
            </p>
            <p className="text-[0.733rem] text-muted-foreground">
              The coach is reading the signals you selected and drafting an edit
              to {agent.name || agent.id}'s charter. It runs on the task board,
              not in this dialog, and takes a few minutes — you can close this
              and carry on.
            </p>
            <p className="text-[0.733rem] text-muted-foreground">
              The task moves to <strong>Review</strong> when the coach proposes
              an edit — the diff waits in the Decisions Inbox, and approving it
              commits a new version recording the coach as drafter,{' '}
              {agent.name || agent.id} as subject, and your approval on it. It
              moves to <strong>Done</strong> if the coach answers in text
              instead — a legitimate outcome, and its reasoning is worth reading
              before re-running with a different selection.
            </p>
          </div>
        ) : (
          <>
            <div>
              <div className="mb-2 flex items-center gap-2">
                <label className="cockpit-field-label">
                  Recorded signals ({selected.size} of {signals.length} selected)
                </label>
                {signals.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    className="ml-auto text-[0.667rem] text-muted-foreground hover:text-foreground"
                  >
                    Clear
                  </button>
                )}
              </div>
              {signals.length === 0 ? (
                <p className="cockpit-note">
                  No recorded signal for this agent. You can still coach it with
                  a note of your own below.
                </p>
              ) : (
                <>
                  <input
                    type="text"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Filter signals (kind, intent, feedback)…"
                    className="cockpit-input mb-2"
                  />
                  <div className="max-h-[16rem] space-y-1 overflow-y-auto rounded-lg border border-border/40 p-2">
                    {filteredSignals.map((s) => (
                      <label
                        key={s.event_id}
                        className="flex cursor-pointer gap-2 rounded-md p-1.5 text-[0.7rem] hover:bg-muted/30"
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(s.event_id)}
                          onChange={() => toggle(s.event_id)}
                          className="mt-0.5 shrink-0"
                        />
                        <span className="min-w-0">
                          <span className="flex items-center gap-2">
                            <span className="cockpit-badge">{s.kind}</span>
                            <span className="tabular-nums text-muted-foreground">
                              event {s.event_id}
                            </span>
                            {s.at && (
                              <span className="text-muted-foreground">
                                {new Date(s.at).toLocaleString()}
                              </span>
                            )}
                            {s.author === 'agent' && (
                              <span className="text-muted-foreground">
                                the agent's own words
                              </span>
                            )}
                          </span>
                          {s.intent && (
                            <span className="mt-0.5 block text-muted-foreground">on: {s.intent}</span>
                          )}
                          <span className="mt-0.5 block text-foreground">“{s.feedback}”</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </>
              )}
              {kinds.size === 1 && selected.size > 1 && (
                <p className="cockpit-note mt-2" data-tone="info">
                  Every signal you have selected is the same kind. A one-sided
                  selection coaches a one-sided rule — the charter-v6 session
                  worked because it fed both the over-correction and the
                  under-correction together.
                </p>
              )}
            </div>

            <div>
              <label className="cockpit-field-label mb-2 block">
                Your own feedback (optional)
              </label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Say it in your own words. Recorded on the log before the run, so the agent can cite it."
                rows={3}
                className="cockpit-textarea min-h-[88px]"
              />
            </div>
          </>
        )}

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          {result ? (
            <>
              {onOpenTask && (
                <Button
                  variant="outline"
                  onClick={() => { onOpenChange(false); onOpenTask(result.task_id); }}
                >
                  Open on the board
                </Button>
              )}
              {onOpenDecisions && (
                <Button
                  variant="outline"
                  onClick={() => { onOpenChange(false); onOpenDecisions(); }}
                >
                  Open Decisions Inbox
                </Button>
              )}
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
                Cancel
              </Button>
              <Button
                disabled={busy || !canRun}
                title={canRun ? undefined : 'Select at least one signal, or write a note'}
                onClick={() => run(async () => {
                  setResult(await onCoach([...selected], note.trim()));
                })}
              >
                {busy ? 'Starting…' : 'Start coaching session'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Hire ── */

interface Template { id: string; label: string; description: string; packs: string[] }

export function HireDialog({ open, onOpenChange, onHire }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onHire: (body: { name: string; mission: string; template: string }) => Promise<unknown>;
}) {
  const { rpc } = useGateway();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [template, setTemplate] = useState('general');
  const [name, setName] = useState('');
  const [mission, setMission] = useState('');
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) return;
    setName(''); setMission(''); setTemplate('general');
    rpc('agents.templates')
      .then((r) => setTemplates((r as { templates: Template[] }).templates ?? []))
      .catch(() => setTemplates([]));
  }, [open, rpc]);

  const chosen = templates.find((t) => t.id === template);
  const options: InlineSelectOption[] = templates.map((t) => ({
    value: t.id, label: t.label,
  }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Hire an agent
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            One transaction: the roster row, its charter v1, and its capability
            grants land together or not at all. A template is a starting
            loadout, never an agent type — you can grant and revoke from here
            afterwards, and coach its charter from day one.
          </DialogDescription>
        </DialogHeader>

        <div>
          <label className="cockpit-field-label mb-2 block">Template</label>
          {/* `inline` — see the note in GrantDialog. Without it the dialog
              swallows the option click and the value never changes. */}
          <InlineSelect
            value={template}
            onChange={setTemplate}
            options={options.length ? options : [{ value: 'general', label: 'Loading…' }]}
            ariaLabel="Starting capability loadout"
            inline
          />
          {chosen && (
            <div className="mt-2 rounded-lg border border-border/50 bg-muted/20 p-3 text-[0.733rem]">
              <p className="text-muted-foreground">{chosen.description}</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {chosen.packs.map((p) => (
                  <span key={p} className="cockpit-badge" data-tone="info">{p}</span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div>
          <label className="cockpit-field-label mb-2 block">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Release Manager"
            className="cockpit-input"
          />
        </div>

        <div>
          <label className="cockpit-field-label mb-2 block">Mission</label>
          <textarea
            value={mission}
            onChange={(e) => setMission(e.target.value)}
            placeholder="What does this agent exist to do? This becomes its role on the roster and the mission in its charter."
            rows={3}
            className="cockpit-textarea min-h-[96px]"
          />
        </div>

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={busy || !name.trim() || !mission.trim()}
            onClick={() => run(
              () => onHire({ name: name.trim(), mission: mission.trim(), template }),
              () => onOpenChange(false),
            )}
          >
            {busy ? 'Hiring…' : 'Hire'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
