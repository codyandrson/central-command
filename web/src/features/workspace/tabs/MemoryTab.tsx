/**
 * MemoryTab — "what this agent has learned", read-only.
 *
 * It replaces upstream's MEMORY.md editor. Central Command's memory is not a
 * file, and nothing here writes: every note, skill document, graph episode
 * and charter version arrives through a proposal the operator decides in the
 * Decisions Inbox, so the tab shows the result and links to where it is
 * decided. Five sections, each from its own read so one failing leaves the
 * rest on screen: what is waiting for a decision, the notes (operator
 * preferences, then the agent's own), its private graph episodes, the skill
 * documents it proposed, and its charter versions.
 */

import { RefreshCw, Brain } from 'lucide-react';
import {
  useCharterHistory, useEpisodes, useLearned, useNotes,
} from '../hooks/useAgentLearned';
import {
  CharterSection, EpisodesSection, NotesSection, PendingSection, SkillDocsSection,
  type LearnedNav, type LearnedTarget,
} from './MemorySections';

export type { LearnedTarget };

interface MemoryTabProps extends LearnedNav {
  agentId: string;
}

/** Workspace tab listing what the agent has learned and what awaits approval. */
export function MemoryTab({ agentId, onNavigate, onOpenSession }: MemoryTabProps) {
  const notes = useNotes(agentId);
  const learned = useLearned(agentId);
  const episodes = useEpisodes(agentId);
  const charter = useCharterHistory(agentId);
  const nav: LearnedNav = { onNavigate, onOpenSession };

  const busy = notes.loading || learned.loading || episodes.loading || charter.loading;
  const refreshAll = () => {
    notes.refresh();
    learned.refresh();
    episodes.refresh();
    charter.refresh();
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        <div className="flex items-center border-b border-border/40">
          <div className="flex items-center gap-2 px-3 py-1.5 text-[0.733rem] flex-1 text-muted-foreground">
            <Brain size={12} className="shrink-0" />
            <span>What this agent has learned</span>
          </div>
          <button
            type="button"
            onClick={refreshAll}
            disabled={busy}
            className="shrink-0 px-2 py-1.5 bg-transparent border-0 text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-purple/50 focus-visible:ring-offset-0"
            title="Refresh"
            aria-label="Refresh what this agent has learned"
          >
            <RefreshCw size={10} className={busy ? 'animate-spin' : ''} />
          </button>
        </div>

        <PendingSection learned={learned} nav={nav} />
        <NotesSection notes={notes} nav={nav} />
        <EpisodesSection episodes={episodes} loadMore={episodes.loadMore} nav={nav} />
        <SkillDocsSection learned={learned} nav={nav} />
        <CharterSection charter={charter} nav={nav} />
      </div>
    </div>
  );
}
