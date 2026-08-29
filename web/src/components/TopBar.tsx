import {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
  lazy,
  Suspense,
  type ReactNode,
} from "react";
import {
  Activity,
  BarChart3,
  Settings,
  Radio,
  Users,
  Brain,
  MessageSquare,
  LayoutGrid,
  Inbox as InboxIcon,
  BookOpen,
  Waypoints,
  Target,
  ShieldCheck,
  Clock,
  LayoutGrid,
} from "lucide-react";
import type { ViewMode } from "@/features/command-palette/commands";
import type { AgentLogEntry, EventEntry, TokenData } from "@/types";
import NerveLogo from "./NerveLogo";

const AgentLog = lazy(() =>
  import("@/features/activity/AgentLog").then((m) => ({ default: m.AgentLog })),
);
const EventLog = lazy(() =>
  import("@/features/activity/EventLog").then((m) => ({ default: m.EventLog })),
);
const TokenUsage = lazy(() =>
  import("@/features/dashboard/TokenUsage").then((m) => ({
    default: m.TokenUsage,
  })),
);

/** Identifies which dropdown panel is currently open, or `null` for none. */
type PanelId =
  | "agent-log"
  | "usage"
  | "events"
  | "sessions"
  | "workspace"
  | null;

type PanelConfig = {
  boxClass: string;
  heightClass: string;
  contentClass: string;
};

const PANEL_CONFIG: Record<Exclude<PanelId, null> | "default", PanelConfig> = {
  sessions: {
    boxClass: "w-[440px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[70vh] opacity-100",
    contentClass: "max-h-[65vh] overflow-y-auto",
  },
  workspace: {
    boxClass: "w-[600px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[75vh] opacity-100",
    contentClass: "h-[70vh] max-h-[70vh] overflow-hidden",
  },
  "agent-log": {
    boxClass: "w-[480px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[400px] opacity-100",
    contentClass: "max-h-[400px] overflow-y-auto",
  },
  usage: {
    boxClass: "w-[480px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[400px] opacity-100",
    contentClass: "max-h-[400px] overflow-y-auto",
  },
  events: {
    boxClass: "w-[480px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[400px] opacity-100",
    contentClass: "max-h-[400px] overflow-y-auto",
  },
  default: {
    boxClass: "w-[480px] max-w-[calc(100vw-1.067rem)]",
    heightClass: "max-h-[400px] opacity-100",
    contentClass: "max-h-[400px] overflow-y-auto",
  },
};

/** Props for {@link TopBar}. */
interface TopBarProps {
  /** Callback to open the settings modal. */
  onSettings: () => void;
  /** Agent log entries rendered in the dropdown log panel. */
  agentLogEntries: AgentLogEntry[];
  /** Token usage data for the usage panel (null while loading). */
  tokenData: TokenData | null;
  /** Whether the agent-log icon should pulse green to indicate recent activity. */
  logGlow: boolean;
  /** Event log entries for the events panel. */
  eventEntries: EventEntry[];
  /** Whether the Events button/panel should be shown (feature flag). */
  eventsVisible: boolean;
  /** Whether the Log button/panel should be shown (feature flag). */
  logVisible: boolean;
  /** Show compact-layout panel launchers (Sessions/Workspace). */
  mobilePanelButtonsVisible?: boolean;
  /** Renderable Sessions panel content (compact mode). */
  sessionsPanel?: ReactNode;
  /** Renderable Workspace panel content (compact mode). */
  workspacePanel?: ReactNode;
  /** Current view mode (chat or kanban). */
  viewMode?: ViewMode;
  /** Callback to change the view mode. */
  onViewModeChange?: (mode: ViewMode) => void;
  /** Whether the Tasks/Kanban view toggle should be shown. */
  showKanbanView?: boolean;
  /** Per-tab "awaits you" counts driving the notification badges. */
  attention?: { chat: number; decisions: number; tasks: number; verify: number };
}

/**
 * Notification badge on a view-mode tab — a small count pill that says "there
 * is something here awaiting you", so a new decision shows even from another
 * tab. `tone` distinguishes action-required (decisions/chat) from
 * informational (tasks awaiting routing).
 */
function TabBadge({ count, tone }: { count: number; tone: 'action' | 'info' }) {
  if (count <= 0) return null;
  const toneClass =
    tone === 'action'
      ? 'bg-primary text-primary-foreground'
      : 'border border-orange/40 bg-orange/15 text-orange';
  return (
    <span
      aria-label={`${count} awaiting`}
      className={`ml-1 inline-flex min-w-[1.05rem] items-center justify-center rounded-full px-1 py-0 text-[0.6rem] font-bold leading-[1.05rem] tabular-nums ${toneClass}`}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

/**
 * Top navigation bar for the Nerve cockpit.
 *
 * Displays the Nerve logo/brand, and provides toggle buttons for the
 * Agent Log, Events, Token Usage, and (in compact mode) Sessions +
 * Workspace panels.
 */
export function TopBar({
  onSettings,
  agentLogEntries,
  tokenData,
  logGlow,
  eventEntries,
  eventsVisible,
  logVisible,
  mobilePanelButtonsVisible = false,
  sessionsPanel,
  workspacePanel,
  viewMode = "chat",
  onViewModeChange,
  showKanbanView = true,
  attention,
}: TopBarProps) {
  const [activePanel, setActivePanel] = useState<PanelId>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonsRef = useRef<HTMLDivElement>(null);

  const togglePanel = useCallback((panel: PanelId) => {
    setActivePanel((prev) => (prev === panel ? null : panel));
  }, []);

  const isPanelAvailable = useCallback(
    (panel: PanelId) => {
      if (!panel) return true;
      if (panel === "events") return eventsVisible;
      if (panel === "agent-log") return logVisible;
      if (panel === "sessions")
        return mobilePanelButtonsVisible && Boolean(sessionsPanel);
      if (panel === "workspace")
        return mobilePanelButtonsVisible && Boolean(workspacePanel);
      return true;
    },
    [
      eventsVisible,
      logVisible,
      mobilePanelButtonsVisible,
      sessionsPanel,
      workspacePanel,
    ],
  );

  const visiblePanel = useMemo<PanelId>(() => {
    if (!activePanel) return null;
    return isPanelAvailable(activePanel) ? activePanel : null;
  }, [activePanel, isPanelAvailable]);

  // Clear stale panel state asynchronously when panel availability changes.
  useEffect(() => {
    if (!activePanel || visiblePanel) return;
    const timer = window.setTimeout(() => setActivePanel(null), 0);
    return () => window.clearTimeout(timer);
  }, [activePanel, visiblePanel]);

  // Click outside to close
  useEffect(() => {
    if (!visiblePanel) return;
    function handleClick(e: MouseEvent) {
      const targetNode = e.target as Node;
      if (
        panelRef.current?.contains(targetNode) ||
        buttonsRef.current?.contains(targetNode)
      )
        return;

      const targetElement = e.target instanceof Element ? e.target : null;
      // Keep topbar panel open while interacting with modal/portal content
      // launched from inside the panel (e.g., Spawn Agent, Add Memory dialogs).
      if (
        targetElement?.closest(
          '[data-slot="dialog-content"], [data-slot="dialog-overlay"], [role="dialog"], [data-radix-popper-content-wrapper]',
        )
      ) {
        return;
      }

      setActivePanel(null);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [visiblePanel]);

  // Escape to close
  useEffect(() => {
    if (!visiblePanel) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setActivePanel(null);
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [visiblePanel]);

  const totalCost = useMemo(() => {
    if (!tokenData) return null;
    const cost =
      tokenData.windowCost ??
      tokenData.persistent?.totalCost ??
      tokenData.totalCost ??
      0;
    return "$" + cost.toFixed(2);
  }, [tokenData]);

  const panelConfig = useMemo(() => {
    if (!visiblePanel) return PANEL_CONFIG.default;
    return PANEL_CONFIG[visiblePanel] ?? PANEL_CONFIG.default;
  }, [visiblePanel]);

  const panelBoxClass = panelConfig.boxClass;
  const panelHeightClass = visiblePanel
    ? panelConfig.heightClass
    : "max-h-0 opacity-0 pointer-events-none";
  const panelContentClass = panelConfig.contentClass;

  const buttonBase = "shell-icon-button h-11 min-w-11 px-3 max-[371px]:h-[38px] max-[371px]:min-w-[38px] max-[371px]:gap-0.5 max-[371px]:px-2 max-[371px]:[&_svg]:size-3 sm:h-10 sm:min-w-9 sm:px-3";

  return (
    <div className="relative z-40 px-2 pt-2 sm:px-4 sm:pt-3">
      <header className="topbar-mobile-compact shell-panel flex min-h-14 flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl px-3 py-2 shrink-0 max-[371px]:gap-x-1.5 max-[371px]:px-2 sm:flex-nowrap sm:px-4">
        <div className="flex min-w-0 items-center gap-3 max-[371px]:gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-primary/20 bg-background/55 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] max-[371px]:h-9 max-[371px]:w-9">
            <NerveLogo size={24} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold uppercase tracking-[0.34em] text-primary max-[371px]:text-xs max-[371px]:tracking-[0.22em] sm:text-base">
                Nerve
              </span>
            </div>
            <div className="hidden xl:block text-[0.733rem] text-muted-foreground/80">
              Agentic Command Center{" "}
            </div>
          </div>
        </div>
        {/* View mode toggle */}
        {onViewModeChange && (
          <div className="order-3 flex w-full min-w-0 max-w-full items-center gap-2 overflow-x-auto pb-1 max-[371px]:gap-1 sm:order-none sm:ml-2 sm:w-auto sm:pb-0">
            <button
              onClick={() => onViewModeChange("chat")}
              title="Chat View"
              aria-label="Switch to chat view"
              aria-pressed={viewMode === "chat"}
              data-active={viewMode === "chat"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <MessageSquare size={13} aria-hidden="true" />
              <span>Chat</span>
              {attention && <TabBadge count={attention.chat} tone="action" />}
            </button>
            <button
              onClick={() => onViewModeChange("inbox")}
              title="Decisions Inbox"
              aria-label="Switch to decisions inbox"
              aria-pressed={viewMode === "inbox"}
              data-active={viewMode === "inbox"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <InboxIcon size={13} aria-hidden="true" />
              <span>Decisions</span>
              {attention && <TabBadge count={attention.decisions} tone="action" />}
            </button>
            {showKanbanView && (
              <button
                onClick={() => onViewModeChange("kanban")}
                title="Tasks View"
                aria-label="Switch to tasks view"
                aria-pressed={viewMode === "kanban"}
                data-active={viewMode === "kanban"}
                className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
              >
                <LayoutGrid size={13} aria-hidden="true" />
                <span>Tasks</span>
                {attention && <TabBadge count={attention.tasks} tone="info" />}
              </button>
            )}
            {/* Agents — team management. No attention badge: the roster is
                changed by the operator, so it never "awaits you". */}
            <button
              onClick={() => onViewModeChange("agents")}
              title="Agents"
              aria-label="Switch to agents view"
              aria-pressed={viewMode === "agents"}
              data-active={viewMode === "agents"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <Users size={13} aria-hidden="true" />
              <span>Agents</span>
            </button>
            {/* Skills — the knowledge library. No attention badge, for the same
                reason as Agents: the operator changes it, so it never awaits
                them. Declared gaps DO await the operator, but they surface on
                the Skills screen itself rather than as a count here. */}
            <button
              onClick={() => onViewModeChange("skills")}
              title="Skills"
              aria-label="Switch to skills view"
              aria-pressed={viewMode === "skills"}
              data-active={viewMode === "skills"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <BookOpen size={13} aria-hidden="true" />
              <span>Skills</span>
            </button>
            {/* Graph — the knowledge-graph explorer. No attention badge, same
                reasoning as Agents/Skills: the operator explores it, it never
                "awaits" them. */}
            <button
              onClick={() => onViewModeChange("graph")}
              title="Graph"
              aria-label="Switch to graph view"
              aria-pressed={viewMode === "graph"}
              data-active={viewMode === "graph"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <Waypoints size={13} aria-hidden="true" />
              <span>Graph</span>
            </button>
            {/* Graph Verifications — the auditor worklist (2026-08-19 spec).
                Badged like Decisions ('action' tone): every parked row awaits
                the operator's confirm/problem, and without the badge twelve
                items accumulated invisibly on day one (2026-08-20). */}
            <button
              onClick={() => onViewModeChange("graph-verify")}
              title="Graph Verifications"
              aria-label="Switch to graph verifications view"
              aria-pressed={viewMode === "graph-verify"}
              data-active={viewMode === "graph-verify"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <ShieldCheck size={13} aria-hidden="true" />
              <span>Verify</span>
              {attention && <TabBadge count={attention.verify} tone="action" />}
            </button>
            {/* Goals — lines of effort. No attention badge, same reasoning as
                Agents/Skills/Graph: the operator reads it, it never "awaits"
                them. */}
            <button
              onClick={() => onViewModeChange("loe")}
              title="Goals"
              aria-label="Switch to goals view"
              aria-pressed={viewMode === "loe"}
              data-active={viewMode === "loe"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <Target size={13} aria-hidden="true" />
              <span>Goals</span>
            </button>
            {/* Activity — the coverage surface (2026-08-11 spec): current
                operational state, every run, the durable event log. No
                attention badge: the queue/failure counts it owns are rendered
                ON the screen, and a badge here would compete with Decisions,
                which is the surface that genuinely awaits the operator. */}
            <button
              onClick={() => onViewModeChange("activity")}
              title="Activity"
              aria-label="Switch to activity view"
              aria-pressed={viewMode === "activity"}
              data-active={viewMode === "activity"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <Activity size={13} aria-hidden="true" />
              <span>Activity</span>
            </button>
            {/* Crons — the operator scheduling console over the Central Command
                heartbeat (moved out of the chat workspace pane: it is global,
                not agent-scoped). No attention badge, same reasoning as
                Agents/Skills/Graph/Goals: the operator manages it, it never
                "awaits" them. */}
            <button
              onClick={() => onViewModeChange("crons")}
              title="Crons"
              aria-label="Switch to crons view"
              aria-pressed={viewMode === "crons"}
              data-active={viewMode === "crons"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <Clock size={13} aria-hidden="true" />
              <span>Crons</span>
            </button>
            {/* Systems — the launchpad: every deployed/integrated system,
                live status, and where its credential lives. Same terms as
                Crons: an operator-driven surface, no per-agent scope, no
                attention badge. */}
            <button
              onClick={() => onViewModeChange("systems")}
              title="Systems"
              aria-label="Switch to systems view"
              aria-pressed={viewMode === "systems"}
              data-active={viewMode === "systems"}
              className="shell-chip min-h-11 flex-1 justify-center text-[0.733rem] uppercase tracking-[0.14em] max-[371px]:min-h-[38px] max-[371px]:gap-1 max-[371px]:px-2 max-[371px]:text-[0.667rem] max-[371px]:tracking-[0.08em] max-[371px]:[&_svg]:size-3 sm:min-h-10 sm:flex-none"
            >
              <LayoutGrid size={13} aria-hidden="true" />
              <span>Systems</span>
            </button>
          </div>
        )}
        <div ref={buttonsRef} className="ml-auto flex min-w-0 max-w-full items-center justify-end gap-1.5 overflow-x-auto pb-1 max-[371px]:gap-0.5 sm:max-w-none sm:gap-2 sm:overflow-visible sm:pb-0">
          {/* Compact layout launchers (chat-first mode) */}
          {mobilePanelButtonsVisible && sessionsPanel && (
            <button
              onClick={() => togglePanel("sessions")}
              title="Sessions"
              aria-label="Toggle sessions panel"
              aria-expanded={visiblePanel === "sessions"}
              aria-haspopup="true"
              aria-controls="topbar-panel"
              data-active={visiblePanel === "sessions"}
              className={buttonBase}
            >
              <Users size={14} aria-hidden="true" />
              <span className="hidden sm:inline">Sessions</span>
            </button>
          )}

          {mobilePanelButtonsVisible && workspacePanel && (
            <button
              onClick={() => togglePanel("workspace")}
              title="Workspace"
              aria-label="Toggle workspace panel"
              aria-expanded={visiblePanel === "workspace"}
              aria-haspopup="true"
              aria-controls="topbar-panel"
              data-active={visiblePanel === "workspace"}
              className={buttonBase}
            >
              <Brain size={14} aria-hidden="true" />
              <span className="hidden sm:inline">Workspace</span>
            </button>
          )}

          {/* Agent Log button */}
          {logVisible && (
            <button
              onClick={() => togglePanel("agent-log")}
              title="Agent Log"
              aria-label="Toggle agent log panel"
              aria-expanded={visiblePanel === "agent-log"}
              aria-haspopup="true"
              aria-controls="topbar-panel"
              data-active={visiblePanel === "agent-log"}
              className={buttonBase}
            >
              <Activity
                size={14}
                className={logGlow ? "text-green" : ""}
                aria-hidden="true"
              />
              <span className="hidden sm:inline">Log</span>
              {agentLogEntries.length > 0 && (
                <span className="hidden min-w-5 items-center justify-center rounded-full bg-background/80 px-1.5 py-0.5 text-[0.6rem] tabular-nums text-foreground/80 md:inline-flex">
                  {agentLogEntries.length}
                </span>
              )}
            </button>
          )}

          {/* Events button */}
          {eventsVisible && (
            <button
              onClick={() => togglePanel("events")}
              title="Events"
              aria-label="Toggle events panel"
              aria-expanded={visiblePanel === "events"}
              aria-haspopup="true"
              aria-controls="topbar-panel"
              data-active={visiblePanel === "events"}
              className={buttonBase}
            >
              <Radio size={14} aria-hidden="true" />
              <span className="hidden sm:inline">Events</span>
              {eventEntries.length > 0 && (
                <span className="hidden min-w-5 items-center justify-center rounded-full bg-background/80 px-1.5 py-0.5 text-[0.6rem] tabular-nums text-foreground/80 md:inline-flex">
                  {eventEntries.length}
                </span>
              )}
            </button>
          )}

          {/* Usage button */}
          <button
            onClick={() => togglePanel("usage")}
            title="Token Usage"
            aria-label="Toggle usage panel"
            aria-expanded={visiblePanel === "usage"}
            aria-haspopup="true"
            aria-controls="topbar-panel"
            data-active={visiblePanel === "usage"}
            className={buttonBase}
          >
            <BarChart3 size={14} aria-hidden="true" />
            <span className="hidden sm:inline">Usage</span>
            {totalCost && (
              <span className="hidden rounded-full bg-background/80 px-2 py-0.5 text-[0.6rem] tabular-nums text-foreground/80 lg:inline-flex">
                {totalCost}
              </span>
            )}
          </button>

          {/* Settings button */}
          <button
            onClick={onSettings}
            title="Settings"
            aria-label="Open settings"
            className="shell-icon-button size-11 px-0 max-[371px]:size-[38px] max-[371px]:[&_svg]:size-3 sm:size-10"
          >
            <Settings size={14} aria-hidden="true" />
          </button>
        </div>
      </header>

      {/* Expandable dropdown panel */}
      <div
        ref={panelRef}
        id="topbar-panel"
        role="region"
        aria-label={visiblePanel ? `${visiblePanel} panel` : undefined}
        hidden={!visiblePanel}
        className={`shell-panel absolute right-0 mt-2 overflow-hidden rounded-2xl transition-all duration-200 ease-out ${panelBoxClass} ${panelHeightClass}`}
        style={{ top: "100%" }}
      >
        <div className={panelContentClass}>
          <Suspense
            fallback={
              <div className="p-4 text-muted-foreground text-xs">Loading…</div>
            }
          >
            {visiblePanel === "agent-log" && (
              <AgentLog entries={agentLogEntries} glow={logGlow} />
            )}
            {visiblePanel === "events" && <EventLog entries={eventEntries} />}
            {visiblePanel === "usage" && <TokenUsage data={tokenData} />}
            {visiblePanel === "sessions" && sessionsPanel}
            {visiblePanel === "workspace" && workspacePanel}
          </Suspense>
        </div>
      </div>
    </div>
  );
}
