import { useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { useSessionContext } from '@/contexts/SessionContext';
import { useChat } from '@/contexts/ChatContext';

/**
 * Live-transcript bridge: Central Command's gateway forwards every durable-log
 * event as a cc.* push. When the session the operator is watching emits a
 * step (the runtime persists its transcript per step and announces
 * session.step), reload its history so the transcript grows in place.
 *
 * Step-granular by design — the durable log is the source of truth, so a
 * missed push costs one reload, never data. Token-level streaming can layer
 * on later without changing this contract.
 */
export function CcLiveBridge() {
  const { subscribe } = useGateway();
  const { currentSession } = useSessionContext();
  const { loadHistory } = useChat();

  const stateRef = useRef({ currentSession, loadHistory });
  useEffect(() => {
    stateRef.current = { currentSession, loadHistory };
  }, [currentSession, loadHistory]);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const unsub = subscribe((msg) => {
      // conversation.ended included: closing a session (from the panel row or
      // another view) must clear/reload the watched pane — a closed lane's
      // alias resolves to empty history, so the pane can't read as
      // "still continuing" after a close.
      if (
        msg.event !== 'cc.session.step'
        && msg.event !== 'cc.session.completed'
        && msg.event !== 'cc.conversation.ended'
      ) return;
      const evt = msg.payload as { ref_id?: string; payload?: { agent_id?: string } } | undefined;
      const refId = evt?.ref_id;
      const agentId = evt?.payload?.agent_id;
      const { currentSession: session } = stateRef.current;
      // Direct key "agent:<agent_id>:<session_id>", or an alias key like
      // "agent:<agent_id>:main" (spawn flow) that tracks the agent's latest
      // conversation — match either.
      const direct = Boolean(refId) && session.endsWith(`:${refId}`);
      const alias = !session.includes(':sess_') && Boolean(agentId) && session.startsWith(`agent:${agentId}:`);
      if (!direct && !alias) return;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        void stateRef.current.loadHistory(stateRef.current.currentSession);
      }, 500);
    });
    return () => {
      unsub();
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [subscribe]);

  return null;
}
