"""The update hold — the one flag the runtime reads and `api.hold` flips.

In memory ON PURPOSE. The hold exists to get THIS process stopped cleanly for
an update; the process the updater starts afterwards must come up unheld, and
losing the flag to any other restart is the same outcome. What must survive the
restart is per-session and lives in the session row: a run parked because of
the hold carries `pending_stop.reason == "update_hold"`, which is how the next
process knows to resume it (`api.hold.resume_held_sessions`). Nothing in here
imports the api tier — `runtime/` reads, `api/` writes.
"""

from __future__ import annotations

from datetime import datetime

active: bool = False
target: str = ""
since: datetime | None = None

REASON = "update_hold"


class RunHeld(Exception):
    """A fresh run was refused because an update is waiting for the agents to
    finish. Not a failure: the work stays exactly where it was (an ASSIGNED
    task, an unsent message) and starts after the restart."""
