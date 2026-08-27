interface BaseChatArtifact {
  source?: string | null;
}

export interface PlanChatArtifact extends BaseChatArtifact {
  title: string;
  path: string;
}

export interface BeadChatArtifact extends BaseChatArtifact {
  title: string;
  id: string;
}

export interface WorkspacePathChatArtifact extends BaseChatArtifact {
  path: string;
  kind: 'file' | 'directory';
}

function formatArtifactBlock(kind: string, lines: Array<[label: string, value: string | null | undefined]>): string {
  return [
    `${kind} context:`,
    ...lines
      .filter(([, value]) => typeof value === 'string' && value.trim().length > 0)
      .map(([label, value]) => `- ${label}: ${value!.trim()}`),
  ].join('\n');
}

export function formatPlanAddToChat(plan: PlanChatArtifact): string {
  return formatArtifactBlock('Plan', [
    ['Source', plan.source],
    ['Title', plan.title],
    ['Path', plan.path],
  ]);
}

export function formatBeadAddToChat(bead: BeadChatArtifact): string {
  return formatArtifactBlock('Bead', [
    ['Source', bead.source],
    ['Title', bead.title],
    ['ID', bead.id],
  ]);
}

export function formatWorkspacePathAddToChat(item: WorkspacePathChatArtifact): string {
  return formatArtifactBlock('Workspace', [
    ['Source', item.source],
    ['Kind', item.kind],
    ['Path', item.path],
  ]);
}

export function mergeAddToChatText(currentDraft: string, nextBlock: string): string {
  const trimmedDraft = currentDraft.trim();
  if (!trimmedDraft) return nextBlock;

  return `${trimmedDraft}\n\n${nextBlock}`;
}
