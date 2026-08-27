"""The heartbeat (Phase 4, D27) — a scheduler for recurring team work.

The team gets a pulse: mail polls, dispatcher drain windows, a nightly
Jira-hygiene task, the auditor's agreement report — authored and toggled as
`heartbeat_schedule` rows, surfaced in the cockpit's crons tab.

**Approval attaches to what the work does, never to the trigger.** The
heartbeat neither bypasses a gate that exists nor adds one that doesn't: its
closed action registry (actions.py) can only invoke levers the operator
already has, and this package never imports the gateway's approve/execute
machinery (guard-tested, same pattern as the runtime import ban).
"""

from central_command.heartbeat.engine import engine, fire  # noqa: F401
