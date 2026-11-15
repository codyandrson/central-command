/**
 * WorkspacePanel — Tabbed container for the chat page's per-agent workspace.
 * Tabs: Memory (what the agent has learned), Kanban, Config (with Files/Skills sub-views)
 * Active tab persisted in localStorage. Content lazy-loaded per tab.
 * Tab action buttons (add, refresh) render in the tab bar header.
 * Crons moved to its own top-level view (2026-08-21) — it is a global
 * operator console over the heartbeat, not agent-scoped, so it never
 * belonged here.
 */

import { useState, useCallback, useEffect } from 'react';
import { WorkspaceTabs, type TabId } from './WorkspaceTabs';
import { ConfigTab, MemoryTab, SkillsTab } from './tabs';
import type { LearnedTarget } from './tabs/MemoryTab';
import { KanbanQuickView } from '@/features/kanban';
import { getWorkspaceStorageKey } from './workspaceScope';
import { useSettings } from '@/contexts/SettingsContext';

const CONFIG_VIEW_KEY = 'nerve-config-view';

type ConfigView = 'files' | 'skills';

function getInitialConfigView(agentId: string): ConfigView {
  try {
    if (localStorage.getItem(CONFIG_VIEW_KEY) === 'skills') {
      return 'skills';
    }

    const stored = localStorage.getItem(getWorkspaceStorageKey('config-view', agentId));
    if (stored === 'files') {
      return 'files';
    }
  } catch {
    // ignore storage errors
  }

  return 'files';
}

function persistConfigView(view: ConfigView, agentId: string) {
  try {
    if (view === 'skills') {
      localStorage.setItem(CONFIG_VIEW_KEY, 'skills');
      return;
    }

    localStorage.removeItem(CONFIG_VIEW_KEY);
    localStorage.setItem(getWorkspaceStorageKey('config-view', agentId), 'files');
  } catch {
    // ignore storage errors
  }
}

interface ConfigWithSkillsProps {
  agentId: string;
}

/** Combined Config tab with Files/Skills sub-view toggle. */
function ConfigWithSkills({ agentId }: ConfigWithSkillsProps) {
  const [view, setView] = useState<ConfigView>(() => getInitialConfigView(agentId));

  const switchView = useCallback((nextView: ConfigView) => {
    setView(nextView);
    persistConfigView(nextView, agentId);
  }, [agentId]);

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-center gap-0.5 px-2 py-1 border-b border-border/40">
        {(['files', 'skills'] as const).map(nextView => (
          <button
            key={nextView}
            onClick={() => switchView(nextView)}
            className={`text-[0.667rem] uppercase tracking-wider px-2 py-0.5 rounded-sm border-0 cursor-pointer transition-colors focus-visible:ring-2 focus-visible:ring-purple/50 focus-visible:ring-offset-0 ${
              view === nextView
                ? 'bg-purple/15 text-purple font-semibold'
                : 'bg-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {nextView === 'files' ? 'charter' : nextView}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {view === 'files' ? <ConfigTab key={agentId} agentId={agentId} /> : <SkillsTab key={agentId} agentId={agentId} />}
      </div>
    </div>
  );
}

const STORAGE_KEY = 'nerve-workspace-tab';

function getInitialTab(canShowKanban: boolean): TabId {
  const allowedTabs: TabId[] = canShowKanban
    ? ['memory', 'config', 'kanban']
    : ['memory', 'config'];
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    // A pre-move value of 'crons' (Crons is now its own top-level view) is
    // not in allowedTabs and falls through to the 'memory' default below.
    if (stored && allowedTabs.includes(stored as TabId)) {
      return stored as TabId;
    }
  } catch { /* ignore */ }
  return 'memory';
}

interface WorkspacePanelProps {
  workspaceAgentId: string;
  /** Render in compact dropdown mode (chat-first topbar panel). */
  compact?: boolean;
  /** Switch the app to full kanban board view. */
  onOpenBoard?: () => void;
  /** Open a specific task in the full board view. */
  onOpenTask?: (taskId: string) => void;
  /** Memory tab links: the screen that owns a section's detail (Decisions Inbox, graph, skills, agents). */
  onNavigate?: (target: LearnedTarget) => void;
  /** Memory tab link: open the session a note was grounded in. */
  onOpenSession?: (sessionId: string, agentId: string) => void;
}

export function WorkspacePanel({
  workspaceAgentId,
  compact = false,
  onOpenBoard,
  onOpenTask,
  onNavigate,
  onOpenSession,
}: WorkspacePanelProps) {
  const { kanbanVisible } = useSettings();
  const [activeTab, setActiveTab] = useState<TabId>(() => getInitialTab(kanbanVisible));

  const [visitedTabs, setVisitedTabs] = useState<Set<TabId>>(() => new Set([activeTab]));

  const handleTabChange = useCallback((tab: TabId) => {
    setActiveTab(tab);
    setVisitedTabs(prev => {
      if (prev.has(tab)) return prev;
      const next = new Set(prev);
      next.add(tab);
      return next;
    });
    try {
      localStorage.setItem(STORAGE_KEY, tab);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (kanbanVisible) return;

    if (activeTab === 'kanban') {
      const timeoutId = window.setTimeout(() => {
        handleTabChange('memory');
      }, 0);
      return () => window.clearTimeout(timeoutId);
    }

    try {
      if (localStorage.getItem(STORAGE_KEY) === 'kanban') {
        localStorage.setItem(STORAGE_KEY, activeTab);
      }
    } catch { /* ignore */ }
  }, [activeTab, kanbanVisible, handleTabChange]);

  return (
    <div className={compact ? 'h-[70vh] max-h-[70vh] flex flex-col min-h-0' : 'h-full flex flex-col min-h-0'}>
      <WorkspaceTabs
        activeTab={activeTab}
        onTabChange={handleTabChange}
        kanbanCount={undefined}
        showKanban={kanbanVisible}
      />
      <div className="flex-1 min-h-0 overflow-hidden">
        <div className={activeTab === 'memory' ? 'h-full' : 'hidden'} hidden={activeTab !== 'memory'} role="tabpanel" id="workspace-tabpanel-memory" aria-labelledby="workspace-tab-memory">
          {visitedTabs.has('memory') && (
            <MemoryTab
              key={workspaceAgentId}
              agentId={workspaceAgentId}
              onNavigate={onNavigate}
              onOpenSession={onOpenSession}
            />
          )}
        </div>
        <div className={activeTab === 'config' ? 'h-full' : 'hidden'} hidden={activeTab !== 'config'} role="tabpanel" id="workspace-tabpanel-config" aria-labelledby="workspace-tab-config">
          {visitedTabs.has('config') && <ConfigWithSkills key={workspaceAgentId} agentId={workspaceAgentId} />}
        </div>
        {kanbanVisible && (
          <div className={activeTab === 'kanban' ? 'h-full' : 'hidden'} hidden={activeTab !== 'kanban'} role="tabpanel" id="workspace-tabpanel-kanban" aria-labelledby="workspace-tab-kanban">
            {visitedTabs.has('kanban') && (
              <KanbanQuickView
                agentId={workspaceAgentId}
                onOpenBoard={onOpenBoard ?? (() => {})}
                onOpenTask={(task) => onOpenTask ? onOpenTask(task.id) : onOpenBoard?.()}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
