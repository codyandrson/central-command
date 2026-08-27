import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { TaskSourceEmail } from './types';

/** Collapsed-expandable source email — mirrors the Decisions Inbox's
 * EmailCard pattern (subject + sender collapsed, full body on expand) so an
 * operator sees the same rendering whether they're approving a decision or
 * inspecting a task's origin on the board. */
export function SourceEmailBlock({ email }: { email: TaskSourceEmail }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="cockpit-note space-y-2">
      <h4 className="cockpit-field-label">Source email</h4>
      <div className="rounded-lg border border-border/40">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-primary/[0.04]"
        >
          <ChevronDown size={12} className={`shrink-0 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
          <span className="min-w-0 flex-1 truncate text-xs text-foreground">
            {email.subject || '(no subject)'}
          </span>
          <span className="shrink-0 text-[0.667rem] text-muted-foreground">{email.from}</span>
        </button>
        {open && (
          <div className="whitespace-pre-wrap cockpit-wrap border-t border-border/40 px-3 py-2 text-[0.733rem] leading-relaxed text-foreground/85">
            {email.text || '(content not hydrated)'}
          </div>
        )}
      </div>
    </div>
  );
}
