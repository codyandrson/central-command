import type { Command } from './types';
import { themes, type ThemeName } from '@/lib/themes';
import { fonts, type FontName } from '@/lib/fonts';
import type { TTSProvider } from '@/features/tts/useTTS';

export type ViewMode = 'chat' | 'kanban' | 'inbox' | 'agents' | 'skills' | 'graph' | 'graph-verify' | 'activity' | 'loe' | 'crons';

export interface CommandActions {
  onNewSession: () => void;
  onResetSession: () => void;
  onToggleSound: () => void;
  onSettings: () => void;
  onSearch: () => void;
  onAbort: () => void;
  onSetTheme: (theme: ThemeName) => void;
  onSetFont: (font: FontName) => void;
  onTtsProviderChange: (provider: TTSProvider) => void;
  onToggleWakeWord: () => void;
  onToggleEvents: () => void;
  onToggleLog: () => void;
  onToggleTelemetry: () => void;
  onOpenSettings: () => void;
  onRefreshSessions: () => void;
  onRefreshMemory: () => void;
  onSetViewMode?: (mode: ViewMode) => void;
  canShowKanban?: boolean;
}

const THEME_LABELS: Record<ThemeName, string> = {
  'midnight': 'Midnight',
  'light': 'Light',
  'phosphor': 'Phosphor',
  'dracula': 'Dracula',
  'nord': 'Nord',
  'solarized-dark': 'Solarized Dark',
  'catppuccin-mocha': 'Catppuccin Mocha',
  'tokyo-night': 'Tokyo Night',
  'gruvbox-dark': 'Gruvbox Dark',
  'one-dark': 'One Dark',
  'monokai': 'Monokai',
  'ayu-dark': 'Ayu Dark',
  'rose-pine': 'Rosé Pine',
  'monochrome': 'Monochrome',
};

const FONT_LABELS: Record<FontName, string> = {
  'instrument-sans': 'Instrument Sans',
  'space-grotesk': 'Space Grotesk',
  'jetbrains-mono': 'JetBrains Mono',
};

/** Build the full list of command-palette commands from action callbacks. */
export function createCommands(actions: CommandActions): Command[] {
  const themeCommands: Command[] = (Object.keys(themes) as ThemeName[]).map((key) => ({
    id: `theme-${key}`,
    label: `Theme: ${THEME_LABELS[key] || key}`,
    action: () => actions.onSetTheme(key),
    category: 'appearance' as const,
    keywords: ['theme', 'color', 'dark', 'light', key.replace(/-/g, ' ')],
  }));

  const fontCommands: Command[] = (Object.keys(fonts) as FontName[]).map((key) => ({
    id: `font-${key}`,
    label: `Font: ${FONT_LABELS[key] || key}`,
    action: () => actions.onSetFont(key),
    category: 'appearance' as const,
    keywords: ['font', 'typeface', 'typography', key.replace(/-/g, ' ')],
  }));

  return [
    {
      id: 'search',
      label: 'Search messages',
      shortcut: '⌘F',
      action: actions.onSearch,
      category: 'navigation',
      keywords: ['find', 'filter'],
    },
    {
      id: 'abort',
      label: 'Stop generation',
      shortcut: 'Esc',
      action: actions.onAbort,
      category: 'actions',
      keywords: ['cancel', 'stop', 'abort'],
    },
    {
      id: 'new-session',
      label: 'Create session',
      action: actions.onNewSession,
      category: 'actions',
      keywords: ['new', 'session', 'agent', 'subagent', 'spawn', 'create', 'launch'],
    },
    {
      id: 'reset',
      label: 'Close session & start fresh',
      action: actions.onResetSession,
      category: 'actions',
      keywords: ['close', 'new', 'fresh', 'reset'],
    },
    {
      id: 'refresh-sessions',
      label: 'Refresh Sessions',
      action: actions.onRefreshSessions,
      category: 'actions',
      keywords: ['refresh', 'reload', 'sessions'],
    },
    {
      id: 'refresh-memory',
      label: 'Refresh Memory',
      action: actions.onRefreshMemory,
      category: 'actions',
      keywords: ['refresh', 'reload', 'memory'],
    },
    {
      id: 'toggle-events',
      label: 'Toggle Events Panel',
      action: actions.onToggleEvents,
      category: 'navigation',
      keywords: ['events', 'log', 'panel'],
    },
    {
      id: 'toggle-log',
      label: 'Toggle Log Panel',
      action: actions.onToggleLog,
      category: 'navigation',
      keywords: ['activity', 'log', 'panel'],
    },
    {
      id: 'toggle-telemetry',
      label: 'Toggle Usage Panel',
      action: actions.onToggleTelemetry,
      category: 'navigation',
      keywords: ['telemetry', 'usage', 'tokens', 'panel'],
    },
    {
      id: 'open-settings',
      label: 'Open Settings',
      action: actions.onOpenSettings,
      category: 'navigation',
      keywords: ['settings', 'config', 'preferences'],
    },
    {
      id: 'sound',
      label: 'Toggle sound effects',
      action: actions.onToggleSound,
      category: 'settings',
      keywords: ['audio', 'mute', 'sfx'],
    },
    {
      id: 'settings',
      label: 'Connection settings',
      action: actions.onSettings,
      category: 'settings',
      keywords: ['config', 'connect', 'gateway'],
    },
    // TTS commands
    {
      id: 'tts-openai',
      label: 'TTS: Switch to OpenAI',
      action: () => actions.onTtsProviderChange('openai' as TTSProvider),
      category: 'voice',
      keywords: ['tts', 'voice', 'speech', 'openai'],
    },
    {
      id: 'tts-replicate',
      label: 'TTS: Switch to Replicate',
      action: () => actions.onTtsProviderChange('replicate' as TTSProvider),
      category: 'voice',
      keywords: ['tts', 'voice', 'speech', 'replicate', 'qwen'],
    },
    {
      id: 'tts-edge',
      label: 'TTS: Switch to Edge (Free)',
      action: () => actions.onTtsProviderChange('edge' as TTSProvider),
      category: 'voice',
      keywords: ['tts', 'voice', 'speech', 'edge', 'free'],
    },
    {
      id: 'tts-xiaomi',
      label: 'TTS: Switch to Xiaomi Mimo',
      action: () => actions.onTtsProviderChange('xiaomi' as TTSProvider),
      category: 'voice',
      keywords: ['tts', 'voice', 'speech', 'xiaomi', 'mimo'],
    },
    {
      id: 'toggle-wake-word',
      label: 'Toggle Wake Word',
      action: actions.onToggleWakeWord,
      category: 'voice',
      keywords: ['wake', 'voice', 'microphone', 'hey'],
    },
    // Decisions Inbox — Central Command's approval gate (always available)
    ...(actions.onSetViewMode ? [
      {
        id: 'open-decisions-inbox',
        label: 'Open Decisions Inbox',
        action: () => actions.onSetViewMode!('inbox'),
        category: 'kanban' as const,
        keywords: ['decisions', 'inbox', 'approvals', 'proposals', 'dismissals', 'review', 'view'],
      },
    ] : []),
    // Agents — the team-management surface (always available; managing the
    // roster must not depend on the kanban feature flag).
    ...(actions.onSetViewMode ? [
      {
        id: 'open-agents',
        label: 'Open Agents',
        action: () => actions.onSetViewMode!('agents'),
        category: 'kanban' as const,
        keywords: ['agents', 'roster', 'team', 'charter', 'capabilities', 'coach', 'hire', 'retire', 'view'],
      },
    ] : []),
    // Skills — the knowledge library. Available on the same terms as Agents:
    // what the team knows is managed by the operator, not gated by a feature
    // flag.
    ...(actions.onSetViewMode ? [
      {
        id: 'open-skills',
        label: 'Open Skills',
        action: () => actions.onSetViewMode!('skills'),
        category: 'kanban' as const,
        keywords: ['skills', 'library', 'knowledge', 'docs', 'documents', 'reference', 'gaps', 'import', 'view'],
      },
    ] : []),
    // Graph — the knowledge-graph explorer. Same terms as Agents/Skills: an
    // operator-driven read surface, no feature flag.
    ...(actions.onSetViewMode ? [
      {
        id: 'open-graph',
        label: 'Open Graph',
        action: () => actions.onSetViewMode!('graph'),
        category: 'kanban' as const,
        keywords: ['graph', 'knowledge', 'neo4j', 'entities', 'relationships', 'explore', 'view'],
      },
    ] : []),
    // Graph Verifications — the auditor worklist (2026-08-19 spec, Task 6).
    // Same terms as Agents/Skills/Graph: an operator-read surface, no feature
    // flag; it happens to also hold two operator actions (confirm/problem).
    ...(actions.onSetViewMode ? [
      {
        id: 'open-graph-verify',
        label: 'Open Graph Verifications',
        action: () => actions.onSetViewMode!('graph-verify'),
        category: 'kanban' as const,
        keywords: ['graph', 'verification', 'auditor', 'episode', 'delta', 'confirm', 'problem', 'view'],
      },
    ] : []),
    // Activity — the coverage surface (2026-08-11 activity-coverage spec):
    // current operational state, every run, and the durable event log. The
    // keywords are deliberately broad because this is where an operator lands
    // when they don't know which screen owns what they're looking for.
    ...(actions.onSetViewMode ? [
      {
        id: 'open-activity',
        label: 'Open Activity',
        action: () => actions.onSetViewMode!('activity'),
        category: 'kanban' as const,
        keywords: ['activity', 'runs', 'events', 'log', 'audit', 'history', 'dashboard',
                   'queue', 'ledger', 'dispatch', 'failures', 'heartbeat', 'view'],
      },
    ] : []),
    // Goals — lines of effort. Same terms as Agents/Skills/Graph: an
    // operator-read surface, no feature flag.
    ...(actions.onSetViewMode ? [
      {
        id: 'open-loe',
        label: 'Open Goals',
        action: () => actions.onSetViewMode!('loe'),
        category: 'kanban' as const,
        keywords: ['goals', 'loe', 'lines of effort', 'objectives', 'accountability', 'view'],
      },
    ] : []),
    // Crons — the operator scheduling console over the Central Command heartbeat.
    // Same terms as Agents/Skills/Graph/Goals: an operator-driven surface
    // with no per-agent scope, so it carries no feature flag.
    ...(actions.onSetViewMode ? [
      {
        id: 'open-crons',
        label: 'Open Crons',
        action: () => actions.onSetViewMode!('crons'),
        category: 'kanban' as const,
        keywords: ['crons', 'schedule', 'heartbeat', 'automation', 'recurring', 'view'],
      },
    ] : []),
    // Kanban commands
    ...(actions.onSetViewMode && actions.canShowKanban !== false ? [
      {
        id: 'open-kanban',
        label: 'Open Tasks View',
        action: () => actions.onSetViewMode!('kanban'),
        category: 'kanban' as const,
        keywords: ['kanban', 'board', 'tasks', 'view'],
      },
      {
        id: 'open-chat',
        label: 'Open Chat View',
        action: () => actions.onSetViewMode!('chat'),
        category: 'kanban' as const,
        keywords: ['chat', 'conversation', 'view'],
      },
      {
        id: 'create-kanban-task',
        label: 'Create Task',
        action: () => actions.onSetViewMode!('kanban'),
        category: 'kanban' as const,
        keywords: ['kanban', 'task', 'create', 'new', 'add'],
      },
    ] : []),
    ...themeCommands,
    ...fontCommands,
  ];
}

const CATEGORY_ORDER: Record<string, number> = {
  actions: 0,
  navigation: 1,
  kanban: 2,
  settings: 3,
  appearance: 4,
  voice: 5,
};

/** Filter commands by fuzzy-matching against a search query. */
export function filterCommands(commands: Command[], query: string): Command[] {
  const candidates = query.trim()
    ? commands.filter(cmd => {
        const q = query.toLowerCase();
        if (cmd.label.toLowerCase().includes(q)) return true;
        if (cmd.keywords?.some(k => k.toLowerCase().includes(q))) return true;
        return false;
      })
    : commands;
  
  // Always sort by category order so display order matches flat index
  return candidates.sort((a, b) => {
    const orderA = CATEGORY_ORDER[a.category || 'actions'] ?? 99;
    const orderB = CATEGORY_ORDER[b.category || 'actions'] ?? 99;
    return orderA - orderB;
  });
}
