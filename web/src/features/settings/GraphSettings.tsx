import { useEffect, useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useGraph } from '@/features/graph/useGraph';

/**
 * Graph section of Settings — the Graphistry seam (2026-08-09 spec). Server-
 * backed (`app_setting` table via GET/PUT /api/graph/settings), not
 * localStorage: this flag gates BACKEND health-probe behavior, so it must
 * persist where the API can read it, unlike the cockpit's client-only prefs.
 */
export function GraphSettings() {
  const { status, error, fetchStatus, saveSettings } = useGraph();
  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    void fetchStatus().then((s) => {
      if (!s) return;
      setEnabled(s.graphistry_enabled);
      setUrl(s.graphistry_url || '');
      setLoaded(true);
    });
  }, [fetchStatus]);

  const handleSave = async () => {
    setSaving(true);
    await saveSettings({ graphistry_enabled: enabled, graphistry_url: url });
    setSaving(false);
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-[0.8rem] font-semibold text-foreground">Graphistry</h3>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          Deferred integration — the toggle records intent only. The Pi never
          starts the Graphistry server; this just tells the Graph panel where
          to health-probe it.
        </p>
      </div>

      <div className="flex items-center justify-between gap-3">
        <label htmlFor="graphistry-enabled" className="text-[0.75rem] text-foreground">
          Enable Graphistry
        </label>
        <Switch
          id="graphistry-enabled"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={!loaded}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="graphistry-url" className="text-[0.75rem] text-foreground">
          Graphistry URL
        </label>
        <Input
          id="graphistry-url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://workstation:8080"
          disabled={!loaded}
        />
      </div>

      {error && <p className="text-[0.7rem] text-destructive">{error}</p>}
      {status && (
        <p className="text-[0.667rem] text-muted-foreground">
          Current state: <span className="font-medium text-foreground">{status.graphistry}</span>
        </p>
      )}

      <Button size="sm" variant="outline" onClick={handleSave} disabled={!loaded || saving}>
        {saving ? 'Saving…' : 'Save'}
      </Button>
    </div>
  );
}
