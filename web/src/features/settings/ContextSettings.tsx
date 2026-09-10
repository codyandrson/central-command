import { useCallback, useEffect, useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

/**
 * Context section of Settings (2026-09-10) — what the runtime does to an
 * agent's WORKING window as it fills. Server-backed (`app_setting` via
 * GET/PUT /api/context/settings): these flags change what the backend sends
 * to the model, so they must live where `runtime/context.py` can read them.
 * Each one takes effect at the agent's next model request; the transcript on
 * record is never changed, so turning a toggle off restores the full history.
 */
export interface ContextSettingsShape {
  drop_thinking: boolean;
  clear_tool_results: boolean;
  tool_results_keep_turns: number;
  pressure_warning: boolean;
  output_headroom: boolean;
  output_headroom_tokens: number;
  summarize: boolean;
  summarize_threshold: number;
}

const TOGGLES: Array<{ key: keyof ContextSettingsShape; label: string; help: string }> = [
  { key: 'drop_thinking', label: 'Drop old reasoning',
    help: 'Send the model its reasoning from the latest turn only. Earlier thinking stays on record but leaves the window.' },
  { key: 'clear_tool_results', label: 'Clear old tool output',
    help: 'Replace tool results older than the kept turns with a one-line placeholder. The call itself stays visible.' },
  { key: 'pressure_warning', label: 'Warn the agent under pressure',
    help: 'Once the window passes the pressure threshold, tell the agent so it can record what matters before it is lost.' },
  { key: 'output_headroom', label: 'Reserve output headroom',
    help: 'Count a reply-sized reserve against the window, so a full prompt is caught before the model has no room to answer.' },
  { key: 'summarize', label: 'Summarize when still over budget',
    help: 'If the window is still past the threshold after the steps above, compress the oldest half into a summary (one model call, cached per session).' },
];

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json() as { ok: boolean; result?: T; error?: string };
  if (!res.ok || !body.ok || body.result === undefined) throw new Error(body.error || `HTTP ${res.status}`);
  return body.result;
}

export function ContextSettings() {
  const [form, setForm] = useState<ContextSettingsShape | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api<ContextSettingsShape>('/api/context/settings')
      .then((s) => { setForm(s); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const set = useCallback(<K extends keyof ContextSettingsShape>(key: K, value: ContextSettingsShape[K]) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }, []);

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const saved = await api<ContextSettingsShape>('/api/context/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      setForm(saved);
      setError(null);
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const loaded = form !== null;

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-[0.8rem] font-semibold text-foreground">Context window</h3>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          What an agent is sent as its conversation fills the model&apos;s window. Nothing here
          edits the transcript on record; each toggle applies at the agent&apos;s next turn.
        </p>
      </div>

      {TOGGLES.map((t) => (
        <div key={t.key} className="space-y-1">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor={`ctx-${t.key}`} className="text-[0.75rem] text-foreground">{t.label}</label>
            <Switch
              id={`ctx-${t.key}`}
              checked={Boolean(form?.[t.key])}
              onCheckedChange={(v) => set(t.key, v as never)}
              disabled={!loaded}
            />
          </div>
          <p className="text-[0.667rem] text-muted-foreground">{t.help}</p>
        </div>
      ))}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <label htmlFor="ctx-keep-turns" className="text-[0.75rem] text-foreground">Tool output kept (turns)</label>
          <Input id="ctx-keep-turns" type="number" min={0} max={50} disabled={!loaded || !form?.clear_tool_results}
            value={form?.tool_results_keep_turns ?? ''}
            onChange={(e) => set('tool_results_keep_turns', Number(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="ctx-headroom" className="text-[0.75rem] text-foreground">Headroom (tokens)</label>
          <Input id="ctx-headroom" type="number" min={0} max={200000} step={1024} disabled={!loaded || !form?.output_headroom}
            value={form?.output_headroom_tokens ?? ''}
            onChange={(e) => set('output_headroom_tokens', Number(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="ctx-summarize-at" className="text-[0.75rem] text-foreground">Summarize at (fraction)</label>
          <Input id="ctx-summarize-at" type="number" min={0.5} max={1} step={0.05} disabled={!loaded || !form?.summarize}
            value={form?.summarize_threshold ?? ''}
            onChange={(e) => set('summarize_threshold', Number(e.target.value))} />
        </div>
      </div>

      {error && <p className="text-[0.7rem] text-destructive">{error}</p>}
      {savedAt && !error && <p className="text-[0.667rem] text-muted-foreground">Saved.</p>}

      <Button size="sm" variant="outline" onClick={handleSave} disabled={!loaded || saving}>
        {saving ? 'Saving…' : 'Save'}
      </Button>
    </div>
  );
}
