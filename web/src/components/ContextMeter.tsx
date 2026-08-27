import { useRef, useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { fmtK } from '@/lib/formatting';
import { AnimatedNumber } from '@/components/ui/AnimatedNumber';
import { CONTEXT_WARNING_THRESHOLD, CONTEXT_CRITICAL_THRESHOLD } from '@/lib/constants';
import { PROGRESS_BAR_TRANSITION } from '@/lib/progress-colors';

// Pre-defined color configs to avoid object creation during render
const COLOR_CRITICAL = {
  bar: 'bg-red',
  glow: 'rgba(231, 76, 60, 0.4)',
  growGlow: 'rgba(231, 76, 60, 0.6)',
  text: 'text-red',
} as const;

const COLOR_WARNING = {
  bar: 'bg-orange',
  glow: 'rgba(232, 168, 56, 0.4)',
  growGlow: 'rgba(232, 168, 56, 0.6)',
  text: 'text-orange',
} as const;

const COLOR_NORMAL = {
  bar: 'bg-green',
  glow: 'rgba(76, 175, 80, 0.3)',
  growGlow: 'rgba(76, 175, 80, 0.5)',
  text: 'text-muted-foreground',
} as const;

// Unknown limit: muted, no fill, no warning coloring — never fabricate a denominator.
const COLOR_UNKNOWN = {
  bar: 'bg-muted-foreground/20',
  glow: 'rgba(148, 163, 184, 0.15)',
  growGlow: 'rgba(148, 163, 184, 0.15)',
  text: 'text-muted-foreground/50',
} as const;

/** Props for {@link ContextMeter}. */
interface ContextMeterProps {
  /** Number of context tokens consumed so far. */
  used: number;
  /** Maximum context window size in tokens, or null/undefined if the gateway didn't report one. */
  limit: number | null | undefined;
}

/**
 * Compact progress bar showing context-window token usage.
 *
 * Transitions through green → orange → red as usage crosses warning/critical
 * thresholds, and includes an animated token counter and glow effects. When
 * the gateway hasn't reported a limit for the model, renders "<used> / ?" in
 * a muted, unknown state instead of assuming a limit.
 * Displayed in the {@link StatusBar}.
 */
export function ContextMeter({ used, limit }: ContextMeterProps) {
  const hasLimit = limit != null && limit > 0;
  const percent = hasLimit ? Math.min(100, (used / limit) * 100) : 0;
  const [isGrowing, setIsGrowing] = useState(false);
  const prevPercentRef = useRef(percent);

  useEffect(() => {
    setIsGrowing(percent > prevPercentRef.current);
    prevPercentRef.current = percent;
  }, [percent]);

  const isWarning = hasLimit && percent >= CONTEXT_WARNING_THRESHOLD;
  const isCritical = hasLimit && percent >= CONTEXT_CRITICAL_THRESHOLD;

  const colors = !hasLimit ? COLOR_UNKNOWN : isCritical ? COLOR_CRITICAL : isWarning ? COLOR_WARNING : COLOR_NORMAL;

  // Enhanced glow when growing
  const boxShadow = isGrowing
    ? `0 0 8px ${colors.growGlow}, 0 0 4px ${colors.glow}`
    : `0 0 4px ${colors.glow}`;

  const tooltipText = hasLimit
    ? `Context: ${fmtK(used)} / ${fmtK(limit)} tokens (${percent.toFixed(0)}%)${
        isCritical
          ? ' — CRITICAL: Consider starting a new session'
          : isWarning
          ? ' — Warning: Approaching context limit'
          : ''
      }`
    : `Context: ${fmtK(used)} tokens — limit unknown: the gateway did not report a limit for this model`;

  return (
    <div
      className="flex cursor-default items-center gap-2 max-[378px]:gap-1.5"
      title={tooltipText}
    >
      {/* Warning icon - only show when warning/critical */}
      {(isWarning || isCritical) && (
        <AlertTriangle
          size={10}
          className={`${colors.text} max-[378px]:size-[9px] ${isCritical ? 'animate-pulse' : ''}`}
        />
      )}

      {/* Progress bar with smooth width and color transitions */}
      <div className="h-2 w-16 overflow-hidden rounded-full border border-border/60 bg-background/80 max-[378px]:h-1.5 max-[378px]:w-12">
        <div
          className={`h-full rounded-full ${colors.bar}`}
          style={{ 
            width: `${percent}%`,
            boxShadow,
            transition: PROGRESS_BAR_TRANSITION,
          }}
        />
      </div>

      {/* Animated token count */}
      <AnimatedNumber
        value={used}
        format={fmtK}
        className={`font-mono text-[0.667rem] max-[378px]:text-[0.6rem] ${colors.text}`}
        duration={700}
      />

      {/* Limit unknown — show "?" rather than a fabricated number */}
      {!hasLimit && (
        <span className={`font-mono text-[0.667rem] max-[378px]:text-[0.6rem] ${colors.text}`}>
          / ?
        </span>
      )}

      {/* Label - changes based on state */}
      <span className={`font-mono text-[0.6rem] uppercase tracking-[0.2em] max-[378px]:text-[0.533rem] max-[378px]:tracking-[0.14em] ${colors.text}`}>
        Context
      </span>
    </div>
  );
}
