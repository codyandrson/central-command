"""`fake_world`: the dry_run executor's stand-in in the test tree.

Under `tests/conftest.py`'s `no_live_world` guard an approval that reaches a
real handler fails loudly as unconfigured. A test that needs the approval to
EXECUTE without any service installs this instead: every `executor.HANDLERS`
entry not named in `real` becomes a recorder returning `"[fake] <capability>"`,
and every `executor.WORLD_CHECKS` entry not named in `real` becomes a no-op
that reads nothing. Internal capabilities (charter, LOE, catalog, skill) are
faked too, exactly as dry_run skipped them, so a test that pinned dry_run keeps
its meaning; `real=("charter.update",)` runs the named handlers (and their
world checks) for real.

    log = fake_world(monkeypatch)
    ...approve...
    assert log == [("jira.set_due_date", {"issue_key": "DEMO-1", ...}, approver)]

The returned log is the handler calls in order, `(capability, arguments,
approver)`; `log.checks` holds the faked world checks' calls,
`(capability, arguments)`. Both are undone with the test's monkeypatch.
"""

from __future__ import annotations

from central_command.gateway import executor


class WorldLog(list):
    """The handler calls `[(capability, arguments, approver)]`, plus the faked
    world checks' calls in `.checks`."""

    def __init__(self):
        super().__init__()
        self.checks: list[tuple[str, dict]] = []


def fake_world(monkeypatch, real=()) -> WorldLog:
    real = set(real)
    unknown = real - set(executor.HANDLERS)
    if unknown:
        raise ValueError(f"fake_world(real=...) names no handler: {sorted(unknown)}")
    log = WorldLog()

    def recorder(capability: str):
        async def handler(args: dict, approver: str, proposer: str | None) -> str:
            log.append((capability, args, approver))
            return f"[fake] {capability}"

        return handler

    def check_recorder(capability: str):
        async def check(args: dict) -> None:
            log.checks.append((capability, args))

        return check

    for capability in executor.HANDLERS:
        if capability not in real:
            monkeypatch.setitem(executor.HANDLERS, capability, recorder(capability))
    for capability in executor.WORLD_CHECKS:
        if capability not in real:
            monkeypatch.setitem(executor.WORLD_CHECKS, capability, check_recorder(capability))
    return log
