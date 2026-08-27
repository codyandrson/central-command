"""Operator-direct episode entry — the onboarding interview's write path.

Setup-time facts (who the operator is, their team, their environment) are
OPERATOR INPUT, which is trusted evidence — not agent claims, which is what
the approval gate exists to check. This script is the operator's hands, like
the cockpit curation path: it writes through the exact same
`integrations.graphiti.add_episode` call the Executor uses, with provenance
that says so (`trust=operator-direct`, the curation-path convention). It
lives in scripts/, outside runtime/, because agents must never hold it.

Extraction still runs unreviewed on these episodes, exactly as it does on
approved ones — that is the graph-verify sweep's job, not approval's.

Usage:
    python scripts/onboard_episode.py "<name>" "<episode body>"
    python scripts/onboard_episode.py "Team: platform" - <<'EOF'
    Sarah Chen leads the platform team ...
    EOF
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    name, body = sys.argv[1], sys.argv[2]
    if body == "-":
        body = sys.stdin.read()
    if not name.strip() or not body.strip():
        sys.exit("FATAL: episode name and body must be non-empty")

    from central_command.integrations import graphiti

    ack = await graphiti.add_episode(
        name=name.strip(),
        episode_body=body.strip(),
        source_description=(
            "operator onboarding entry, trust=operator-direct "
            "(setup interview, scripts/onboard_episode.py)"
        ),
    )
    print(ack)


if __name__ == "__main__":
    asyncio.run(main())
