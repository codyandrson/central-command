import { useState, useMemo } from 'react';
import type { TokenData, TokenEntry } from '@/types';
import { AnimatedNumber } from '@/components/ui/AnimatedNumber';
import { fmtTokens } from '@/lib/formatting';

// ── Provider icons & colors ──────────────────────────────────────────

const PROVIDER_ICONS: Record<string, string> = {
  anthropic: '🟣',
  'openai-codex': '⚡',
  openai: '🟢',
  google: '🔵',
  gemini: '🔵',
};

const PROVIDER_BAR_CLASSES: Record<string, string> = {
  anthropic: 'bg-purple shadow-[0_0_4px_rgba(155,89,182,0.4)]',
  'openai-codex': 'bg-green shadow-[0_0_4px_rgba(76,175,80,0.4)]',
  openai: 'bg-green shadow-[0_0_4px_rgba(76,175,80,0.4)]',
};

const DEFAULT_BAR_CLASS = 'bg-primary shadow-[0_0_8px_rgba(232,168,56,0.3)]';

// ── Expandable provider row ──────────────────────────────────────────

function ProviderRow({
  entry,
  maxCost,
}: {
  entry: TokenEntry;
  maxCost: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const pct = Math.max(2, (entry.cost / maxCost) * 100);
  const barClass = PROVIDER_BAR_CLASSES[entry.source] || DEFAULT_BAR_CLASS;
  const costCents = Math.round(entry.cost * 100);
  const icon = PROVIDER_ICONS[entry.source] || '●';
  const avgCost = entry.messageCount ? entry.cost / entry.messageCount : 0;
  const hasErrors = (entry.errorCount || 0) > 0;

  return (
    <div className="flex flex-col">
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${entry.source} provider details`}
        className="flex items-center gap-2 text-[0.733rem] w-full hover:bg-muted/30 rounded px-0.5 py-0.5 transition-colors cursor-pointer group"
      >
        <span className="w-3.5 text-center shrink-0 text-xs flex items-center justify-center">{icon}</span>
        <span className="text-foreground text-[0.733rem] font-bold w-16 shrink-0 uppercase tracking-[0.5px]">
          {entry.source}
        </span>
        <div className="flex-1 h-2 bg-background border border-border/60 overflow-hidden">
          <div
            className={`h-full ${barClass}`}
            style={{
              width: `${pct}%`,
              transition: 'width 700ms cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
          />
        </div>
        <AnimatedNumber
          value={costCents}
          format={(n) => '$' + (n / 100).toFixed(2)}
          className="text-muted-foreground text-[0.733rem] w-13 text-right shrink-0"
          duration={600}
        />
        <span
          className={`text-[0.667rem] transition-transform duration-150 ${expanded ? 'rotate-180' : ''} text-muted-foreground/50 group-hover:text-muted-foreground`}
        >
          ▼
        </span>
      </button>

      {expanded && (
        <div className="pl-6 pr-1 pb-1.5 pt-0.5 flex flex-col gap-1 border-l-2 border-border/30 ml-[7px]">
          {/* Token breakdown */}
          <div className="flex gap-3 text-[0.733rem] text-muted-foreground flex-wrap">
            <span>
              ↑ <span className="text-foreground">{fmtTokens(entry.inputTokens || 0)}</span> in
            </span>
            <span>
              ↓ <span className="text-foreground">{fmtTokens(entry.outputTokens || 0)}</span> out
            </span>
            {(entry.cacheReadTokens || 0) > 0 && (
              <span>
                📦 <span className="text-foreground">{fmtTokens(entry.cacheReadTokens || 0)}</span> cached
              </span>
            )}
          </div>

          {/* Message stats */}
          <div className="flex gap-3 text-[0.733rem] text-muted-foreground flex-wrap">
            <span>
              💬 <span className="text-foreground">{(entry.messageCount || 0).toLocaleString()}</span> msgs
            </span>
            <span>
              avg <span className="text-foreground">${avgCost.toFixed(4)}</span>/msg
            </span>
            {hasErrors && (
              <span className="text-red">
                ⚠ <span className="font-bold">{entry.errorCount}</span> errors
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Spend-over-time mini bar chart (presentational) ────────────────────

/** One bar per day of `daily`, height ∝ cost. Renders nothing if there's
 * no data or every day is $0 — no empty-state box for a widget this small. */
function DailySpendChart({ daily }: { daily?: TokenData['daily'] }) {
  const max = Math.max(0, ...((daily || []).map((d) => d.cost)));
  if (!daily || daily.length === 0 || max <= 0) return null;

  return (
    <div className="flex items-end gap-[2px] h-10 px-1 py-1 border border-border/40 bg-background/40">
      {daily.map((d) => (
        <div key={d.date} className="flex-1 h-full flex items-end" title={`${d.date} — $${d.cost.toFixed(4)}`}>
          {d.cost > 0 && (
            <div
              className={`w-full ${DEFAULT_BAR_CLASS}`}
              style={{ height: `${Math.max(1, Math.round((d.cost / max) * 36))}px` }}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

interface TokenUsageProps {
  data: TokenData | null;
}

/** Dashboard widget displaying token usage breakdown with visual bars. */
export function TokenUsage({ data }: TokenUsageProps) {
  const entries = useMemo(
    () =>
      (data?.entries || []).filter(
        (e) => e.cost > 0 || (e.messageCount || 0) > 0 || (e.errorCount || 0) > 0,
      ),
    [data?.entries],
  );
  const maxCost = useMemo(() => Math.max(1, ...entries.map((e) => e.cost)), [entries]);

  if (!data) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <div className="panel-header border-l-[3px] border-l-primary">
          <span className="panel-label text-primary">
            <span className="panel-diamond">◆</span>
            USAGE
          </span>
        </div>
        <div className="p-3 text-muted-foreground text-[0.667rem]">Loading…</div>
      </div>
    );
  }

  const totalCostCents = Math.round((data.persistent?.totalCost ?? data.totalCost ?? 0) * 100);

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="panel-header border-l-[3px] border-l-primary">
        <span className="panel-label text-primary">
          <span className="panel-diamond">◆</span>
          USAGE
        </span>
      </div>
      <div className="flex-1 overflow-y-auto">
        <div className="px-3 py-2.5 flex flex-col gap-1">
          {/* ── Accumulated cost ───────────────────────────────── */}
          {typeof data.windowCost === 'number' ? (
            <div className="flex flex-col gap-0.5 pb-1.5 border-b border-border/40 mb-0.5">
              <div className="flex items-baseline gap-2">
                <AnimatedNumber
                  value={Math.round(data.windowCost * 100)}
                  format={(n) => '$' + (n / 100).toFixed(2)}
                  className="text-xl font-bold text-primary [text-shadow:0_0_8px_rgba(232,168,56,0.3)]"
                  duration={800}
                />
                <span className="text-[0.733rem] text-muted-foreground uppercase tracking-[1px]">
                  {data.windowDays ?? 30}d
                </span>
              </div>
              <span className="text-[0.667rem] text-muted-foreground/60">
                all-time ${(totalCostCents / 100).toFixed(2)}
              </span>
            </div>
          ) : (
            <div className="flex items-baseline gap-2 pb-1.5 border-b border-border/40 mb-0.5">
              <AnimatedNumber
                value={totalCostCents}
                format={(n) => '$' + (n / 100).toFixed(2)}
                className="text-xl font-bold text-primary [text-shadow:0_0_8px_rgba(232,168,56,0.3)]"
                duration={800}
              />
              <span className="text-[0.733rem] text-muted-foreground uppercase tracking-[1px]">all-time</span>
            </div>
          )}

          {/* ── Spend over time ─────────────────────────────────── */}
          <DailySpendChart daily={data.daily} />

          {/* ── Per-provider expandable rows ───────────────────── */}
          {entries.length > 0 ? (
            entries.map((e) => (
              <ProviderRow
                key={e.source}
                entry={e}
                maxCost={maxCost}
              />
            ))
          ) : (
            <div className="text-[0.733rem] text-muted-foreground/50 italic">No usage data</div>
          )}

          {/* ── Aggregate token stats ──────────────────────────── */}
          <div className="flex gap-3 pt-1.5 mt-0.5 border-t border-border/40 text-[0.733rem] text-muted-foreground flex-wrap">
            <span>
              ↑{' '}
              <AnimatedNumber
                value={data.persistent?.totalInput || data.totalInput || 0}
                format={fmtTokens}
                className="text-foreground"
                duration={600}
              />{' '}
              in
            </span>
            <span>
              ↓{' '}
              <AnimatedNumber
                value={data.persistent?.totalOutput || data.totalOutput || 0}
                format={fmtTokens}
                className="text-foreground"
                duration={600}
              />{' '}
              out
            </span>
            {(data.totalMessages ?? 0) > 0 && (
              <span>
                💬{' '}
                <AnimatedNumber
                  value={data.totalMessages || 0}
                  format={(n) => n.toLocaleString()}
                  className="text-foreground"
                  duration={600}
                />{' '}
                msgs
              </span>
            )}
          </div>

          {/* ── Proxy UI link ───────────────────────────────────── */}
          {data.proxyUiUrl && (
            <div className="pt-1.5 mt-0.5 border-t border-border/40">
              <a
                href={data.proxyUiUrl}
                target="_blank"
                rel="noreferrer"
                className="text-[0.733rem] text-muted-foreground/60 hover:text-muted-foreground"
              >
                View in LiteLLM →
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
