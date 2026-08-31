/* eslint-disable react-refresh/only-export-components -- hook intentionally co-located with provider */
import { createContext, useContext, useCallback, useRef, useEffect, useState, useMemo, type ReactNode } from 'react';
import { useGateway } from './GatewayContext';
import { useSettings } from './SettingsContext';
import { getSessionKey, type Session, type AgentLogEntry, type EventEntry, type GatewayEvent, type EventPayload, type AgentEventPayload, type ChatEventPayload, type ContentBlock, type SessionsListResponse, type ChatHistoryResponse, type ChatMessage, type GranularAgentState } from '@/types';
import { playPing } from '@/features/voice/audio-feedback';
import { describeToolUse } from '@/utils/helpers';
import {
  buildAgentRootSessionKey,
  extractIdentityName,
  getAgentRegistrationName,
  getRootAgentSessionKey,
  getSessionDisplayLabel,
  getTopLevelAgentSessions,
  isSubagentSessionKey,
  isTopLevelAgentSessionKey,
  isCentralCommandSessionKey,
  pickDefaultSessionKey,
  getRootAgentId,
} from '@/features/sessions/sessionKeys';

const BUSY_STATES = new Set(['running', 'thinking', 'tool_use', 'delta', 'started']);
const IDLE_STATES = new Set(['idle', 'done', 'error', 'final', 'aborted', 'completed']);

// Use the full session list for the sidebar so older root chats stay visible.
const FULL_SESSIONS_LIMIT = 1000;
const MAIN_SESSION_KEY = 'agent:main:main';
const SESSIONS_SPAWNED_LIMIT = 500;

export type SubagentCleanupMode = 'keep' | 'delete';

export interface SpawnSessionOpts {
  kind: 'root' | 'subagent';
  task: string;
  model?: string;
  thinking?: string;
  label?: string;
  cleanup?: SubagentCleanupMode;
  agentName?: string;
  parentSessionKey?: string;
}

interface SessionContextValue {
  sessions: Session[];
  sessionsLoading: boolean;
  currentSession: string;
  setCurrentSession: (key: string) => void;
  /** Whether the chat view is the one on screen right now — "currently
   *  viewing a session" means this AND currentSession, never currentSession
   *  alone (App owns the view switch; this mirrors it in). */
  isViewingChat: boolean;
  setViewingChat: (active: boolean) => void;
  busyState: Record<string, boolean>;
  agentStatus: Record<string, GranularAgentState>;
  unreadSessions: Record<string, boolean>;
  markSessionRead: (key: string) => void;
  abortSession: (sessionKey: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  deleteSession: (sessionKey: string) => Promise<void>;
  openNewSession: (rootOrAgentKey: string) => Promise<void>;
  spawnSession: (opts: SpawnSessionOpts) => Promise<void>;
  updateSession: (sessionKey: string, updates: Partial<Session>) => void;
  agentLogEntries: AgentLogEntry[];
  eventEntries: EventEntry[];
  agentName: string;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * Which session keys carry per-session UNREAD.
 *
 * `isTopLevelAgentSessionKey` alone was the whole bug: it matches only
 * `agent:<id>:main`, and every REAL Central Command lane is `agent:<id>:sess_*`
 * (`nerve_gateway._session_key`), so per-session unread had never once fired
 * on a real row. Widened here rather than in the predicate itself, because
 * `isTopLevelAgentSessionKey` is load-bearing for the session tree, the
 * default-session picker and the model fallback in GatewayContext.
 */
function marksUnread(sessionKey: string): boolean {
  return isTopLevelAgentSessionKey(sessionKey) || isCentralCommandSessionKey(sessionKey);
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const { connectionState, rpc, subscribe } = useGateway();
  const { soundEnabled } = useSettings();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [currentSession, setCurrentSessionRaw] = useState('');
  // Defaults true because the app boots into the chat view; App corrects it
  // via setViewingChat on the first viewMode effect regardless.
  const [isViewingChat, setViewingChat] = useState(true);
  const isViewingChatRef = useRef(isViewingChat);
  useEffect(() => {
    isViewingChatRef.current = isViewingChat;
  }, [isViewingChat]);
  const [agentLogEntries, setAgentLogEntries] = useState<AgentLogEntry[]>([]);
  const [eventEntries, setEventEntries] = useState<EventEntry[]>([]);
  const [agentStatus, setAgentStatus] = useState<Record<string, GranularAgentState>>({});
  const [agentName, setAgentName] = useState('Agent');
  const [defaultAgentWorkspaceRoot, setDefaultAgentWorkspaceRoot] = useState<string | null>(null);
  const [rootIdentityNames, setRootIdentityNames] = useState<Record<string, string>>({});
  const [rootIdentityMisses, setRootIdentityMisses] = useState<Record<string, true>>({});
  const [unreadSessionKeys, setUnreadSessionKeys] = useState<Set<string>>(new Set());
  const unreadSessionKeysRef = useRef(unreadSessionKeys);
  const soundEnabledRef = useRef(soundEnabled);
  const logStateRef = useRef<Record<string, boolean>>({});
  const toolSeenRef = useRef<Map<string, number>>(new Map());
  const doneTimeoutsRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const delayedRefreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Derive busyState from agentStatus for backward compatibility
  const busyState = useMemo(() => {
    const result: Record<string, boolean> = {};
    for (const [key, state] of Object.entries(agentStatus)) {
      result[key] = state.status !== 'IDLE' && state.status !== 'DONE';
    }
    return result;
  }, [agentStatus]);
  
  // Derive unreadSessions as a stable Record<string, boolean> for consumers
  const unreadSessions = useMemo(() => {
    const result: Record<string, boolean> = {};
    for (const key of unreadSessionKeys) {
      result[key] = true;
    }
    return result;
  }, [unreadSessionKeys]);

  useEffect(() => {
    unreadSessionKeysRef.current = unreadSessionKeys;
  }, [unreadSessionKeys]);

  useEffect(() => {
    soundEnabledRef.current = soundEnabled;
  }, [soundEnabled]);

  const markSessionRead = useCallback((key: string) => {
    if (!unreadSessionKeysRef.current.has(key)) return;
    const next = new Set(unreadSessionKeysRef.current);
    next.delete(key);
    unreadSessionKeysRef.current = next;
    setUnreadSessionKeys(next);
  }, []);

  const setCurrentSession = useCallback((key: string) => {
    currentSessionRef.current = key;
    setCurrentSessionRaw(key);
    markSessionRead(key);
  }, [markSessionRead]);

  const fetchHiddenCronSessions = useCallback(async (activeMinutes: number, limit: number): Promise<Session[]> => {
    try {
      const params = new URLSearchParams({
        activeMinutes: String(activeMinutes),
        limit: String(limit),
      });
      const res = await fetch(`/api/sessions/hidden?${params.toString()}`);
      if (!res.ok) return [];
      const data = await res.json() as { ok?: boolean; sessions?: Session[] };
      return Array.isArray(data.sessions) ? data.sessions : [];
    } catch {
      return [];
    }
  }, []);

  const mergeSessionLists = useCallback((primary: Session[], supplemental: Session[]): Session[] => {
    if (supplemental.length === 0) return primary;
    const merged = [...primary];
    const seen = new Set(primary.map(getSessionKey));
    for (const session of supplemental) {
      const key = getSessionKey(session);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push(session);
    }
    return merged;
  }, []);

  // Fetch agent name from server-info on mount
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch('/api/server-info', { signal: controller.signal });
        if (!res.ok) return;
        const data = await res.json() as { agentName?: string; defaultAgentWorkspaceRoot?: string | null };
        if (data.agentName) {
          setAgentName(data.agentName);
        }
        setDefaultAgentWorkspaceRoot(
          typeof data.defaultAgentWorkspaceRoot === 'string' && data.defaultAgentWorkspaceRoot.trim()
            ? data.defaultAgentWorkspaceRoot
            : null,
        );
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') {
          // silent fail - use default
        }
      }
    })();
    return () => controller.abort();
  }, []);
  const sessionsRef = useRef(sessions);
  
  // Update refs in effect to avoid render-time mutations
  useEffect(() => {
    const rootAgentIds = Array.from(new Set(
      sessions
        .map((session) => getSessionKey(session))
        .filter(isTopLevelAgentSessionKey)
        .map((sessionKey) => getRootAgentId(sessionKey))
        .filter((rootId): rootId is string => Boolean(rootId) && rootId !== 'main'),
    )).filter((rootId) => !rootIdentityNames[rootId] && !rootIdentityMisses[rootId]);

    if (rootAgentIds.length === 0) return;

    const controller = new AbortController();
    void Promise.all(rootAgentIds.map(async (rootId) => {
      try {
        const params = new URLSearchParams({ agentId: rootId });
        const res = await fetch(`/api/workspace/identity?${params.toString()}`, { signal: controller.signal });
        // A failed fetch caches as a miss too: returning null left the agent in
        // neither map, so every sessions update refetched it — on 2026-08-17
        // that was dozens of doomed agents.files.get RPCs per second against a
        // gateway that doesn't serve workspace files.
        if (!res.ok) return { rootId, kind: 'miss' as const };
        const data = await res.json() as { ok?: boolean; content?: string };
        const identityName = typeof data.content === 'string' ? extractIdentityName(data.content) : null;
        if (identityName) return { rootId, identityName, kind: 'hit' as const };
        return { rootId, kind: 'miss' as const };
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return null;
        return null;
      }
    })).then((results) => {
      if (controller.signal.aborted) return;
      const resolved = results.filter((result): result is NonNullable<typeof result> => Boolean(result));
      if (resolved.length === 0) return;

      setRootIdentityMisses((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const result of resolved) {
          if (result.kind === 'hit') {
            if (!next[result.rootId]) continue;
            delete next[result.rootId];
            changed = true;
            continue;
          }
          if (next[result.rootId]) continue;
          next[result.rootId] = true;
          changed = true;
        }
        return changed ? next : prev;
      });

      setRootIdentityNames((prev) => {
        const next = { ...prev };
        let changed = false;
        for (const result of resolved) {
          if (result.kind !== 'hit') continue;
          const { rootId, identityName } = result;
          if (next[rootId] === identityName) continue;
          next[rootId] = identityName;
          changed = true;
        }
        return changed ? next : prev;
      });
    });

    return () => controller.abort();
  }, [rootIdentityMisses, rootIdentityNames, sessions]);

  const displaySessions = useMemo(() => sessions.map((session) => {
    const sessionKey = getSessionKey(session);
    if (!isTopLevelAgentSessionKey(sessionKey)) return session;

    const rootId = getRootAgentId(sessionKey);
    if (!rootId) return session;

    const identityName = rootId === 'main' ? agentName : rootIdentityNames[rootId];
    if (rootId !== 'main' && !identityName) {
      if (!session.identityName) return session;
      const rest = { ...session };
      delete rest.identityName;
      return rest;
    }
    if (!identityName || session.identityName === identityName) return session;
    return { ...session, identityName };
  }), [agentName, rootIdentityNames, sessions]);

  useEffect(() => {
    sessionsRef.current = displaySessions;
  }, [displaySessions]);
  
  const currentSessionRef = useRef(currentSession);
  useEffect(() => {
    currentSessionRef.current = currentSession;
  }, [currentSession]);

  const isCurrentlyViewing = (sessionKey: string) =>
    isViewingChatRef.current && currentSessionRef.current === sessionKey;

  const markSessionUnread = useCallback((sessionKey: string) => {
    if (!sessionKey || isCurrentlyViewing(sessionKey) || unreadSessionKeysRef.current.has(sessionKey)) return;
    const next = new Set(unreadSessionKeysRef.current);
    next.add(sessionKey);
    unreadSessionKeysRef.current = next;
    setUnreadSessionKeys(next);
  }, []);

  const pingSession = useCallback((sessionKey: string) => {
    if (!sessionKey || isCurrentlyViewing(sessionKey) || !soundEnabledRef.current) return;
    playPing();
  }, []);

  const listAuthoritativeSessions = useCallback(async () => {
    if (connectionState !== 'connected') return sessionsRef.current;
    try {
      const [res, hiddenCronSessions] = await Promise.all([
        rpc('sessions.list', { limit: FULL_SESSIONS_LIMIT }) as Promise<SessionsListResponse>,
        fetchHiddenCronSessions(24 * 60, FULL_SESSIONS_LIMIT),
      ]);

      const baseSessions = mergeSessionLists(res?.sessions ?? [], hiddenCronSessions);
      const spawnedByRoots = new Set<string>([MAIN_SESSION_KEY]);
      for (const rootSession of getTopLevelAgentSessions(baseSessions)) {
        spawnedByRoots.add(getSessionKey(rootSession));
      }

      // Keep active child sessions visible even when the full sessions.list
      // result lags behind the recent spawn/discovery flow.
      const spawnedSessionLists = await Promise.all(
        [...spawnedByRoots].map(async (rootSessionKey) => {
          try {
            const spawnedRes = await rpc('sessions.list', { spawnedBy: rootSessionKey, limit: SESSIONS_SPAWNED_LIMIT }) as SessionsListResponse;
            return spawnedRes?.sessions ?? [];
          } catch (err) {
            console.debug('[SessionContext] Failed to fetch spawned sessions for root:', rootSessionKey, err);
            return [];
          }
        }),
      );

      return spawnedSessionLists.reduce(
        (acc, spawnedSessions) => mergeSessionLists(acc, spawnedSessions),
        baseSessions,
      );
    } catch (err) {
      console.debug('[SessionContext] Failed to fetch authoritative session list:', err);
      return sessionsRef.current;
    }
  }, [connectionState, fetchHiddenCronSessions, mergeSessionLists, rpc]);

  const setGranularStatus = useCallback((sessionKey: string, state: GranularAgentState) => {
    if (!sessionKey) return;
    // Cancel any pending DONE→IDLE timeout for this session
    if (doneTimeoutsRef.current[sessionKey]) {
      clearTimeout(doneTimeoutsRef.current[sessionKey]);
      delete doneTimeoutsRef.current[sessionKey];
    }
    // If transitioning to DONE, schedule auto-transition to IDLE after 3s
    if (state.status === 'DONE') {
      // Mark subagent sessions as unread when they complete (unless currently viewing)
      if (isSubagentSessionKey(sessionKey)) {
        markSessionUnread(sessionKey);
      }
      doneTimeoutsRef.current[sessionKey] = setTimeout(() => {
        setAgentStatus(prev => {
          const current = prev[sessionKey];
          // Only transition if still in DONE state
          if (!current || current.status !== 'DONE') return prev;
          return { ...prev, [sessionKey]: { status: 'IDLE', since: Date.now() } };
        });
        delete doneTimeoutsRef.current[sessionKey];
      }, 3000);
    }
    setAgentStatus(prev => {
      const existing = prev[sessionKey];
      // Optimization: skip update if status/tool haven't changed
      if (existing && existing.status === state.status && existing.toolName === state.toolName) return prev;
      return { ...prev, [sessionKey]: state };
    });
  }, [markSessionUnread]);

  const shouldLogTool = useCallback((toolId: string) => {
    if (!toolId) return false;
    const now = Date.now();
    const map = toolSeenRef.current;
    const DEDUP_MS = 5 * 60 * 1000;
    const last = map.get(toolId);
    if (last && now - last < DEDUP_MS) return false;
    map.set(toolId, now);
    // Prune expired entries when map grows too large
    if (map.size > 500) {
      for (const [key, ts] of map) {
        if (now - ts > DEDUP_MS) map.delete(key);
      }
    }
    return true;
  }, []);

  const addAgentLogEntry = useCallback((icon: string, text: string) => {
    const entry: AgentLogEntry = { icon, text, ts: Date.now() };
    setAgentLogEntries(prev => [entry, ...prev].slice(0, 100));
    fetch('/api/agentlog', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry)
    }).catch(() => {});
  }, []);

  const friendlyName = useCallback((sk: string) => {
    if (!sk) return 'unknown';
    const sess = sessionsRef.current.find(s => getSessionKey(s) === sk);
    if (sess) return getSessionDisplayLabel(sess, agentName);
    if (isSubagentSessionKey(sk)) return 'sub-agent ' + sk.split(':').pop()?.slice(0, 8);
    return sk.split(':').pop() || sk;
  }, [agentName]);

  const rpcRef = useRef(rpc);
  
  useEffect(() => {
    rpcRef.current = rpc;
  }, [rpc]);

  const streamFrameLogRef = useRef(0);

  const addEvent = useCallback((msg: GatewayEvent) => {
    // Durable-log events arrive prefixed "cc.<kind>" (nerve_gateway.py's
    // _forward_events); native gateway events (chat/agent/cron/connect.challenge)
    // never carry the prefix. Strip it before categorizing so both land the
    // same way instead of falling through to SYSTEM.
    const evt = (msg.event || 'response').replace(/^cc\./, '');
    const p = (msg.payload || {}) as EventPayload;

    // Streaming frames arrive once per token, and every setEventEntries call
    // is a new context value that re-renders every useSessionContext()
    // consumer — composer included. One "Response streaming" entry per 5s
    // carries the same information as five hundred.
    const isStreamFrame =
      (evt === 'chat' && p.state === 'delta') ||
      (evt === 'agent' && (p as AgentEventPayload).stream === 'assistant');
    if (isStreamFrame) {
      const now = Date.now();
      if (now - streamFrameLogRef.current < 5000) return;
      streamFrameLogRef.current = now;
    }

    const chatStateDescs: Record<string, string> = {
      delta: 'Response streaming', final: 'Response complete',
      error: 'Chat error', aborted: 'Response aborted',
    };

    let badge = 'SYSTEM', badgeCls = 'badge-system', desc = evt;

    // Domain segment -> existing badge category (no dedicated badge per
    // domain; these are the closest fit among CHAT/AGENT/CRON/SYSTEM/ERROR).
    const domainBadge: Record<string, ['CHAT' | 'AGENT' | 'CRON', 'badge-chat' | 'badge-agent' | 'badge-cron']> = {
      session: ['CHAT', 'badge-chat'],
      proposal: ['AGENT', 'badge-agent'],
      task: ['AGENT', 'badge-agent'],
      work: ['CRON', 'badge-cron'],
      heartbeat: ['CRON', 'badge-cron'],
      feed: ['CRON', 'badge-cron'],
    };

    if (evt.startsWith('chat')) {
      badge = 'CHAT'; badgeCls = 'badge-chat';
      desc = chatStateDescs[p.state || ''] || (p.sessionKey ? 'Message from ' + p.sessionKey : 'Chat event');
    } else if (evt.startsWith('agent')) {
      badge = 'AGENT'; badgeCls = 'badge-agent';
      const ap = p as AgentEventPayload;
      if (ap.stream === 'lifecycle') {
        const phase = String((ap.data as Record<string, unknown> | undefined)?.phase || '');
        desc = 'Agent lifecycle: ' + (phase || 'unknown');
      } else if (ap.stream === 'assistant') {
        desc = 'Agent assistant output';
      } else {
        const state = p.state || p.agentState || '';
        desc = state ? 'Agent state: ' + state : 'Agent event';
      }
    } else if (evt.startsWith('cron')) {
      badge = 'CRON'; badgeCls = 'badge-cron';
      desc = p.name ? 'Cron job: ' + p.name : 'Cron job triggered';
    } else if (evt === 'connect.challenge') {
      desc = 'Connection challenge received';
    } else if (evt.startsWith('presence')) {
      desc = 'Presence update';
    } else if (evt.includes('error')) {
      badge = 'ERROR'; badgeCls = 'badge-error';
      desc = (typeof p.message === 'string' ? p.message : p.error) || 'Error occurred';
    } else if (domainBadge[evt.split('.')[0]]) {
      [badge, badgeCls] = domainBadge[evt.split('.')[0]];
      desc = evt;
    }

    setEventEntries(prev => [{ badge, badgeCls, desc, ts: new Date() }, ...prev].slice(0, 50));
  }, []);

  const feedAgentLog = useCallback((evt: string, p: EventPayload) => {
    const sk = p.sessionKey || '';
    const name = friendlyName(sk);
    const isSubagent = isSubagentSessionKey(sk);
    const isMain = isTopLevelAgentSessionKey(sk);

    const processToolBlocks = (blocks: ContentBlock[]) => {
      for (const block of blocks) {
        if (block.type !== 'tool_use' && block.type !== 'toolCall') continue;
        if (!block.name) continue;
        let toolInput: Record<string, unknown> = typeof block.input === 'object' && block.input ? block.input : {};
        if (!toolInput || Object.keys(toolInput).length === 0) {
          const args = block.arguments;
          if (typeof args === 'string') {
            try { toolInput = JSON.parse(args); } catch { toolInput = {}; }
          } else if (typeof args === 'object' && args) {
            toolInput = args;
          }
        }
        const toolId = String(block.id || block.toolCallId || block.name);
        if (shouldLogTool(toolId)) {
          const desc = describeToolUse(block.name, toolInput);
          if (desc) addAgentLogEntry('🔧', desc);
        }
      }
    };

    const processMessages = (msgs: ChatMessage[]) => {
      for (const m of msgs) {
        if (m.role === 'assistant' && Array.isArray(m.content)) {
          processToolBlocks(m.content as ContentBlock[]);
        }
      }
    };

    // Handle lifecycle events from CLI agents (Codex, Claude Code CLI)
    if (evt === 'agent') {
      const ap = p as AgentEventPayload;
      if (ap.stream === 'lifecycle') {
        const phase = (ap.data as Record<string, unknown> | undefined)?.phase;
        if (phase === 'start') {
          logStateRef.current['_conv_' + sk] = true;
          addAgentLogEntry(isMain ? '🧠' : '⚡', isMain ? 'thinking…' : isSubagent ? 'spawned ' + name : name + ' started');
        } else if (phase === 'end') {
          addAgentLogEntry(isMain ? '✦' : '✅', isMain ? 'finished response' : name + ' completed');
          delete logStateRef.current['_conv_' + sk];
        } else if (phase === 'error') {
          addAgentLogEntry('❌', isMain ? 'generation failed' : name + ' failed');
          delete logStateRef.current['_conv_' + sk];
        }
        return;
      }
    }

    if (evt === 'chat') {
      if ((p.state === 'delta' || p.state === 'started') && !logStateRef.current['_conv_' + sk]) {
        logStateRef.current['_conv_' + sk] = true;
        addAgentLogEntry(isMain ? '🧠' : '⚡', isMain ? 'thinking…' : isSubagent ? 'spawned ' + name : name + ' started');
      }
      if (Array.isArray(p.content)) processToolBlocks(p.content as ContentBlock[]);
      if (Array.isArray(p.messages)) processMessages(p.messages as ChatMessage[]);
      if (p.state === 'final') {
        if (sk && rpcRef.current) {
          rpcRef.current('chat.history', { sessionKey: sk, limit: 10 })
            .then((res: unknown) => processMessages((res as ChatHistoryResponse)?.messages || []))
            .catch(() => {});
        }
        addAgentLogEntry(isMain ? '✦' : '✅', isMain ? 'finished response' : name + ' completed');
        delete logStateRef.current['_conv_' + sk];
      } else if (p.state === 'error' || p.state === 'aborted') {
        const icon = p.state === 'error' ? '❌' : '⛔';
        const verb = p.state === 'error' ? 'failed' : 'aborted';
        addAgentLogEntry(icon, isMain ? (p.state === 'error' ? 'generation failed' : 'response aborted') : name + ' ' + verb);
        delete logStateRef.current['_conv_' + sk];
      }
    } else if (evt === 'cron') {
      addAgentLogEntry('⏰', 'cron: ' + (p.name || 'scheduled task fired'));
    } else if (evt === 'connect.challenge') {
      addAgentLogEntry('🔗', 'connected to gateway');
    } else if (evt.includes('error')) {
      addAgentLogEntry('❌', (typeof p.message === 'string' ? p.message : p.error) || 'something went wrong');
    }
  }, [addAgentLogEntry, friendlyName, shouldLogTool]);

  const refreshSessions = useCallback(async () => {
    if (connectionState !== 'connected') return;
    try {
      const newSessions = await listAuthoritativeSessions();
      const nextCurrentSession = pickDefaultSessionKey(newSessions, currentSessionRef.current);
      
      // Smart diffing: preserve object references for unchanged sessions.
      // This prevents unnecessary re-renders in child components.
      setSessions(prev => {
        // Fast path: if lengths differ, structure changed
        if (prev.length !== newSessions.length) return newSessions;
        
        // Create lookup for efficient comparison
        const prevMap = new Map(prev.map(s => [getSessionKey(s), s]));
        
        let hasChanges = false;
        const merged = newSessions.map(newSession => {
          const key = getSessionKey(newSession);
          const existing = prevMap.get(key);
          
          // If session doesn't exist in prev, it's new
          if (!existing) {
            hasChanges = true;
            return newSession;
          }
          
          // Compare every field the SERVER sent. This used to be a hand-written
          // allowlist that omitted `status`, and the one status-derived field it
          // did carry — `state` — is "idle" for every non-RUNNING status. So
          // AWAITING_HUMAN → DONE moved nothing the diff could see: the refresh
          // fetched the closed row, decided "unchanged", and kept the stale
          // object, leaving closed sessions rendering IDLE in the sidebar until
          // a page reload (2026-08-11, sess_d356c2c430ea sat visible after its
          // proposal was rejected). Keys absent from newSession — the
          // locally-stamped `lastActivity` — are ignored on purpose.
          const changed = (Object.keys(newSession) as (keyof Session)[])
            .some(field => existing[field] !== newSession[field]);
          
          if (changed) {
            hasChanges = true;
            return newSession;
          }
          
          // No change - keep the existing reference
          return existing;
        });
        
        // If nothing changed, return the same array reference
        return hasChanges ? merged : prev;
      });
      setCurrentSession(nextCurrentSession);
    } catch (err) {
      console.debug('[SessionContext] Failed to refresh sessions:', err);
    } finally {
      setSessionsLoading(false);
    }
  }, [connectionState, listAuthoritativeSessions, setCurrentSession]);

  const refreshSessionsRef = useRef(refreshSessions);
  useEffect(() => {
    refreshSessionsRef.current = refreshSessions;
  }, [refreshSessions]);

  // Update session in list from WebSocket event data
  const updateSessionFromEvent = useCallback((sessionKey: string, updates: Partial<Session>) => {
    setSessions(prev => {
      const idx = prev.findIndex(s => getSessionKey(s) === sessionKey);
      if (idx === -1) {
        // New session appeared that we don't have - schedule a refresh
        // Use setTimeout to avoid calling during render
        setTimeout(() => {
          void refreshSessionsRef.current();
        }, 100);
        return prev;
      }
      
      // Check if the update actually changes anything
      const existing = prev[idx];
      const hasChanges = Object.entries(updates).some(
        ([key, value]) => existing[key as keyof Session] !== value
      );
      
      // If nothing changed, return the same array reference
      if (!hasChanges) return prev;
      
      // Update only the changed session, preserving other references
      return prev.map((s, i) => {
        if (i !== idx) return s;
        return { ...s, ...updates, lastActivity: Date.now() };
      });
    });
  }, []);

  // Extract session updates (state + token data) from a typed agent event payload
  const extractSessionUpdates = useCallback((state: string | undefined, payload: AgentEventPayload | ChatEventPayload): Partial<Session> => {
    const updates: Partial<Session> = {};
    if (state) updates.state = state;
    if ('totalTokens' in payload && typeof payload.totalTokens === 'number') updates.totalTokens = payload.totalTokens;
    if ('contextTokens' in payload && typeof payload.contextTokens === 'number') updates.contextTokens = payload.contextTokens;
    return updates;
  }, []);

  const scheduleDelayedRefresh = useCallback(() => {
    if (delayedRefreshTimeoutRef.current) {
      clearTimeout(delayedRefreshTimeoutRef.current);
    }
    delayedRefreshTimeoutRef.current = setTimeout(() => {
      delayedRefreshTimeoutRef.current = null;
      void refreshSessionsRef.current();
    }, 1500);
  }, []);

  // Subscribe to gateway events for granular status tracking + session state sync + agent log + event log
  useEffect(() => {
    const unsub = subscribe((msg: GatewayEvent) => {
      const evt = msg.event;
      const p = (msg.payload || {}) as EventPayload;

      addEvent(msg);

      // Session granular status tracking + state sync from agent/chat events
      if ((evt === 'agent' || evt === 'chat') && p.sessionKey) {
        const sk = p.sessionKey;
        const typedPayload = evt === 'agent'
          ? (msg.payload || {}) as AgentEventPayload
          : (msg.payload || {}) as ChatEventPayload;

        // Handle lifecycle events from CLI agents (Codex, Claude Code CLI)
        if (evt === 'agent') {
          const ap = typedPayload as AgentEventPayload;

          if (ap.stream === 'lifecycle') {
            const phase = (ap.data as Record<string, unknown> | undefined)?.phase;
            if (phase === 'start') {
              setGranularStatus(sk, { status: 'THINKING', since: Date.now() });
            } else if (phase === 'end') {
              setGranularStatus(sk, { status: 'DONE', since: Date.now() });
              if (marksUnread(sk)) {
                markSessionUnread(sk);
                pingSession(sk);
              }
              refreshSessions();
              scheduleDelayedRefresh();
            } else if (phase === 'error') {
              setGranularStatus(sk, { status: 'ERROR', since: Date.now() });
              if (marksUnread(sk)) {
                markSessionUnread(sk);
                pingSession(sk);
              }
              refreshSessions();
            }
          } else if (ap.stream === 'tool' && ap.data) {
            if (ap.data.phase === 'start' && ap.data.name) {
              const toolDesc = describeToolUse(ap.data.name, ap.data.args || {});
              setGranularStatus(sk, {
                status: 'THINKING',
                toolName: ap.data.name,
                toolDescription: toolDesc || undefined,
                since: Date.now(),
              });
            } else if (ap.data.phase === 'result') {
              setGranularStatus(sk, { status: 'THINKING', since: Date.now() });
            }
          } else if (ap.stream === 'assistant') {
            setGranularStatus(sk, { status: 'STREAMING', since: Date.now() });
          }
        }

        // Handle chat events
        if (evt === 'chat') {
          const cp = typedPayload as ChatEventPayload;
          const state = cp.state || '';

          if (state === 'started') {
            setGranularStatus(sk, { status: 'THINKING', since: Date.now() });
            if (marksUnread(sk)) {
              markSessionUnread(sk);
            }
          } else if (state === 'delta') {
            setGranularStatus(sk, { status: 'STREAMING', since: Date.now() });
          } else if (state === 'final') {
            setGranularStatus(sk, { status: 'DONE', since: Date.now() });
            if (marksUnread(sk)) {
              markSessionUnread(sk);
              pingSession(sk);
            }
            refreshSessions();
            // Delayed refresh to catch token counts that may not be available immediately.
            scheduleDelayedRefresh();
          } else if (state === 'error') {
            setGranularStatus(sk, { status: 'ERROR', since: Date.now() });
            if (marksUnread(sk)) {
              markSessionUnread(sk);
              pingSession(sk);
            }
          } else if (state === 'aborted') {
            setGranularStatus(sk, { status: 'IDLE', since: Date.now() });
          }
        }

        // Also handle legacy state strings for backward compatibility
        const state = evt === 'agent'
          ? ((typedPayload as AgentEventPayload).state || (typedPayload as AgentEventPayload).agentState || '')
          : ((typedPayload as ChatEventPayload).state || '');

        // Map legacy state strings to granular status (only if not already handled above)
        if (evt === 'agent' && !(typedPayload as AgentEventPayload).stream) {
          if (BUSY_STATES.has(state)) {
            setGranularStatus(sk, { status: 'THINKING', since: Date.now() });
          } else if (IDLE_STATES.has(state)) {
            if (state === 'error') {
              setGranularStatus(sk, { status: 'ERROR', since: Date.now() });
            } else if (state === 'aborted') {
              setGranularStatus(sk, { status: 'IDLE', since: Date.now() });
            } else {
              setGranularStatus(sk, { status: 'DONE', since: Date.now() });
            }
            if (state === 'final' || state === 'done' || state === 'completed') {
              refreshSessions();
            }
          }
        }

        const updates = extractSessionUpdates(state || undefined, typedPayload);
        if (Object.keys(updates).length > 0) {
          updateSessionFromEvent(sk, updates);
        }
      }

      // Central Command session-lifecycle events: a close (from ANY surface) or a
      // session resolution must update the panel immediately — waiting for
      // the 30s poll leaves closed rows visibly "open" (2026-07-23).
      if (
        evt === 'cc.conversation.ended'
        || evt === 'cc.session.completed'
        || evt === 'cc.session.failed'
        || evt === 'cc.session.resume_failed'
        // Operator stop/resume flips session status (RUNNING <-> STOPPED) with
        // no other lifecycle event on this path — without these the panel row
        // reads the pre-stop/pre-resume state for up to one poll.
        || evt === 'cc.session.stopped'
        || evt === 'cc.session.resumed'
        // An agent opening its own lane, or asking, CREATES a row the operator
        // has never seen. Without these the lane took up to 30s (one poll) to
        // appear at all — the agent is blocked for that whole time and the
        // panel shows nothing.
        || evt === 'cc.agent.discussion_opened'
        || evt === 'cc.agent.asked'
        // A window park/grant flips the session status (AWAITING_CONTINUE and
        // back) with no other lifecycle event — without these the panel row
        // reads RUNNING for up to one poll while nothing is running.
        || evt === 'cc.session.window_exhausted'
        || evt === 'cc.session.continued'
        || evt === 'cc.session.window_declined'
        // The session rests OPEN with an exec failure attached — the badge
        // needs the row's execFailure field, not just the status flip.
        || evt === 'cc.session.exec_failed'
      ) {
        refreshSessions();
      }

      feedAgentLog(evt, p);
    });

    return () => {
      unsub();
    };
  }, [subscribe, addEvent, setGranularStatus, markSessionUnread, pingSession, feedAgentLog, updateSessionFromEvent, extractSessionUpdates, refreshSessions, scheduleDelayedRefresh]);

  useEffect(() => {
    return () => {
      for (const key of Object.keys(doneTimeoutsRef.current)) {
        clearTimeout(doneTimeoutsRef.current[key]);
      }
      doneTimeoutsRef.current = {};
      if (delayedRefreshTimeoutRef.current) {
        clearTimeout(delayedRefreshTimeoutRef.current);
        delayedRefreshTimeoutRef.current = null;
      }
    };
  }, []);

  // Poll sessions when connected (reduced to 30s - WebSocket events provide real-time updates)
  useEffect(() => {
    if (connectionState !== 'connected') return;
    refreshSessions();
    // Polling is now just a fallback for catching missed updates
    const iv = setInterval(() => refreshSessions(), 30000);
    return () => clearInterval(iv);
  }, [connectionState, refreshSessions]);

  // Load agent log on mount
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch('/api/agentlog', { signal: controller.signal });
        const entries: AgentLogEntry[] = await res.json();
        setAgentLogEntries(entries.slice().reverse().slice(0, 100));
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') {
          console.debug('[SessionContext] Failed to load agent log:', err.message);
        }
      }
    })();
    return () => controller.abort();
  }, []);

  const deleteSession = useCallback(async (sessionKey: string) => {
    // Central Command semantics: CLOSE, never delete — the session row and its
    // transcript stay in the panel as history. So no optimistic removal:
    // close on the server, then refresh from the source of truth (the row
    // comes back with its closed status).
    await rpc('sessions.delete', { key: sessionKey });

    if (unreadSessionKeysRef.current.has(sessionKey)) {
      const next = new Set(unreadSessionKeysRef.current);
      next.delete(sessionKey);
      unreadSessionKeysRef.current = next;
      setUnreadSessionKeys(next);
    }
    // Closing the session you're looking at jumps to the agent's root — the
    // next message there starts a fresh session. The closed one stays
    // selectable below it.
    const [, agentId] = sessionKey.split(':');
    if (sessionKey === currentSessionRef.current && agentId) {
      setCurrentSession(`agent:${agentId}:main`);
    }
    await refreshSessionsRef.current();
  }, [rpc, setCurrentSession]);

  // Open an ADDITIONAL session with an agent — everything already open stays
  // open (multi-session per agent). Selects the fresh session when it lands.
  const openNewSession = useCallback(async (rootOrAgentKey: string) => {
    const agentId = rootOrAgentKey.startsWith('agent:')
      ? rootOrAgentKey.split(':')[1]
      : rootOrAgentKey;
    const res = await rpc('sessions.new', { agentId }) as { sessionKey?: string };
    await refreshSessionsRef.current();
    if (res?.sessionKey) setCurrentSession(res.sessionKey);
  }, [rpc, setCurrentSession]);

  const spawnSession = useCallback(async (opts: SpawnSessionOpts) => {
    const authoritativeSessions = await listAuthoritativeSessions();

    if (opts.kind === 'root') {
      const rootName = opts.agentName?.trim();
      if (!rootName) throw new Error('Agent name is required');

      const sessionKey = buildAgentRootSessionKey(
        rootName,
        authoritativeSessions.map(getSessionKey),
      );
      // Register agent in config (ignore if already registered)
      const agentId = getRootAgentId(sessionKey);
      const registrationName = getAgentRegistrationName(rootName, sessionKey);
      const workspacePath = defaultAgentWorkspaceRoot
        ? `${defaultAgentWorkspaceRoot.replace(/\/+$/, '')}/${agentId}`
        : `~/.openclaw/workspace-${agentId}`;
      try {
        await rpc('agents.create', { name: registrationName, workspace: workspacePath });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (!msg.includes('already exists')) throw err;
      }
      const thinkingLevel = opts.thinking ?? null;

      await rpc('sessions.patch', {
        key: sessionKey,
        label: rootName,
        model: opts.model,
        thinkingLevel,
      });

      const idempotencyKey = `spawn-root-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await rpc('chat.send', {
        sessionKey,
        message: opts.task,
        deliver: false,
        idempotencyKey,
      });

      await refreshSessions();
      setCurrentSession(sessionKey);
      return;
    }

    const fallbackRootSession = getTopLevelAgentSessions(sessionsRef.current)[0];
    const parentSessionKey = opts.parentSessionKey
      || getRootAgentSessionKey(currentSessionRef.current)
      || (fallbackRootSession ? getSessionKey(fallbackRootSession) : '');
    if (!parentSessionKey) {
      throw new Error('Create a top-level agent before launching a subagent');
    }

    const res = await fetch('/api/sessions/spawn-subagent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        parentSessionKey,
        task: opts.task,
        label: opts.label,
        model: opts.model,
        thinking: opts.thinking,
        cleanup: opts.cleanup ?? 'keep',
      }),
    });

    const data = await res.json() as { ok: boolean; sessionKey?: string; error?: string };
    if (!data.ok || !data.sessionKey) {
      throw new Error(data.error ?? 'Failed to spawn subagent');
    }

    await refreshSessions();
    setCurrentSession(data.sessionKey);
  }, [defaultAgentWorkspaceRoot, listAuthoritativeSessions, rpc, refreshSessions, setCurrentSession]);

  const abortSession = useCallback(async (sessionKey: string) => {
    try {
      await rpc('chat.abort', { sessionKey });
    } catch (err) {
      console.error('[SessionContext] Failed to abort session:', err);
    }
  }, [rpc]);

  const value = useMemo<SessionContextValue>(() => ({
    sessions: displaySessions,
    sessionsLoading,
    currentSession,
    setCurrentSession,
    isViewingChat,
    setViewingChat,
    busyState,
    agentStatus,
    unreadSessions,
    markSessionRead,
    abortSession,
    refreshSessions,
    deleteSession,
    openNewSession,
    spawnSession,
    updateSession: updateSessionFromEvent,
    agentLogEntries,
    eventEntries,
    agentName,
  }), [
    displaySessions, sessionsLoading, currentSession, setCurrentSession, isViewingChat, setViewingChat, busyState, agentStatus,
    unreadSessions, markSessionRead,
    abortSession, refreshSessions, deleteSession, openNewSession, spawnSession,
    updateSessionFromEvent, agentLogEntries, eventEntries, agentName,
  ]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSessionContext must be used within SessionProvider');
  return ctx;
}
