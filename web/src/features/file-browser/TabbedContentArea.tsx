/**
 * TabbedContentArea — Container with tab bar that switches between
 * the chat panel and open file editors.
 *
 * Chat panel is always mounted (hidden via CSS, never unmounted)
 * to preserve scroll position, streaming state, and input draft.
 */

import { type ReactNode, lazy, Suspense } from 'react';
import { Loader2, AlertTriangle, X } from 'lucide-react';
import { EditorTabBar } from './EditorTabBar';
import { ImageViewer } from './ImageViewer';
import { MarkdownDocumentView } from './MarkdownDocumentView';
import { PdfViewer } from './PdfViewer';
import { isImageFile, isMarkdownFile, isPdfFile } from './utils/fileTypes';
import type { OpenFile } from './types';

// Lazy-load CodeMirror editor — keeps it out of the initial bundle
const FileEditor = lazy(() => import('./FileEditor'));

function EditorFallback() {
  return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-xs gap-2">
      <Loader2 className="animate-spin" size={14} />
      Loading editor...
    </div>
  );
}

interface SaveToast {
  agentId?: string;
  path: string;
  type: 'conflict';
}

interface TabbedContentAreaProps {
  activeTab: string;
  openFiles: OpenFile[];
  workspaceAgentId: string;
  onSelectTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onContentChange: (path: string, content: string) => void;
  onSaveFile: (path: string) => void;
  onRetryFile: (path: string) => void;
  onReloadFile?: (path: string) => void;
  onOpenWorkspacePath?: (path: string, basePath?: string) => void | Promise<void>;
  pathLinkAliases?: Record<string, string>;
  saveToast?: SaveToast | null;
  onDismissToast?: () => void;
  /** The chat panel rendered as-is (never unmounted). */
  chatPanel: ReactNode;
}

export function TabbedContentArea({
  activeTab,
  openFiles,
  workspaceAgentId,
  onSelectTab,
  onCloseTab,
  onContentChange,
  onSaveFile,
  onRetryFile,
  onReloadFile,
  onOpenWorkspacePath,
  pathLinkAliases,
  saveToast,
  onDismissToast,
  chatPanel,
}: TabbedContentAreaProps) {
  const hasOpenTabs = openFiles.length > 0;
  const visibleSaveToast = saveToast && (!saveToast.agentId || saveToast.agentId === workspaceAgentId)
    ? saveToast
    : null;

  return (
    <div className="h-full flex flex-col min-h-0 min-w-0">
      {/* Tab bar — only shown when files are open */}
      {hasOpenTabs && (
        <EditorTabBar
          activeTab={activeTab}
          openFiles={openFiles}
          onSelectTab={onSelectTab}
          onCloseTab={onCloseTab}
        />
      )}

      {/* Content area */}
      <div className="flex-1 min-h-0 relative">
        {/* Chat panel — always mounted, hidden when file tab is active */}
        <div
          className={activeTab === 'chat' || !hasOpenTabs ? 'h-full' : 'hidden'}
          role="tabpanel"
          id="tabpanel-chat"
          aria-labelledby="tab-chat"
        >
          {chatPanel}
        </div>

        {/* File editors — one per open file, only active one visible */}
        {openFiles.map((file) => (
          <div
            key={file.path}
            className={activeTab === file.path ? 'h-full' : 'hidden'}
            role="tabpanel"
            id={`tabpanel-${file.path}`}
            aria-labelledby={`tab-${file.path}`}
          >
            {isImageFile(file.name) ? (
              <ImageViewer file={file} agentId={workspaceAgentId} />
            ) : isPdfFile(file.name) ? (
              <PdfViewer file={file} agentId={workspaceAgentId} />
            ) : isMarkdownFile(file.name) ? (
              <MarkdownDocumentView
                file={file}
                onContentChange={onContentChange}
                onSave={onSaveFile}
                onRetry={onRetryFile}
                onOpenWorkspacePath={onOpenWorkspacePath}
                pathLinkAliases={pathLinkAliases}
              />
            ) : (
              <Suspense fallback={<EditorFallback />}>
                <FileEditor
                  file={file}
                  onContentChange={onContentChange}
                  onSave={onSaveFile}
                  onRetry={onRetryFile}
                />
              </Suspense>
            )}
          </div>
        ))}

        {/* Save conflict toast */}
        {visibleSaveToast && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 px-4 py-2.5 rounded-lg bg-destructive/10 border border-destructive/30 text-sm shadow-lg animate-in fade-in slide-in-from-bottom-2 duration-200">
            <AlertTriangle size={14} className="text-destructive shrink-0" />
            <span className="text-foreground">File changed externally.</span>
            {onReloadFile && (
              <button
                className="text-primary text-xs font-medium hover:underline"
                onClick={() => { onReloadFile(visibleSaveToast.path); onDismissToast?.(); }}
              >
                Reload
              </button>
            )}
            <button
              className="ml-1 -m-1 p-1.5 text-muted-foreground hover:text-foreground"
              onClick={onDismissToast}
              aria-label="Dismiss"
            >
              <X size={12} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
