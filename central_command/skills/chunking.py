"""Markdown → chunks, for the reference-document search index.

Deliberately simple: split at headings, and only split further when a single
section exceeds the budget. Adjacent small sections are NOT merged — merging
reads well in theory and produces chunks whose heading no longer describes their
content, which is worse for both retrieval and citation. The chunk table is
rebuilt from `skill_doc.content` on every new version, so changing this heuristic
later is a re-import, not a migration.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^#{1,6}[ \t]+\S.*$", re.MULTILINE)


def split_markdown(content: str, max_chars: int = 2000) -> list[str]:
    """Split `content` into chunks of at most `max_chars` characters.

    Anything before the first heading is kept as its own leading chunk — front
    matter and intros carry real information, and silently dropping them is the
    kind of loss nobody notices until an agent cites something that isn't there.
    """
    if not content.strip():
        return []

    starts = [m.start() for m in _HEADING.finditer(content)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    bounds = list(zip(starts, starts[1:] + [len(content)]))
    sections = [content[a:b].strip() for a, b in bounds]

    chunks: list[str] = []
    for section in sections:
        if not section:
            continue
        if len(section) <= max_chars:
            chunks.append(section)
            continue
        buf = ""
        for para in section.split("\n\n"):
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append(buf.strip())
                buf = ""
            # A single paragraph over budget is emitted hard-wrapped rather than
            # dropped: a truncated chunk would read to the model as complete.
            while len(para) > max_chars:
                chunks.append(para[:max_chars])
                para = para[max_chars:]
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
    return chunks
