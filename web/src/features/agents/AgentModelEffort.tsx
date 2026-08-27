import { useState, useEffect } from 'react';
import { Cpu, Gauge } from 'lucide-react';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { InlineSelectOption } from '@/components/ui/InlineSelect';
import { useGatewayModelCatalog } from '@/hooks/useGatewayModelCatalog';
import type { AgentRow } from './types';

const DEFAULT_VALUE = '';

/* ── Model & effort override ── */

/**
 * Sets an agent's model/thinking override directly (`agents.setModel`) — a
 * direct operator action like grant/revoke, not a proposal. Presented as the
 * SAME two inline selectors as the chat header (model + effort, saved on
 * change), so the operator meets one control everywhere. Choosing "Default"
 * for either half clears the override (null); the effort options follow the
 * effective model — for "Default" that is the gateway's primary model, the
 * same rule as the chat header's inherited-model sentinel.
 */
export function AgentModelEffort({ agent, onSave }: {
  agent: AgentRow;
  onSave: (model: string | null, thinking: string | null) => Promise<void>;
}) {
  const { models } = useGatewayModelCatalog();
  const [model, setModelValue] = useState(agent.model ?? DEFAULT_VALUE);
  const [thinking, setThinking] = useState(agent.thinking ?? DEFAULT_VALUE);
  const [busy, setBusy] = useState(false);
  const [uiError, setUiError] = useState<string | null>(null);

  // Sync from the roster when the selected agent (or a refresh) changes.
  useEffect(() => {
    setModelValue(agent.model ?? DEFAULT_VALUE);
    setThinking(agent.thinking ?? DEFAULT_VALUE);
  }, [agent.id, agent.model, agent.thinking]);

  const list = models ?? [];
  const primary = list.find((m) => m.role === 'primary');
  const effective = model ? list.find((m) => m.id === model) : primary;
  const levels = effective?.thinkingLevels ?? [];
  const effortDisabled = busy || levels.length === 0;

  const modelOptions: InlineSelectOption[] = [
    { value: DEFAULT_VALUE, label: primary ? `Default (${primary.label})` : 'Default' },
    ...list.map((m) => ({ value: m.id, label: m.label })),
  ];
  const effortOptions: InlineSelectOption[] = [
    { value: DEFAULT_VALUE, label: 'default' },
    ...levels.map((lvl) => ({ value: lvl, label: lvl })),
  ];

  const save = async (nextModel: string, nextThinking: string, revert: () => void) => {
    setBusy(true);
    setUiError(null);
    try {
      await onSave(nextModel || null, nextThinking || null);
    } catch (err) {
      revert();
      setUiError((err as Error).message || 'Change failed');
    } finally {
      setBusy(false);
    }
  };

  const handleModelChange = (next: string) => {
    const prevModel = model;
    const prevThinking = thinking;
    // The new model may not support the current effort level — clear it rather
    // than silently keep an override the new model can't honor (the chat
    // header does the same on a model change).
    const nextEntry = next ? list.find((m) => m.id === next) : primary;
    const nextLevels = nextEntry?.thinkingLevels ?? [];
    const nextThinking = thinking && !nextLevels.includes(thinking) ? DEFAULT_VALUE : thinking;
    setModelValue(next);
    setThinking(nextThinking);
    void save(next, nextThinking, () => { setModelValue(prevModel); setThinking(prevThinking); });
  };

  const handleEffortChange = (next: string) => {
    const prev = thinking;
    setThinking(next);
    void save(model, next, () => setThinking(prev));
  };

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1 sm:gap-2">
      {uiError && (
        <span
          className="min-w-0 max-w-[220px] truncate text-[0.733rem] text-red"
          title={uiError}
          role="status"
          aria-live="polite"
        >
          ⚠ {uiError}
        </span>
      )}
      <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-1">
        <Cpu size={12} className="shrink-0 text-foreground/70" aria-hidden="true" />
        <span className="text-[0.733rem] text-muted-foreground">Model</span>
        <InlineSelect
          value={model}
          onChange={handleModelChange}
          ariaLabel="Model"
          disabled={busy || list.length === 0}
          title={uiError || undefined}
          triggerClassName="max-w-[180px] rounded-xl border-border/75 bg-background/65 px-2.5 py-1.5 text-[0.733rem] font-sans text-foreground sm:min-h-8 sm:px-2.5 sm:py-1"
          menuClassName="min-w-[180px] rounded-2xl border-border/80 bg-card/98 p-1 shadow-[0_20px_50px_rgba(0,0,0,0.28)] sm:min-w-[220px]"
          options={modelOptions}
        />
      </div>
      <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-1">
        <Gauge size={12} className="shrink-0 text-foreground/70" aria-hidden="true" />
        <span className="text-[0.733rem] text-muted-foreground">Effort</span>
        <InlineSelect
          value={thinking}
          onChange={handleEffortChange}
          ariaLabel="Effort"
          disabled={effortDisabled}
          title={effortDisabled && !busy ? 'This model has no thinking control' : uiError || undefined}
          triggerClassName="max-w-[110px] rounded-xl border-border/75 bg-background/65 px-2.5 py-1.5 text-[0.733rem] font-sans text-foreground sm:min-h-8 sm:px-2.5 sm:py-1"
          menuClassName="rounded-2xl border-border/80 bg-card/98 p-1 shadow-[0_20px_50px_rgba(0,0,0,0.28)]"
          options={effortOptions}
        />
      </div>
    </div>
  );
}

