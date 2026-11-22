/* eslint-disable react-refresh/only-export-components -- hook intentionally co-located with provider */
import { createContext, useContext, useCallback, useRef, useEffect, useState, useMemo, type ReactNode } from 'react';
import { useGateway } from './GatewayContext';
import { useSettings } from './SettingsContext';
import { getSessionKey, type Session, type GatewayEvent, type EventPayload, type AgentEventPayload, type ChatEventPayload, type SessionsListResponse, type GranularAgentState } from '@/types';
import { playPing } from '@/features/voice/audio-feedback';
import { describeToolUse } from '@/utils/helpers';
import {
  getTopLevelAgentSessions,
  isSubagentSessionKey,
  isTopLevelAgentSessionKey,
  isCentralCommandSessionKey,
  pickDefaultSessionKey,
} from '@/features/sessions/sessionKeys';

const BUSY_STATES = new Set(['running', 'thinking', 'tool_use', 'delta', 'started']);
const IDLE_STATES = new Set(['idle', 'done', 'error', 'final', 'aborted', 'completed']);

// Use the full session list for the sidebar so older root chats stay visible.
const FULL_SESSIONS_LIMIT = 1000;
const MAIN_SESSION_KEY = 'agent:main:main';
const SESSIONS_SPAWNED_LIMIT = 500;

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
  /** Rename a conversation (an empty label clears the name). Throws the server's refusal. */
  renameSession: (sessionKey: string, label: string) => Promise<void>;
  openNewSession: (rootOrAgentKey: string) => Promise<void>;
  updateSession: (sessionKey: string, updates: Partial<Session>) => void;
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
  const [agentStatus, setAgentStatus] = useState<Record<string, GranularAgentState>>({});
  const [agentName, setAgentName] = useState('Agent');
  const [unreadSessionKeys, setUnreadSessionKeys] = useState<Set<string>>(new Set());
  const unreadSessionKeysRef = useRef(unreadSessionKeys);
  const soundEnabledRef = useRef(soundEnabled);
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
        const data = await res.json() as { agentName?: string };
        if (data.agentName) {
          setAgentName(data.agentName);
        }
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') {
          // silent fail - use default
        }
      }
    })();
    return () => controller.abort();
  }, []);

  // An agent's display name is its roster name: `sessions.list` sends one root
  // row per roster agent (`agent:<id>:main`) whose `label` IS that name
  // (`nerve_gateway._sessions_list`), and getSessionDisplayLabel shows `label`,
  // falling back to the agent id. Nothing is read from workspace files.
  const sessionsRef = useRef(sessions);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);
  
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
      const res = await rpc('sessions.list', { limit: FULL_SESSIONS_LIMIT }) as SessionsListResponse;

      const baseSessions = res?.sessions ?? [];
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
  }, [connectionState, mergeSessionLists, rpc]);

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

  // Subscribe to gateway events for granular status tracking + session state sync
  useEffect(() => {
    const unsub = subscribe((msg: GatewayEvent) => {
      const evt = msg.event;
      const p = (msg.payload || {}) as EventPayload;

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
        // A re-run writes the link on the old row and starts a new one.
        || evt === 'cc.session.rerun'
        // A rename in another tab or window.
        || evt === 'cc.session.renamed'
      ) {
        refreshSessions();
      }
    });

    return () => {
      unsub();
    };
  }, [subscribe, setGranularStatus, markSessionUnread, pingSession, updateSessionFromEvent, extractSessionUpdates, refreshSessions, scheduleDelayedRefresh]);

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

  // Rename a conversation: the gateway stores the operator's name on the row
  // (display only; the transcript is untouched), then the list is re-read so
  // the label comes from the source of truth.
  const renameSession = useCallback(async (sessionKey: string, label: string) => {
    await rpc('sessions.patch', { key: sessionKey, label });
    await refreshSessionsRef.current();
  }, [rpc]);

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

  const abortSession = useCallback(async (sessionKey: string) => {
    try {
      await rpc('chat.abort', { sessionKey });
    } catch (err) {
      console.error('[SessionContext] Failed to abort session:', err);
    }
  }, [rpc]);

  const value = useMemo<SessionContextValue>(() => ({
    sessions,
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
    renameSession,
    openNewSession,
    updateSession: updateSessionFromEvent,
    agentName,
  }), [
    sessions, sessionsLoading, currentSession, setCurrentSession, isViewingChat, setViewingChat, busyState, agentStatus,
    unreadSessions, markSessionRead,
    abortSession, refreshSessions, deleteSession, renameSession, openNewSession,
    updateSessionFromEvent, agentName,
  ]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSessionContext must be used within SessionProvider');
  return ctx;
}
