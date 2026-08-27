/** Possible high-level states an agent session can be in. */
export type AgentStatusKind = 'IDLE' | 'THINKING' | 'STREAMING' | 'DONE' | 'ERROR';

/** Fine-grained agent state including current tool activity. */
export interface GranularAgentState {
  status: AgentStatusKind;
  toolName?: string;        // e.g., "read", "exec", "web_search"
  toolDescription?: string; // e.g., "Reading src/types.ts"
  since: number;            // Date.now() of last state change
}

/** A gateway session (main agent or sub-agent). Fields are optional due to API version variance. */
export interface Session {
  sessionKey?: string;
  key?: string;
  id?: string;
  label?: string;
  identityName?: string;
  state?: string;
  agentState?: string;
  busy?: boolean;
  processing?: boolean;
  status?: string;
  lastActivity?: string | number;
  updatedAt?: number;
  abortedLastRun?: boolean;
  model?: string;
  thinking?: string;
  thinkingLevel?: string;
  totalTokens?: number;
  contextTokens?: number;
  parentSessionKey?: string; // from gateway API (newer session row shape)
  parentId?: string;  // from gateway API (v2026.2.9+)
  inputTokens?: number;
  outputTokens?: number;
  channel?: string;
  kind?: string;
  displayName?: string;
  mode?: string; // Central Command: 'conversation' | 'oneshot' — conversation rows are closable
  conversational?: boolean; // Central Command roots: agent has a conversational lane (shows the + action)
  /** Central Command: which roster agent this session belongs to. */
  agentId?: string;
  /** Central Command: WHO started this conversation. An 'agent' lane is one the
   *  operator never asked for — the agent needed something. Derived by the
   *  gateway from the OPEN operator_item that points at the session. */
  openedBy?: 'agent' | 'operator';
  /** Central Command: the task the asking agent is blocked on, when there is one. */
  blockedTaskTitle?: string | null;
  /** Central Command: the email this lane was spawned to handle (dispatcher-claimed
   *  work_item), when there is one. Same link chat.history's sourceEmail uses. */
  sourceEmail?: SourceEmail | null;
  /** Central Command: non-null means this run's approved proposal FAILED execution
   *  and the session is resting OPEN (AWAITING_OPERATOR) until closed by hand. */
  execFailure?: string | null;
}

/** Central Command: a dispatcher-claimed email — the work_item a session was
 *  spawned to handle. Mirrors the fields `WorkItem` (decisions feature) reads
 *  off the same row; kept local so chat doesn't reach across features for a
 *  handful of fields. */
export interface SourceEmail {
  id: string;
  kind?: string;
  subject?: string | null;
  payload?: {
    from?: string;
    text?: string;
  };
}

/** Extract the canonical key from a Session object (handles API inconsistency). */
export function getSessionKey(s: Session): string {
  return s.sessionKey || s.key || s.id || '';
}

/** A single entry in the agent activity log (displayed in the TopBar). */
export interface AgentLogEntry {
  icon: string;
  text: string;
  ts: number;
}

/** A gateway event entry shown in the events panel. */
export interface EventEntry {
  badge: string;
  badgeCls: string;
  desc: string;
  ts: Date;
}

export interface Memory {
  type: 'section' | 'item' | 'daily';
  text: string;
  date?: string;
  /** Temporary ID for optimistic updates */
  tempId?: string;
  /** True while the operation is pending confirmation */
  pending?: boolean;
  /** True if the operation failed (for error display before removal) */
  failed?: boolean;
  /** True if this memory is being deleted (fade out animation) */
  deleting?: boolean;
  /** D11-r1: agent-scoped memories carry 'shared'|'private'; absent/dashboard memories are shared. */
  scope?: 'shared' | 'private';
}

/** Memory category types for storing new memories */
export type MemoryCategory = 'preference' | 'fact' | 'decision' | 'entity' | 'other';

/** API response for memory operations */
export interface MemoryApiResponse {
  ok: boolean;
  error?: string;
  result?: unknown;
}

/** Aggregated token usage and cost data from the gateway. */
export interface TokenData {
  entries?: TokenEntry[];
  totalCost?: number;
  totalInput?: number;
  totalOutput?: number;
  totalCacheRead?: number;
  totalMessages?: number;
  persistent?: {
    totalCost: number;
    totalInput: number;
    totalOutput: number;
    lastUpdated: string;
  };
  updatedAt?: number;
  /** Spend over the trailing `windowDays` (vs. totalCost, which is all-time). */
  windowCost?: number;
  windowDays?: number;
  daily?: Array<{ date: string; cost: number; input: number; output: number; requests: number }>;
  proxyUiUrl?: string | null;
}

/** Per-source breakdown of token usage and cost. */
export interface TokenEntry {
  source: string;
  cost: number;
  messageCount?: number;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  errorCount?: number;
}

/** Possible roles for chat messages */
export type ChatMessageRole = 'user' | 'assistant' | 'tool' | 'toolResult' | 'system';

/** Discriminated content block types */
export type ContentBlockType = 'text' | 'tool_use' | 'toolCall' | 'tool_result' | 'toolResult' | 'image' | 'thinking';

/** A single chat message (user, assistant, tool, or system). */
export interface ChatMessage {
  role: ChatMessageRole;
  content: string | ContentBlock[];
  text?: string;
  timestamp?: string | number;
  createdAt?: string | number;
  ts?: string | number;
  MediaPath?: string;
  MediaPaths?: string[];
  MediaType?: string;
  MediaTypes?: string[];
  MediaUrl?: string;
  MediaUrls?: string[];
  /** Central Command: identifies the tool call a toolResult message answers. */
  toolCallId?: string;
  /** Central Command: the tool name, carried on toolResult messages. */
  toolName?: string;
}

/** A content block within a multi-part message (text, tool call, image, etc.). */
export interface ContentBlock {
  type: ContentBlockType;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  id?: string;
  toolCallId?: string;
  arguments?: string | Record<string, unknown>;
  content?: string | ContentBlock[];
  /** Image content block fields (from gateway) */
  data?: string;       // base64 image data
  mimeType?: string;   // e.g. "image/jpeg"
  omitted?: boolean;
  bytes?: number;
  /** Anthropic-style image source */
  source?: { type?: string; media_type?: string; data?: string };
}

/** Gateway message types */
export interface GatewayEvent {
  type: 'event';
  event: string;
  payload?: unknown;
  seq?: number;
  stateVersion?: { presence: number; health: number };
}

export interface GatewayRequest {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

export interface GatewayResponse {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: { message: string; code?: string };
}

export type GatewayMessage = GatewayEvent | GatewayRequest | GatewayResponse;

/** Generic event payload for gateway events */
export interface EventPayload {
  sessionKey?: string;
  state?: string;
  agentState?: string;
  runId?: string;
  seq?: number;
  message?: ChatMessage | string;
  messages?: ChatMessage[];
  content?: ContentBlock[];
  name?: string;
  error?: string;
  errorMessage?: string;
  stopReason?: string;
}

// ─── Typed event payloads ────────────────────────────────────────────

/** Payload for 'chat' events */
export interface ChatEventPayload {
  sessionKey?: string;
  state?: string;
  runId?: string;
  seq?: number;
  message?: ChatMessage | string;
  messages?: ChatMessage[];
  content?: ContentBlock[];
  error?: string;
  errorMessage?: string;
  stopReason?: string;
}

/** Payload for 'agent' events (state changes + tool streaming) */
export interface AgentEventPayload {
  sessionKey?: string;
  state?: string;
  agentState?: string;
  /** Present when stream === 'tool' */
  stream?: string;
  /** Tool stream data (present when stream === 'tool') */
  data?: AgentToolStreamData;
  totalTokens?: number;
  contextTokens?: number;
}

/** Data within an agent tool-stream event */
export interface AgentToolStreamData {
  phase: 'start' | 'result';
  toolCallId?: string;
  name?: string;
  args?: Record<string, unknown>;
}

/** Payload for 'cron' events */
export interface CronEventPayload {
  name?: string;
}

/** Payload for error events */
export interface ErrorEventPayload {
  message?: string;
  error?: string;
}

/** Sessions list RPC response */
export interface SessionsListResponse {
  sessions?: Session[];
}

/** Chat history RPC response */
/**
 * Central Command: whether the operator may type into the session being displayed,
 * and if not, what they should be looking at instead. Served with the history
 * (one payload, one instant) because the composer and the send must agree —
 * a box that invites input the server always rejects is the bug this closes.
 */
export interface ChatComposerState {
  enabled: boolean;
  /** not_a_conversation | pending_proposal | failed | no_session | no_lane |
   *  discussion_concluded | awaiting_continue | stopped */
  code?: string;
  /** What the operator reads — for a run, a sentence about THIS run. */
  message?: string;
  /** The exact 409 a send would come back with (the rule's own words). */
  refusal?: string;
  session?: {
    id: string;
    mode?: string | null;
    status?: string | null;
    agentId?: string | null;
    /** 'concluded' (agent ended its own clarification lane) | 'operator'. */
    closedReason?: string | null;
  } | null;
  /** What kind of run this transcript is: "task run" | "triage run" | "agent run". */
  kind?: string;
  /** The task this run belongs to, when it has one. */
  task?: { id: string; title: string; status: string } | null;
  /** The open ask holding the run — answer it in the Decisions Inbox. */
  blocking?: {
    itemId: string;
    kind: string;
    body: string;
    discussionSessionId?: string | null;
  } | null;
}

export interface ChatHistoryResponse {
  messages?: ChatMessage[];
  composer?: ChatComposerState;
  /** Central Command: the email this lane was spawned to handle, when there is one. */
  sourceEmail?: SourceEmail | null;
}
