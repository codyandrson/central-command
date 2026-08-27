/**
 * SkillsTab — the agent's granted SKILLS, read-only.
 *
 * The vendored version listed skills from an `openclaw` CLI that does not exist
 * here, so it only ever rendered "openclaw CLI not found in PATH". The real
 * skills library is spine rows: `agents.detail` returns this agent's grants
 * (including revoked ones) exactly as the Agents page reads them.
 *
 * Read-only on purpose: granting and revoking lives on the Skills and Agents
 * screens, and there is one write path for it (`skills.grant`/`skills.revoke`).
 */

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Puzzle } from 'lucide-react';
import { useGateway } from '@/contexts/GatewayContext';
import type { AgentDetail, SkillGrant } from '@/features/agents/types';

function SkillRow({ skill }: { skill: SkillGrant }) {
  const retired = skill.status === 'RETIRED';
  const notDelivered = retired || !skill.has_guidance;
  return (
    <div className="border-b border-border/40 px-3 py-2">
      <div className="flex min-w-0 items-center gap-1.5 flex-wrap">
        <span className="text-[0.733rem] text-foreground leading-tight font-medium min-w-0 wrap-anywhere">
          {skill.title}
        </span>
        <span className="text-[0.6rem] px-1 py-px rounded-sm leading-tight bg-muted/30 text-muted-foreground">
          {skill.skill_id}
        </span>
        {notDelivered && (
          <span className="text-[0.6rem] px-1 py-px rounded-sm leading-tight bg-amber-500/10 text-amber-600 dark:text-amber-400 uppercase tracking-wider">
            not delivered
          </span>
        )}
      </div>
      {skill.summary && (
        <div className="text-[0.667rem] text-muted-foreground mt-0.5 leading-snug">
          {skill.summary}
        </div>
      )}
      <div className="text-[0.667rem] text-muted-foreground/70 mt-0.5">
        {retired ? 'retired skill' : skill.has_guidance ? 'guidance' : 'no guidance document'}
        {' · '}{skill.reference_count} ref{skill.reference_count === 1 ? '' : 's'}
      </div>
    </div>
  );
}

interface SkillsTabProps {
  agentId: string;
}

/** Workspace tab listing the skills this agent has been granted. */
export function SkillsTab({ agentId }: SkillsTabProps) {
  const { rpc, connectionState } = useGateway();
  const [skills, setSkills] = useState<SkillGrant[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!agentId) return;
    setIsLoading(true);
    try {
      const res = await rpc('agents.detail', { agentId }) as AgentDetail;
      setSkills(res.skills ?? []);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }, [agentId, rpc]);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    load();
  }, [connectionState, load]);

  const active = skills.filter(s => !s.revoked_at);
  const revoked = skills.filter(s => s.revoked_at);

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        <div className="flex items-center border-b border-border/40">
          <div className="flex items-center gap-2 px-3 py-1.5 text-[0.733rem] flex-1 text-muted-foreground">
            <Puzzle size={12} className="shrink-0" />
            <span>{active.length} granted</span>
          </div>
          <button
            onClick={load}
            disabled={isLoading}
            className="shrink-0 px-2 py-1.5 bg-transparent border-0 text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-purple/50 focus-visible:ring-offset-0"
            title="Refresh skills"
            aria-label="Refresh skills"
          >
            <RefreshCw size={10} className={isLoading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div aria-live="polite" aria-atomic="true">
          {error && <div className="px-3 py-2 text-[0.667rem] text-red bg-red/10">{error}</div>}
        </div>

        {!isLoading && !active.length && !error && (
          <div className="text-muted-foreground px-3 py-8 text-center flex flex-col items-center gap-2">
            <Puzzle size={20} className="text-muted-foreground/50" />
            <span className="text-[0.733rem]">No skills granted</span>
            <span className="text-[0.667rem] text-muted-foreground/70 max-w-[22rem]">
              Grant and revoke skills on the Skills or Agents screen — this view is read-only.
            </span>
          </div>
        )}

        {active.map(s => <SkillRow key={s.skill_id} skill={s} />)}

        {revoked.length > 0 && (
          <div className="border-t border-border/40 mt-1 opacity-60">
            <div className="px-3 py-2 text-[0.667rem] uppercase tracking-wider text-muted-foreground">
              Revoked ({revoked.length})
            </div>
            {revoked.map(s => (
              <div
                key={`${s.skill_id}-${s.revoked_at}`}
                className="px-3 py-1 text-[0.667rem] text-muted-foreground flex min-w-0 flex-wrap items-center gap-2"
              >
                <span className="line-through">{s.skill_id}</span>
                <span className="min-w-0 wrap-anywhere">{s.revoked_reason || 'no reason recorded'}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
