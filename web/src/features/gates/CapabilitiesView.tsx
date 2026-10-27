import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { overrideStateLabel, useCapabilityCatalog, useOverrides } from './useOverrides';
import type { CapabilityRow } from '../agents/types';

/** The overrides on one capability, across agents — the same facts and one-click revoke as the agent page. */
function CapabilityOverrides({ capability }: { capability: string }) {
  const { overrides, error, revoke } = useOverrides({ capability });
  const open = overrides.filter((o) => o.state !== 'revoked');
  return (
    <div className="mt-1.5 space-y-1">
      {error && <p className="text-[0.667rem] text-red">{error}</p>}
      {open.length === 0 && !error && (
        <p className="text-[0.667rem] text-muted-foreground">No overrides on this capability.</p>
      )}
      {open.map((o) => (
        <div key={o.id} className="flex min-w-0 flex-wrap items-center gap-2 text-[0.7rem]">
          <span className="cockpit-badge">{o.agent_id}</span>
          <span className="cockpit-badge">{overrideStateLabel(o)}</span>
          <span className="min-w-0 wrap-anywhere">{o.scope_text}</span>
          <span className="text-muted-foreground">by {o.granted_by}</span>
          <Button size="sm" variant="outline" onClick={() => revoke(o.agent_id, o.id)}>
            Revoke
          </Button>
        </div>
      ))}
    </div>
  );
}

function CapabilityCard({ c }: { c: CapabilityRow }) {
  return (
    <div className="rounded-lg border border-border/40 p-2 text-[0.7rem]">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="font-medium">{c.name}</span>
        <span className="cockpit-badge">default gate: {c.default_gate}</span>
        <span className="cockpit-badge">{c.reversibility}</span>
        {c.requires_acknowledgement && (
          <span className="cockpit-badge">requires acknowledgement</span>
        )}
        <span className="cockpit-badge">{c.overridable ? 'overridable' : 'not overridable'}</span>
      </div>
      <div className="mt-1 text-muted-foreground">{c.overridable_reason}</div>
      {c.overridable && <CapabilityOverrides capability={c.name} />}
    </div>
  );
}

/** The governed capability registry: each row's default gate and whether an override may relax it. */
export function CapabilitiesView() {
  const { capabilities, error } = useCapabilityCatalog(true);
  const [filter, setFilter] = useState('');
  const rows = capabilities.filter((c) => c.name.includes(filter.trim()));
  return (
    <div className="space-y-2 p-3">
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter capabilities"
        aria-label="Filter capabilities"
        className="w-full rounded border border-border/40 bg-transparent px-2 py-1 text-[0.733rem]"
      />
      {error && <p className="text-[0.667rem] text-red">{error}</p>}
      {rows.map((c) => <CapabilityCard key={c.name} c={c} />)}
    </div>
  );
}
