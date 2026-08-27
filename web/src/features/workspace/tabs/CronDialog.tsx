/**
 * CronDialog — Modal for creating or editing cron jobs.
 */

import { useState, useCallback, useRef, useEffect, useMemo, type SelectHTMLAttributes } from 'react';
import { ChevronDown, Cpu, Gauge, X } from 'lucide-react';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { CronJob, CronActionSpec } from '../hooks/useCrons';
import { useSessionContext } from '@/contexts/SessionContext';
import { useGatewayModelCatalog } from '@/hooks/useGatewayModelCatalog';

interface CronDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (job: Record<string, unknown>) => Promise<boolean>;
  mode: 'create' | 'edit';
  /** Pre-fill form when editing */
  initialData?: CronJob | null;
  /** Central Command heartbeat action registry — built-in actions the dialog can author. */
  actions?: Record<string, CronActionSpec>;
}

type ScheduleKind = 'cron' | 'every' | 'at';
type DeliveryMode = 'none' | 'announce';

/** Friendly labels for the built-in heartbeat actions; unknown kinds fall back to the kind itself. */
const ACTION_LABELS: Record<string, string> = {
  'feed.poll': 'Mail poll (built-in)',
  'dispatch.window': 'Dispatcher drain window (built-in)',
  'report.audit_agreement': 'Auditor agreement report (built-in)',
};

const INTERVAL_PRESETS = [
  { value: '300000', label: '5 minutes' },
  { value: '900000', label: '15 minutes' },
  { value: '1800000', label: '30 minutes' },
  { value: '3600000', label: '1 hour' },
  { value: '7200000', label: '2 hours' },
  { value: '21600000', label: '6 hours' },
  { value: '43200000', label: '12 hours' },
  { value: '86400000', label: '24 hours' },
];

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: 'WhatsApp',
  telegram: 'Telegram',
  discord: 'Discord',
  signal: 'Signal',
  slack: 'Slack',
  irc: 'IRC',
  googlechat: 'Google Chat',
  imessage: 'iMessage',
};

const CHANNEL_PLACEHOLDERS: Record<string, string> = {
  whatsapp: '+905551234567',
  telegram: '-100123456789 or @username',
  discord: 'channel-id',
  signal: '+905551234567',
  slack: '#channel or @user',
  irc: '#channel',
  googlechat: 'space-id',
  imessage: '+905551234567',
};

const MAIN_ROOT_SESSION_KEY = 'agent:main:main';

/** Strip the auto-appended delivery instruction from a prompt for clean editing */
function stripDeliveryInstruction(msg: string): string {
  return msg.replace(/\n\n(?:After completing the task, s|S)end the result using the message tool.*$/s, '');
}

function isoToLocal(iso: string): string {
  try {
    const d = new Date(iso);
    // datetime-local expects YYYY-MM-DDTHH:MM
    const pad = (n: number) => n.toString().padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return '';
  }
}

function SectionShell({
  eyebrow,
  title,
  description,
  children,
  className = '',
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`cockpit-surface p-3.5 ${className}`}>
      <div className="space-y-0.5">
        <div className="cockpit-kicker text-[0.6rem]">
          <span className="text-primary">◆</span>
          {eyebrow}
        </div>
        <div className="text-base font-semibold tracking-[-0.03em] text-foreground">{title}</div>
        <p className="text-[0.833rem] leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="mt-3 space-y-2.5">{children}</div>
    </section>
  );
}

function CronSelect(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = '', children, ...rest } = props;

  return (
    <div className="relative">
      <select
        {...rest}
        className={`cockpit-select h-11 appearance-none pr-12 text-sm ${className}`.trim()}
      >
        {children}
      </select>
      <ChevronDown
        size={15}
        aria-hidden="true"
        className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}

/** Modal dialog for creating or editing a cron job (schedule, prompt, model, channel). */
export function CronDialog({ open, onClose, onSubmit, mode, initialData, actions = {} }: CronDialogProps) {
  const prefill = mode === 'edit' && initialData ? initialData : null;
  const { agentName } = useSessionContext();

  const [name, setName] = useState(() => prefill?.name || '');
  const [scheduleKind, setScheduleKind] = useState<ScheduleKind>(() => prefill?.scheduleKind || 'every');
  const [cronExpr, setCronExpr] = useState(() => prefill?.schedule || '0 9 * * *');
  const [cronTz, setCronTz] = useState(() => prefill?.scheduleTz || '');
  const [everyMs, setEveryMs] = useState(() => prefill?.everyMs?.toString() || '3600000');
  const [atTime, setAtTime] = useState(() => prefill?.at ? isoToLocal(prefill.at) : '');
  // 'agentTurn' (a scheduled assigned Task) or a built-in heartbeat action kind.
  const [executionKind, setExecutionKind] = useState<string>(() =>
    prefill
      ? (prefill.payloadKind === 'agentTurn' ? 'agentTurn' : prefill.actionKind || 'agentTurn')
      : 'agentTurn');
  const [actionParams, setActionParams] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(prefill?.actionParams ?? {})) {
      if (v !== undefined && v !== null) out[k] = String(v);
    }
    return out;
  });
  const [message, setMessage] = useState(() => prefill ? stripDeliveryInstruction(prefill.message || '') : '');
  const [model, setModel] = useState(() => prefill?.model || '');
  const [thinking, setThinking] = useState(() => prefill?.thinking || '');
  const [deliveryMode, setDeliveryMode] = useState<DeliveryMode>(() => prefill?.delivery?.mode === 'announce' ? 'announce' : 'none');
  const [deliveryChannel, setDeliveryChannel] = useState(() => prefill?.delivery?.channel || '');
  const [deliveryTo, setDeliveryTo] = useState(() => prefill?.delivery?.to || '');
  const { models: catalogModels } = useGatewayModelCatalog();
  const primaryModel = useMemo(
    () => catalogModels?.find((m) => m.role === 'primary'),
    [catalogModels],
  );
  const models = useMemo(() => [
    {
      value: '',
      label: primaryModel
        ? `Default (${primaryModel.label || primaryModel.id})`
        : 'Default model',
    },
    ...(catalogModels ?? []).map((m) => ({ value: m.id, label: m.label || m.id.split('/').pop() || m.id })),
  ], [catalogModels, primaryModel]);
  // Thinking levels follow the EFFECTIVE model — "Default" resolves to the
  // gateway's primary model, the same sentinel rule as the chat header and
  // the agents panel, so a default-model schedule can still pin an effort.
  const thinkingLevels = useMemo(() => {
    const effective = model ? catalogModels?.find((m) => m.id === model) : primaryModel;
    return effective?.thinkingLevels ?? [];
  }, [catalogModels, model, primaryModel]);
  const thinkingOptions = useMemo(() => [
    { value: '', label: 'Default effort' },
    ...thinkingLevels.map((lvl) => ({ value: lvl, label: lvl })),
  ], [thinkingLevels]);
  // The model changed under an already-chosen effort it no longer supports —
  // clear it rather than silently submit an override the new model can't honor.
  useEffect(() => {
    if (thinking && !thinkingLevels.includes(thinking)) setThinking('');
  }, [thinkingLevels, thinking]);
  // Central Command (D27): an agentTurn cron is a scheduled assigned Task — the
  // assignee comes from the roster's taskable ACTIVE agents.
  const [agentId, setAgentId] = useState(() => prefill?.agentId || '');
  const [taskableAgents, setTaskableAgents] = useState<{ id: string; name: string }[]>([]);
  const [availableChannels, setAvailableChannels] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const effectiveTargetRootSessionKey = MAIN_ROOT_SESSION_KEY;

  const isAgentTurn = executionKind === 'agentTurn';
  const actionSpec: CronActionSpec | undefined = isAgentTurn ? undefined : actions[executionKind];
  // task.create is the agent-task path, never a raw action choice here.
  const builtinKinds = useMemo(
    () => Object.keys(actions).filter(k => k !== 'task.create').sort(),
    [actions],
  );
  // Editing never morphs a schedule across families: a scheduled agent task
  // stays one, a built-in action stays a built-in (kind switching among
  // built-ins is fine — the registry validates the result).
  const editingBuiltin = Boolean(prefill && prefill.payloadKind !== 'agentTurn');

  // Fetch configured channels and taskable agents when dialog opens. The
  // model catalog comes from useGatewayModelCatalog above (fetched once on
  // this component's own mount — the parent remounts it per open via `key`).
  useEffect(() => {
    if (!open) return;
    fetch('/api/channels')
      .then(r => r.json())
      .then((data: { channels?: string[] }) => {
        const ch = data.channels || [];
        setAvailableChannels(ch);
      })
      .catch(() => setAvailableChannels([]));
    fetch('/api/crons/agents')
      .then(r => r.json())
      .then((data: { ok?: boolean; result?: { agents?: { id: string; name: string }[] } }) => {
        setTaskableAgents(data.result?.agents || []);
      })
      .catch(() => setTaskableAgents([]));
  }, [open]);

  // Form state is initialized from props via useState initializers above.
  // Parent uses a `key` prop to force remount when mode/job changes.

  useEffect(() => {
    if (open) {
      dialogRef.current?.showModal();
    } else {
      dialogRef.current?.close();
    }
  }, [open]);

  const handleClose = useCallback(() => {
    setError('');
    onClose();
  }, [onClose]);

  const handleDialogClick = useCallback((e: React.MouseEvent<HTMLDialogElement>) => {
    if (e.target === dialogRef.current) handleClose();
  }, [handleClose]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (isAgentTurn && !message.trim()) {
      setError('Message/prompt is required');
      return;
    }

    if (isAgentTurn && deliveryMode === 'announce' && availableChannels.length > 0 && !deliveryChannel) {
      setError('Select a delivery channel or switch to "Run silently"');
      return;
    }

    if (isAgentTurn && !agentId) {
      setError('Pick an agent to assign the scheduled task to');
      return;
    }

    if (!isAgentTurn) {
      for (const req of actionSpec?.required ?? []) {
        if (!(actionParams[req] ?? '').trim()) {
          setError(`Parameter "${req}" is required`);
          return;
        }
      }
    }

    // Build schedule
    let schedule: Record<string, unknown>;
    if (scheduleKind === 'cron') {
      if (!cronExpr.trim()) { setError('Cron expression required'); return; }
      schedule = { kind: 'cron', expr: cronExpr.trim() };
      if (cronTz.trim()) schedule.tz = cronTz.trim();
    } else if (scheduleKind === 'every') {
      schedule = { kind: 'every', everyMs: parseInt(everyMs) };
    } else {
      if (!atTime.trim()) { setError('Date/time required'); return; }
      schedule = { kind: 'at', at: new Date(atTime).toISOString() };
    }

    // Build payload
    const sessionTarget = isAgentTurn ? 'isolated' : 'main';
    let payload: Record<string, unknown>;
    if (isAgentTurn) {
      let finalMessage = message.trim();

      // Workaround: announce delivery doesn't reliably send to channels like WhatsApp.
      // Instead, append a send instruction to the agent prompt so it uses the message tool directly.
      if (deliveryMode === 'announce' && deliveryChannel && deliveryTo.trim()) {
        finalMessage += `\n\nSend the result using the message tool (channel=${deliveryChannel}, target=${deliveryTo.trim()}). Keep the message concise. After sending, respond with only: NO_REPLY`;
      }

      payload = { kind: 'agentTurn', message: finalMessage };
      // Always present (even blank) so an edit that clears an override — back
      // to "Default" — round-trips as a clear, not a no-op.
      payload.model = model;
      payload.thinking = thinking;
    } else {
      payload = { kind: 'heartbeatAction', actionKind: executionKind, params: actionParams };
    }

    // Build delivery — use "none" when we've baked send instructions into the prompt
    const hasInlineDelivery = isAgentTurn && deliveryMode === 'announce' && deliveryChannel && deliveryTo.trim();
    const delivery: Record<string, unknown> = { mode: hasInlineDelivery ? 'none' : deliveryMode };
    if (deliveryMode === 'announce' && !hasInlineDelivery) {
      if (deliveryChannel) delivery.channel = deliveryChannel;
      if (deliveryTo.trim()) delivery.to = deliveryTo.trim();
    }

    const job: Record<string, unknown> = {
      schedule,
      payload,
      sessionTarget,
      sessionKey: effectiveTargetRootSessionKey,
      delivery,
      enabled: true,
    };
    if (name.trim()) job.name = name.trim();
    if (isAgentTurn && agentId) job.agentId = agentId;

    setSubmitting(true);
    const ok = await onSubmit(job);
    setSubmitting(false);

    if (ok) {
      handleClose();
    } else {
      setError(`Failed to ${mode === 'edit' ? 'update' : 'create'} cron job`);
    }
  }, [name, scheduleKind, cronExpr, cronTz, everyMs, atTime, isAgentTurn, executionKind, actionSpec, actionParams, effectiveTargetRootSessionKey, message, model, thinking, agentId, deliveryMode, deliveryChannel, deliveryTo, onSubmit, handleClose, mode, availableChannels.length]);

  if (!open) return null;

  const isEdit = mode === 'edit';
  return (
    <dialog
      ref={dialogRef}
      onCancel={handleClose}
      onClick={handleDialogClick}
      aria-labelledby="cron-dialog-title"
      className="fixed inset-0 z-50 m-auto max-h-[calc(100dvh-1.067rem)] w-[min(1040px,calc(100vw-1.067rem))] overflow-y-auto rounded-[24px] border border-border/80 bg-card/96 p-0 shadow-[0_36px_90px_rgba(0,0,0,0.38)] backdrop:bg-black/52 sm:max-h-[calc(100dvh-2rem)] sm:rounded-[30px]"
      style={{ overscrollBehavior: 'contain' }}
    >
      <form onSubmit={handleSubmit} onClick={e => e.stopPropagation()} className="flex flex-col">
        {/* Header */}
        <div className="border-b border-border/70 bg-secondary/42 px-4 py-3 sm:px-5 sm:py-3.5">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <div className="cockpit-kicker">
                <span className="text-primary">◆</span>
                Scheduler
              </div>
              <h2 id="cron-dialog-title" className="cockpit-title text-balance text-[1.15rem]">
                {isEdit ? 'Edit cron job' : 'Create cron job'}
              </h2>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="shell-icon-button min-h-9 px-3"
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        <div className="grid gap-3 px-3 py-3 sm:gap-4 sm:px-4 sm:py-4 lg:grid-cols-[minmax(0,0.88fr)_minmax(0,1.12fr)]">
          <div className="space-y-4">
            <SectionShell
              eyebrow="Identity"
              title="Name and timing"
              description="Name the job and choose the cadence it should follow."
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor="cron-name" className="cockpit-field-label">Name (optional)</label>
                  <input
                    id="cron-name"
                    type="text"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    placeholder="Morning status digest"
                    className="cockpit-input"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <span className="cockpit-field-label">Schedule type</span>
                  <CronSelect
                    value={scheduleKind}
                    onChange={e => setScheduleKind(e.target.value as ScheduleKind)}
                    aria-label="Schedule type"
                  >
                    <option value="every">Recurring interval</option>
                    <option value="cron">Cron expression</option>
                    <option value="at">One-shot at time</option>
                  </CronSelect>
                </div>
              </div>

              {scheduleKind === 'cron' && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="flex flex-col gap-1">
                    <label htmlFor="cron-expr" className="cockpit-field-label">Cron expression</label>
                    <input
                      id="cron-expr"
                      type="text"
                      value={cronExpr}
                      onChange={e => setCronExpr(e.target.value)}
                      placeholder="0 9 * * *"
                      className="cockpit-input cockpit-input-mono"
                    />
                    <span className="cockpit-field-hint">Minute, hour, day, month, weekday.</span>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label htmlFor="cron-tz" className="cockpit-field-label">Timezone (optional)</label>
                    <input
                      id="cron-tz"
                      type="text"
                      value={cronTz}
                      onChange={e => setCronTz(e.target.value)}
                      placeholder="Europe/Berlin"
                      className="cockpit-input cockpit-input-mono"
                    />
                    <span className="cockpit-field-hint">Blank uses the server timezone.</span>
                  </div>
                </div>
              )}

              {scheduleKind === 'every' && (
                <div className="flex flex-col gap-1">
                  <span className="cockpit-field-label">Interval</span>
                  <CronSelect
                    value={everyMs}
                    onChange={e => setEveryMs(e.target.value)}
                    aria-label="Interval"
                  >
                    {INTERVAL_PRESETS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </CronSelect>
                  <span className="cockpit-field-hint">Best for recurring checks and summaries.</span>
                </div>
              )}

              {scheduleKind === 'at' && (
                <div className="flex flex-col gap-1">
                  <label htmlFor="cron-at-time" className="cockpit-field-label">Date &amp; time</label>
                  <input
                    id="cron-at-time"
                    type="datetime-local"
                    value={atTime}
                    onChange={e => setAtTime(e.target.value)}
                    style={{ colorScheme: 'dark' }}
                    className="cockpit-input cockpit-input-mono [&::-webkit-calendar-picker-indicator]:brightness-[2.8] [&::-webkit-calendar-picker-indicator]:opacity-70 [&::-webkit-calendar-picker-indicator]:hover:opacity-100"
                  />
                  <span className="cockpit-field-hint">Use this for a single future run.</span>
                </div>
              )}
            </SectionShell>

            <SectionShell
              eyebrow="Execution"
              title="How it runs"
              description="Schedule an assigned agent task, or one of the heartbeat's built-in operator levers."
            >
              <div className="flex flex-col gap-1">
                <span className="cockpit-field-label">Execution type</span>
                <CronSelect
                  value={executionKind}
                  onChange={e => {
                    const kind = e.target.value;
                    setExecutionKind(kind);
                    // Switching among built-ins drops params the new action
                    // doesn't declare — the registry would refuse them anyway.
                    if (kind !== 'agentTurn') {
                      const known = actions[kind]?.params ?? {};
                      setActionParams(prev => Object.fromEntries(
                        Object.entries(prev).filter(([k]) => k in known),
                      ));
                    }
                  }}
                  aria-label="Execution type"
                >
                  {!editingBuiltin && <option value="agentTurn">Agent task (assigned Task)</option>}
                  {(mode === 'create' || editingBuiltin) && builtinKinds.map((k) => (
                    <option key={k} value={k}>{ACTION_LABELS[k] || k}</option>
                  ))}
                </CronSelect>
                {mode === 'edit' && (
                  <span className="cockpit-field-hint">
                    {editingBuiltin
                      ? 'A built-in action stays a built-in — it can switch levers, but not become an agent task.'
                      : 'A scheduled agent task stays an agent task.'}
                  </span>
                )}
              </div>
              {isAgentTurn && (
                <div className="flex flex-col gap-1">
                  <span className="cockpit-field-label">Assign to agent</span>
                  <CronSelect
                    value={agentId}
                    onChange={e => setAgentId(e.target.value)}
                    aria-label="Assign to agent"
                  >
                    <option value="">Pick a taskable agent…</option>
                    {taskableAgents.map((a) => (
                      <option key={a.id} value={a.id}>{a.name}</option>
                    ))}
                  </CronSelect>
                  <span className="cockpit-field-hint">
                    The schedule creates an assigned Task for this agent; any
                    proposal it makes still parks in the Decisions Inbox.
                  </span>
                </div>
              )}
              <div className="cockpit-note" data-tone="primary">
                {isAgentTurn
                  ? `Agent tasks run in their own private cron session beneath ${agentName}'s main agent and keep the main thread clean.`
                  : actionSpec?.description || 'A built-in heartbeat action — an operator lever fired on a schedule.'}
              </div>
            </SectionShell>
          </div>

          <div className="space-y-4">
            <SectionShell
              eyebrow="Payload"
              title={isAgentTurn ? 'What the agent should do' : 'Action parameters'}
              description={isAgentTurn
                ? 'Write the task the same way you would brief a teammate.'
                : 'The action registry declares what this lever accepts — nothing else reaches it.'}
            >
              {isAgentTurn ? (
                <div className="flex flex-col gap-1">
                  <label htmlFor="cron-message" className="cockpit-field-label">Prompt</label>
                  <textarea
                    id="cron-message"
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    rows={3}
                    placeholder="Check my inbox, summarize the important items, and flag anything that needs a reply."
                    className="cockpit-textarea min-h-[118px]"
                  />
                </div>
              ) : Object.keys(actionSpec?.params ?? {}).length === 0 ? (
                <div className="cockpit-note">
                  This action takes no parameters.
                </div>
              ) : (
                Object.entries(actionSpec?.params ?? {}).map(([param, doc]) => (
                  <div key={param} className="flex flex-col gap-1">
                    <label htmlFor={`cron-param-${param}`} className="cockpit-field-label">
                      {param}{actionSpec?.required.includes(param) ? '' : ' (optional)'}
                    </label>
                    <input
                      id={`cron-param-${param}`}
                      type="text"
                      value={actionParams[param] ?? ''}
                      onChange={e => setActionParams(prev => ({ ...prev, [param]: e.target.value }))}
                      className="cockpit-input cockpit-input-mono"
                    />
                    <span className="cockpit-field-hint">{doc}</span>
                  </div>
                ))
              )}

              {isAgentTurn && (
                <div className="flex flex-col gap-1">
                  {/* The SAME paired model/effort selectors as the chat header
                      and the agents panel — one control everywhere a model is
                      picked. `inline` is REQUIRED here: showModal() makes
                      everything outside the <dialog> inert, so a portal'd
                      listbox would open but never take a click. */}
                  <div className="flex min-w-0 flex-wrap items-center gap-1 sm:gap-2">
                    <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-1">
                      <Cpu size={12} className="shrink-0 text-foreground/70" aria-hidden="true" />
                      <span className="text-[0.733rem] text-muted-foreground">Model</span>
                      <InlineSelect
                        value={model}
                        onChange={setModel}
                        ariaLabel="Model"
                        disabled={models.length <= 1}
                        triggerClassName="max-w-[180px] rounded-xl border-border/75 bg-background/65 px-2.5 py-1.5 text-[0.733rem] font-sans text-foreground sm:min-h-8 sm:px-2.5 sm:py-1"
                        menuClassName="min-w-[180px] rounded-2xl border-border/80 bg-card/98 p-1 shadow-[0_20px_50px_rgba(0,0,0,0.28)] sm:min-w-[220px]"
                        options={models}
                        inline
                      />
                    </div>
                    <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-1">
                      <Gauge size={12} className="shrink-0 text-foreground/70" aria-hidden="true" />
                      <span className="text-[0.733rem] text-muted-foreground">Effort</span>
                      <InlineSelect
                        value={thinking}
                        onChange={setThinking}
                        ariaLabel="Thinking effort"
                        disabled={thinkingLevels.length === 0}
                        title={thinkingLevels.length === 0 ? 'This model has no thinking control' : undefined}
                        triggerClassName="max-w-[110px] rounded-xl border-border/75 bg-background/65 px-2.5 py-1.5 text-[0.733rem] font-sans text-foreground sm:min-h-8 sm:px-2.5 sm:py-1"
                        menuClassName="rounded-2xl border-border/80 bg-card/98 p-1 shadow-[0_20px_50px_rgba(0,0,0,0.28)]"
                        options={thinkingOptions}
                        inline
                      />
                    </div>
                  </div>
                  <span className="cockpit-field-hint">
                    Leave these on default unless the job needs a specific model or effort level.
                  </span>
                </div>
              )}
            </SectionShell>

            {isAgentTurn && (
              <SectionShell
                eyebrow="Delivery"
                title="What happens after it finishes"
                description="Choose whether the result stays in Nerve or gets sent out."
              >
                <div className="flex flex-col gap-1">
                  <span className="cockpit-field-label">Result handling</span>
                  <CronSelect
                    value={deliveryMode}
                    onChange={e => setDeliveryMode(e.target.value as DeliveryMode)}
                    aria-label="Delivery mode"
                  >
                    <option value="announce">Send result to a channel</option>
                    <option value="none">Keep it inside Nerve</option>
                  </CronSelect>
                </div>

                {deliveryMode === 'none' && (
                  <div className="cockpit-note">
                    The result stays in the session transcript for later review.
                  </div>
                )}

                {deliveryMode === 'announce' && (
                  <div className="space-y-2.5">
                    {availableChannels.length === 0 ? (
                      <div className="rounded-[18px] border border-orange/30 bg-orange/6 px-3 py-3 text-[0.733rem] text-orange/85">
                        No messaging channels are configured yet. Set one up in OpenClaw first, or keep the job inside Nerve.
                      </div>
                    ) : (
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="flex flex-col gap-1">
                          <span className="cockpit-field-label">Channel</span>
                          <CronSelect
                            value={deliveryChannel}
                            onChange={e => setDeliveryChannel(e.target.value)}
                            aria-label="Delivery channel"
                          >
                            <option value="">Select channel…</option>
                            {availableChannels.map((channel) => (
                              <option key={channel} value={channel}>{CHANNEL_LABELS[channel] || channel}</option>
                            ))}
                          </CronSelect>
                        </div>
                        <div className="flex flex-col gap-1">
                          <label htmlFor="cron-deliver-to" className="cockpit-field-label">Recipient / destination</label>
                          <input
                            id="cron-deliver-to"
                            type="text"
                            value={deliveryTo}
                            onChange={e => setDeliveryTo(e.target.value)}
                            placeholder={CHANNEL_PLACEHOLDERS[deliveryChannel] || 'recipient ID'}
                            className="cockpit-input cockpit-input-mono"
                          />
                        </div>
                      </div>
                    )}
                    <div className="cockpit-note" data-tone="primary">
                      Nerve appends the delivery instruction so the agent can send the result directly when it finishes.
                    </div>
                  </div>
                )}
              </SectionShell>
            )}

            {error && (
              <div className="cockpit-note" data-tone="danger">{error}</div>
            )}

            <div className="flex flex-col items-stretch gap-3 rounded-[24px] border border-border/70 bg-secondary/28 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <p className="text-sm leading-5 text-muted-foreground">
                {isEdit
                  ? 'Save when the timing and delivery look right.'
                  : 'Create the job when the timing and delivery look right.'}
              </p>
              <button
                type="submit"
                disabled={submitting}
                className="inline-flex min-h-10 items-center justify-center rounded-2xl bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-[0_16px_34px_rgba(0,0,0,0.24)] transition-transform hover:-translate-y-px hover:bg-primary/95 disabled:cursor-not-allowed disabled:opacity-50 sm:shrink-0"
              >
                {submitting ? (isEdit ? 'Saving...' : 'Creating...') : (isEdit ? 'Save Changes' : 'Create Cron')}
              </button>
            </div>
          </div>
        </div>
      </form>
    </dialog>
  );
}
