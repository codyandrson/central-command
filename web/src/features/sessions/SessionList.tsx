import { useRef, useEffect, useMemo, useState, useCallback } from 'react';
import type { Session } from '@/types';
import { getSessionKey } from '@/types';
import type { SpawnSessionOpts } from '@/contexts/SessionContext';
import { SessionSkeletonGroup } from '@/components/skeletons';
import { buildAgentSidebarTree, buildSessionTree, flattenTree, getSessionType } from './sessionTree';
import { getSessionDisplayLabel, isTopLevelAgentSessionKey, TERMINAL_SESSION_STATUSES } from './sessionKeys';
import { SessionNode } from './SessionNode';
import type { StalledDiscussion } from './useStalledDiscussions';
import type { GranularAgentState } from '@/types';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { AlertTriangle, History, Plus, RefreshCw } from 'lucide-react';
import { SpawnAgentDialog } from './SpawnAgentDialog';

interface SessionListProps {
  sessions: Session[];
  currentSession: string;
  /** Chat view is the one on screen — gates "currently viewing" behaviors
   *  (the history-filter pin) so they don't outlive navigating away.
   *  Defaults true so the standalone-render call sites (tests, any future
   *  chat-only usage) keep the old always-pinned behavior. */
  isViewingChat?: boolean;
  busyState: Record<string, boolean>;
  agentStatus?: Record<string, GranularAgentState>;
  unreadSessions?: Record<string, boolean>;
  onSelect: (key: string) => void;
  onRefresh: () => void;
  onDelete?: (sessionKey: string) => Promise<void>;
  onNewSession?: (sessionKey: string) => Promise<void>;
  onSpawn?: (opts: SpawnSessionOpts) => Promise<void | boolean>;
  onRename?: (sessionKey: string, label: string) => Promise<void>;
  onAbort?: (sessionKey: string) => Promise<void>;
  isLoading?: boolean;
  agentName?: string;
  /** Render in compact dropdown mode (chat-first topbar panel). */
  compact?: boolean;
  /** Tier-3 stall sweep: discussions flagged as quiet, keyed by session id.
   *  Supplied by the owner (App) so this list stays a pure view. */
  stalledDiscussions?: Record<string, StalledDiscussion>;
}

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

/** How long a lane has been quiet, in the coarsest unit that is still true:
 *  "3d", "6h", or "<1h". Never rounds up — see the call site. */
export function formatWaiting(elapsedMs: number): string {
  if (elapsedMs >= DAY_MS) return `${Math.floor(elapsedMs / DAY_MS)}d`;
  if (elapsedMs >= HOUR_MS) return `${Math.floor(elapsedMs / HOUR_MS)}h`;
  return '<1h';
}

/** Session rows carry `updatedAt` as an ISO string from the gateway even
 *  though older Nerve rows used epoch ms — accept both, trust neither. */
function parseMoment(value: string | number | undefined): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

function countDescendants(node: ReturnType<typeof buildSessionTree>[number]): number {
  return node.children.reduce((total, child) => total + 1 + countDescendants(child), 0);
}

function findNodeByKey(nodes: ReturnType<typeof buildSessionTree>, key: string): ReturnType<typeof buildSessionTree>[number] | null {
  const queue = [...nodes];
  while (queue.length > 0) {
    const node = queue.shift()!;
    if (node.key === key) return node;
    queue.push(...node.children);
  }
  return null;
}

/** Sidebar list of agent sessions with tree structure and context menus. */
export function SessionList({ sessions, currentSession, isViewingChat = true, busyState, agentStatus, unreadSessions, onSelect, onRefresh, onDelete, onNewSession, onSpawn, onRename, onAbort, isLoading, agentName = 'Agent', compact = false, stalledDiscussions }: SessionListProps) {
  const [deleteTarget, setDeleteTarget] = useState<{ key: string; label: string; descendantCount: number; isRootAgent: boolean } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [spawnOpen, setSpawnOpen] = useState(false);
  const [renamingKey, setRenamingKey] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);
  const [expandedState, setExpandedState] = useState<Record<string, boolean>>({});

  const handleDelete = useCallback(async () => {
    if (!deleteTarget || !onDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(deleteTarget.key);
      setDeleteTarget(null);
    } catch (err) {
      // Surface it — a silently-swallowed failure reads as "nothing happened"
      // (the 2026-07-23 dead-socket incident: closes clicked during a deploy
      // reconnect vanished into the console).
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, onDelete]);

  const startRename = useCallback((sessionKey: string, currentLabel: string) => {
    setRenamingKey(sessionKey);
    setRenameValue(currentLabel);
    setTimeout(() => renameInputRef.current?.focus(), 0);
  }, []);

  const commitRename = useCallback(async () => {
    if (!renamingKey || !onRename) return;
    const trimmed = renameValue.trim();
    if (trimmed) {
      try { await onRename(renamingKey, trimmed); } catch (err) { console.error('Failed to rename session:', err); }
    }
    setRenamingKey(null);
  }, [renamingKey, renameValue, onRename]);

  const cancelRename = useCallback(() => {
    setRenamingKey(null);
  }, []);

  const handleRenameChange = useCallback((value: string) => {
    setRenameValue(value);
  }, []);

  const handleToggleExpand = useCallback((key: string) => {
    setExpandedState((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  }, []);

  const prevPercentsRef = useRef<Record<string, number>>({});
  const prevTokensRef = useRef<Record<string, number>>({});

  // Calculate which sessions are growing (compare to previous render via ref)
  const growingSessions = useMemo(() => {
    const result: Record<string, boolean> = {};
    sessions.forEach(s => {
      const sessionKey = getSessionKey(s);
      const used = s.totalTokens || 0;
      // Unknown limit → no percent-derived visuals (never invent a denominator).
      const max = s.contextTokens;
      const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
      const prevPct = prevPercentsRef.current[sessionKey];
      result[sessionKey] = !!max && prevPct !== undefined && pct > prevPct;
    });
    return result;
  }, [sessions]);

  // Update refs AFTER render
  useEffect(() => {
    sessions.forEach(s => {
      const sessionKey = getSessionKey(s);
      const used = s.totalTokens || 0;
      const max = s.contextTokens;
      const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
      prevPercentsRef.current[sessionKey] = pct;
      if (used > 0) {
        prevTokensRef.current[sessionKey] = used;
      }
    });
  }, [sessions]);

  // History filter — closed/terminal sessions are archived out of the default
  // view (they're never deleted; this is purely display). The toggle shows
  // them; the currently-VIEWED row (chat view active AND it's the selected
  // session) is always kept visible so a filter flip can never yank the
  // session the operator is looking at — but that pin must not outlive
  // navigating away from chat, or it just protects the last-viewed session
  // forever.
  const [showHistory, setShowHistory] = useState(() => {
    try { return localStorage.getItem('cc-sessions-show-history') === 'true'; } catch { return false; }
  });
  const toggleHistory = useCallback(() => {
    setShowHistory(prev => {
      const next = !prev;
      try { localStorage.setItem('cc-sessions-show-history', String(next)); } catch { /* ignore */ }
      return next;
    });
  }, []);

  // This sidebar is a CONVERSATION switcher, so its history is conversations.
  // Before the Activity screen existed (2026-08-11 activity-coverage spec) the
  // toggle was the only place any closed session appeared, so it showed all of
  // them: 565 of 601 rows on the Pi were `oneshot` machine runs — 423 from
  // inbox-triage alone — and the list was unnavigable. Those runs live on
  // Activity → Runs now, which covers every session by construction, so
  // dropping them here is Gmail-archive semantics (out of the list, never out
  // of reach) rather than hiding them. A run WITHOUT a mode is kept: an
  // unknown shape must not vanish silently, which is the failure this whole
  // record exists to end.
  const { visibleSessions, historyCount } = useMemo(() => {
    const isHistory = (s: Session) =>
      TERMINAL_SESSION_STATUSES.has((s.status || '').toUpperCase())
      && !(isViewingChat && getSessionKey(s) === currentSession);
    const isMachineRun = (s: Session) => s.mode === 'oneshot';
    return {
      visibleSessions: sessions.filter(s =>
        !isHistory(s) || (showHistory && !isMachineRun(s))),
      historyCount: sessions.filter(s => isHistory(s) && !isMachineRun(s)).length,
    };
  }, [sessions, showHistory, currentSession, isViewingChat]);

  // Build tree and flatten for rendering
  const tree = useMemo(() => buildAgentSidebarTree(visibleSessions), [visibleSessions]);
  const flatNodes = useMemo(() => flattenTree(tree, expandedState), [tree, expandedState]);

  const handleSetDeleteTarget = useCallback((key: string, label: string) => {
    const targetNode = findNodeByKey(tree, key);
    setDeleteTarget({
      key,
      label,
      descendantCount: targetNode ? countDescendants(targetNode) : 0,
      isRootAgent: isTopLevelAgentSessionKey(key),
    });
  }, [tree]);

  return (
    <div className={compact ? 'flex flex-col max-h-[65vh]' : 'h-full flex flex-col min-h-0'}>
      <div className="panel-header border-l-[3px] border-l-info">
        <span className="panel-label text-info">
          <span className="panel-diamond">◆</span>
          AGENTS
        </span>
        <div className="ml-auto flex items-center gap-2">
          {onSpawn && (
            <button
              type="button"
              onClick={() => setSpawnOpen(true)}
              aria-label="Hire an agent or assign work"
              title="Hire an agent or assign work"
              className="shell-icon-button size-10 px-0"
            >
              <Plus size={16} />
            </button>
          )}
          <button
            type="button"
            onClick={toggleHistory}
            aria-pressed={showHistory}
            aria-label={showHistory ? 'Hide closed sessions' : 'Show closed sessions'}
            title={showHistory
              ? 'Hide closed sessions (history)'
              : `Show closed sessions${historyCount > 0 ? ` (${historyCount})` : ''}`}
            className="shell-icon-button size-10 px-0"
            data-active={showHistory ? 'true' : 'false'}
          >
            <History size={16} className={showHistory ? 'text-info' : undefined} />
          </button>
          <button
            type="button"
            onClick={onRefresh}
            aria-label="Refresh sessions"
            title="Refresh sessions"
            className="shell-icon-button size-10 px-0"
          >
            <RefreshCw size={16} aria-hidden="true" className={isLoading ? 'animate-spin' : undefined} />
          </button>
        </div>
      </div>
      <div className={compact ? 'overflow-y-auto' : 'flex-1 overflow-y-auto'}>
        {isLoading && flatNodes.length === 0 ? (
          <SessionSkeletonGroup count={4} />
        ) : flatNodes.length === 0 ? (
          <div className="text-muted-foreground px-3 py-2 text-[0.733rem]">No active sessions</div>
        ) : flatNodes.map((node) => {
          const sessionKey = node.key;
          const sessionType = getSessionType(sessionKey);
          const isSubagent = sessionType === 'subagent';
          const isCron = sessionType === 'cron';
          const isCronRun = sessionType === 'cron-run';
          const isRootAgent = isTopLevelAgentSessionKey(sessionKey);
          const label = getSessionDisplayLabel(node.session, agentName);
          const isGrowing = growingSessions[sessionKey] ?? false;
          const running = busyState[sessionKey] || node.session.state === 'running' || node.session.agentState === 'running' || node.session.busy || node.session.processing || node.session.status === 'running' || node.session.status === 'busy' || (isGrowing && isSubagent);
          const isActive = sessionKey === currentSession;
          const currentTokens = node.session.totalTokens || 0;
          const prevTokens = prevTokensRef.current[sessionKey] || 0;
          const displayTokens = Math.max(currentTokens, prevTokens);
          const isExpanded = expandedState[sessionKey] ?? !isCron;

          // Tier 3: this row is a clarification discussion the sweep flagged as
          // gone quiet. The chip names the task it blocks, so a lane quiet for
          // three days reads differently from one quiet for five minutes.
          // The stamp is CURRENT STATE, so a flag older than the session's last
          // movement has been overtaken — the lane spoke since and is no longer
          // known-stalled. Rendered inline in the existing row: no extra line,
          // no reorder, nothing the operator is reading moves.
          const flagged = stalledDiscussions?.[sessionKey.split(':').pop() ?? ''];
          const movedAt = parseMoment(node.session.updatedAt ?? node.session.lastActivity);
          // Report the elapsed time, do not round it UP to a day. An earlier
          // version clamped with Math.max(1, …), which is invisible at the
          // 24h default but makes every chip claim "1d" the moment
          // CC_DISCUSSION_STALL_HOURS is lowered — defeating the one thing the
          // chip is for, telling five minutes apart from three days.
          const stall = flagged && (movedAt === null || flagged.stalledAt >= movedAt)
            ? {
              taskTitle: flagged.taskTitle,
              waitingFor: formatWaiting(Date.now() - (movedAt ?? flagged.stalledAt)),
            }
            : null;

          // The agent opened this lane and is resting in AWAITING_OPERATOR:
          // it asked, and is waiting. `openedBy` is DERIVED by the gateway
          // from the OPEN operator_item that points at the session, so the
          // chip clears the moment the ask is answered — no client guessing.
          const asked = node.session.openedBy === 'agent'
            && node.session.status === 'AWAITING_OPERATOR'
            ? {
              taskTitle: node.session.blockedTaskTitle ?? null,
              // Same no-round-up formatter as the stall chip.
              waitingFor: formatWaiting(Date.now() - (movedAt ?? Date.now())),
            }
            : null;

          return (
            <SessionNode
              key={sessionKey}
              node={node}
              isActive={isActive}
              isGrowing={isGrowing}
              running={running}
              displayTokens={displayTokens}
              label={label}
              isExpanded={isExpanded}
              hasChildren={node.children.length > 0}
              isRootAgent={isRootAgent}
              isSubagent={isSubagent}
              isCron={isCron}
              isCronRun={isCronRun}
              isUnread={unreadSessions?.[sessionKey] ?? false}
              isRenaming={renamingKey === sessionKey}
              renameValue={renameValue}
              renameInputRef={renameInputRef}
              granularStatus={agentStatus?.[sessionKey]}
              stall={stall}
              asked={asked}
              onSelect={onSelect}
              onToggleExpand={handleToggleExpand}
              onDelete={onDelete ? handleSetDeleteTarget : undefined}
              onNewSession={onNewSession}
              onStartRename={onRename ? startRename : undefined}
              onAbort={onAbort}
              onRenameChange={handleRenameChange}
              onRenameCommit={commitRename}
              onRenameCancel={cancelRename}
              compact={compact}
            />
          );
        })}
      </div>

      {/* Close confirmation dialog — Central Command semantics: closing is a
          status flip with an event record; the session and its transcript
          stay in history forever. Nothing is ever deleted. */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open && !deleting) { setDeleteTarget(null); setDeleteError(null); } }}>
        <DialogContent className="bg-card border-border max-w-md">
          <DialogHeader>
            <DialogTitle className="text-red font-mono text-sm tracking-wider uppercase flex items-center gap-2">
              <AlertTriangle size={16} />
              Close Session
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-xs">
              {deleteTarget?.isRootAgent
                ? 'This closes the agent\'s current open session (kept in history — sessions are never deleted). The agent itself stays on the roster; retire it from the Agents page if it should leave the team.'
                : 'The session closes and stays in history with its full transcript — sessions are never deleted. The agent\'s next message starts a fresh one.'}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <div className="bg-background border border-border/60 px-3 py-2">
              <p className="text-[0.733rem] text-muted-foreground uppercase tracking-wider mb-1">Session:</p>
              <p className="text-[0.8rem] text-foreground font-mono">{deleteTarget?.label}</p>
              <p className="text-[0.667rem] text-muted-foreground font-mono mt-1 break-all">{deleteTarget?.key}</p>
            </div>
            {deleteError && (
              <div className="mt-2 border border-red/40 bg-red/10 px-3 py-2 text-[0.733rem] text-red break-words">
                Close failed — the session is unchanged: {deleteError}
              </div>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={deleting}
              className="font-mono text-xs"
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="font-mono text-xs bg-red text-foreground hover:bg-red/90"
            >
              {deleting ? 'Closing...' : 'Close Session'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Session creation dialog */}
      {onSpawn && (
        <SpawnAgentDialog
          open={spawnOpen}
          onOpenChange={setSpawnOpen}
          onSpawn={onSpawn}
        />
      )}
    </div>
  );
}
