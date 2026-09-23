import { useCallback, useEffect, useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

/**
 * Mail rules section of Settings (standing "auto-dismiss mail matching these
 * criteria" rules). Agents PROPOSE a rule through the Decisions Inbox
 * (`mail.create_rule`); the operator can also create one directly here.
 * Matching mail folds at claim time without an agent run. Server-backed via
 * `/api/cc/mail-rules*`, same envelope/api() helper as ContextSettings.
 */
export interface MailRuleCriteria {
  from_address?: string;
  from_domain?: string;
  subject_contains?: string;
  body_contains?: string;
}

export interface MailRule {
  id: string;
  position: number;
  action: 'dismiss';
  criteria: MailRuleCriteria;
  exceptions: MailRuleCriteria;
  description: string;
  reason: string;
  created_by: string;
  created_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
  matched_count: number;
  last_matched_at: string | null;
  reopened_count: number;
}

interface PreviewResult {
  queued_matches: number;
  samples: Array<{ from: string; subject: string; date: string }>;
  description: string;
}

const CRITERIA_FIELDS: Array<{ key: keyof MailRuleCriteria; label: string; placeholder: string }> = [
  { key: 'from_address', label: 'From address', placeholder: 'someone@example.com' },
  { key: 'from_domain', label: 'From domain', placeholder: 'example.com' },
  { key: 'subject_contains', label: 'Subject contains', placeholder: 'newsletter' },
  { key: 'body_contains', label: 'Body contains', placeholder: 'unsubscribe' },
];

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json() as { ok: boolean; result?: T; error?: string };
  if (!res.ok || !body.ok || body.result === undefined) throw new Error(body.error || `HTTP ${res.status}`);
  return body.result;
}

function emptyCriteria(): MailRuleCriteria {
  return { from_address: '', from_domain: '', subject_contains: '', body_contains: '' };
}

function trimmed(c: MailRuleCriteria): MailRuleCriteria {
  const out: MailRuleCriteria = {};
  for (const k of Object.keys(c) as Array<keyof MailRuleCriteria>) {
    const v = c[k]?.trim();
    if (v) out[k] = v;
  }
  return out;
}

function CriteriaFields({
  values, onChange, idPrefix,
}: {
  values: MailRuleCriteria;
  onChange: (key: keyof MailRuleCriteria, value: string) => void;
  idPrefix: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {CRITERIA_FIELDS.map((f) => (
        <div key={f.key} className="space-y-1">
          <label htmlFor={`${idPrefix}-${f.key}`} className="text-[0.7rem] text-muted-foreground">{f.label}</label>
          <Input
            id={`${idPrefix}-${f.key}`}
            value={values[f.key] ?? ''}
            placeholder={f.placeholder}
            onChange={(e) => onChange(f.key, e.target.value)}
          />
        </div>
      ))}
    </div>
  );
}

function RuleRow({ rule, onRevoke }: { rule: MailRule; onRevoke: (id: string, reason: string) => Promise<void> }) {
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const isRevoked = rule.revoked_at != null;

  return (
    <div className={`space-y-1.5 rounded-lg border border-border/40 p-3 ${isRevoked ? 'opacity-55' : ''}`}>
      <p className="text-[0.75rem] text-foreground">{rule.description}</p>
      <p className="text-[0.667rem] text-muted-foreground">
        {rule.reason} — by {rule.created_by} on {new Date(rule.created_at).toLocaleString()}
      </p>
      <p className="text-[0.667rem] text-muted-foreground">
        matched {rule.matched_count} · reopened {rule.reopened_count}
        {rule.last_matched_at && ` · last matched ${new Date(rule.last_matched_at).toLocaleString()}`}
      </p>
      {isRevoked && (
        <p className="text-[0.667rem] text-destructive">
          revoked {rule.revoked_at && new Date(rule.revoked_at).toLocaleString()}
          {rule.revoked_reason ? `: ${rule.revoked_reason}` : ''}
        </p>
      )}
      {!isRevoked && (
        revoking ? (
          <div className="flex items-center gap-2">
            <Input
              value={reason}
              placeholder="Reason (optional)"
              onChange={(e) => setReason(e.target.value)}
              className="h-8 text-[0.7rem]"
            />
            <Button size="sm" variant="destructive" disabled={busy} onClick={async () => {
              setBusy(true);
              try { await onRevoke(rule.id, reason); } finally { setBusy(false); setRevoking(false); }
            }}>
              {busy ? 'Revoking…' : 'Confirm'}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setRevoking(false); setReason(''); }}>
              Cancel
            </Button>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setRevoking(true)}>
            Revoke
          </Button>
        )
      )}
    </div>
  );
}

export function MailRulesSettings() {
  const [rules, setRules] = useState<MailRule[] | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [criteria, setCriteria] = useState<MailRuleCriteria>(emptyCriteria());
  const [exceptions, setExceptions] = useState<MailRuleCriteria>(emptyCriteria());
  const [reason, setReason] = useState('');
  const [applyToQueued, setApplyToQueued] = useState(true);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback((includeRevoked: boolean) => {
    api<{ rules: MailRule[] }>(`/api/cc/mail-rules?include_revoked=${includeRevoked}`)
      .then((r) => { setRules(r.rules); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => { load(showRevoked); }, [load, showRevoked]);

  const hasCriteria = Object.values(trimmed(criteria)).length > 0;

  const setCriteriaField = (key: keyof MailRuleCriteria, value: string) => {
    setCriteria((c) => ({ ...c, [key]: value }));
    setPreview(null);
  };
  const setExceptionField = (key: keyof MailRuleCriteria, value: string) => {
    setExceptions((c) => ({ ...c, [key]: value }));
    setPreview(null);
  };

  const handlePreview = async () => {
    if (!hasCriteria) { setFormError('Enter at least one criterion.'); return; }
    setPreviewing(true);
    setFormError(null);
    try {
      const result = await api<PreviewResult>('/api/cc/mail-rules/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ criteria: trimmed(criteria), exceptions: trimmed(exceptions) }),
      });
      setPreview(result);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
      setPreview(null);
    } finally {
      setPreviewing(false);
    }
  };

  const handleCreate = async () => {
    if (!hasCriteria || !preview || !reason.trim()) return;
    setCreating(true);
    setFormError(null);
    try {
      await api('/api/cc/mail-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          criteria: trimmed(criteria),
          exceptions: trimmed(exceptions),
          reason: reason.trim(),
          apply_to_queued: applyToQueued,
        }),
      });
      setCriteria(emptyCriteria());
      setExceptions(emptyCriteria());
      setReason('');
      setApplyToQueued(true);
      setPreview(null);
      load(showRevoked);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id: string, revokeReason: string) => {
    try {
      await api(`/api/cc/mail-rules/${encodeURIComponent(id)}/revoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: revokeReason.trim() || undefined }),
      });
      load(showRevoked);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const active = (rules ?? []).filter((r) => r.revoked_at == null);
  const revoked = (rules ?? []).filter((r) => r.revoked_at != null);

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-[0.8rem] font-semibold text-foreground">Mail rules</h3>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          Standing rules that auto-dismiss matching mail at claim time, without an agent run.
          Agents can propose one through the Decisions Inbox; you can also create one directly here.
        </p>
      </div>

      <div className="flex items-center justify-between gap-3">
        <label htmlFor="mail-rules-show-revoked" className="text-[0.75rem] text-foreground">Show revoked</label>
        <Switch id="mail-rules-show-revoked" checked={showRevoked} onCheckedChange={setShowRevoked} />
      </div>

      {error && <p className="text-[0.7rem] text-destructive">{error}</p>}

      <div className="space-y-2">
        {rules === null && !error && <p className="text-[0.7rem] text-muted-foreground">Loading…</p>}
        {rules !== null && active.length === 0 && <p className="text-[0.7rem] text-muted-foreground">No active rules.</p>}
        {active.map((r) => <RuleRow key={r.id} rule={r} onRevoke={handleRevoke} />)}
        {showRevoked && revoked.map((r) => <RuleRow key={r.id} rule={r} onRevoke={handleRevoke} />)}
      </div>

      <div className="space-y-3 rounded-lg border border-border/40 p-3">
        <h4 className="text-[0.75rem] font-semibold text-foreground">New rule</h4>

        <div className="space-y-1.5">
          <p className="text-[0.667rem] font-semibold uppercase tracking-wide text-muted-foreground">Criteria</p>
          <CriteriaFields values={criteria} onChange={setCriteriaField} idPrefix="rule-criteria" />
        </div>

        <div className="space-y-1.5">
          <p className="text-[0.667rem] font-semibold uppercase tracking-wide text-muted-foreground">Exceptions</p>
          <CriteriaFields values={exceptions} onChange={setExceptionField} idPrefix="rule-exception" />
        </div>

        <div className="space-y-1">
          <label htmlFor="rule-reason" className="text-[0.75rem] text-foreground">Reason</label>
          <Input id="rule-reason" value={reason} onChange={(e) => { setReason(e.target.value); }} placeholder="Why this rule exists" />
        </div>

        <div className="flex items-center justify-between gap-3">
          <label htmlFor="rule-apply-to-queued" className="text-[0.75rem] text-foreground">
            Also dismiss the {preview ? preview.queued_matches : 'N'} queued matches now
          </label>
          <Switch id="rule-apply-to-queued" checked={applyToQueued} onCheckedChange={setApplyToQueued} />
        </div>

        {formError && <p className="text-[0.7rem] text-destructive">{formError}</p>}

        {preview && (
          <div className="space-y-1.5 rounded-lg bg-muted/40 p-2.5">
            <p className="text-[0.7rem] text-foreground">{preview.description}</p>
            <p className="text-[0.667rem] text-muted-foreground">queued matches: {preview.queued_matches}</p>
            {preview.samples.length > 0 && (
              <ul className="space-y-1">
                {preview.samples.map((s, i) => (
                  <li key={i} className="text-[0.667rem] text-muted-foreground">
                    {s.from} — {s.subject} ({s.date})
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={!hasCriteria || previewing} onClick={handlePreview}>
            {previewing ? 'Previewing…' : 'Preview'}
          </Button>
          <Button size="sm" disabled={!preview || !reason.trim() || creating} onClick={handleCreate}>
            {creating ? 'Creating…' : 'Create'}
          </Button>
        </div>
      </div>
    </div>
  );
}
