"""`fake_world`: the one way a test lets an approval EXECUTE without a service.

Under `tests/conftest.py`'s `no_live_world` guard an approval that reaches a
real handler fails loudly as unconfigured. A test that needs the approval to
EXECUTE without any service installs this instead: every `executor.HANDLERS`
entry not named in `real` becomes a recorder returning `"[fake] <capability>"`,
and every `executor.WORLD_CHECKS` entry not named in `real` becomes a no-op
that reads nothing. Internal capabilities (charter, LOE, catalog, skill) are
faked too, so a test that never needs their effect does not run them;
`real=("charter.update",)` runs the named handlers (and their world checks)
for real.

It is safe to call twice in one test: a second call sees through an earlier
fake to the original handler, so an autouse fixture can fake everything and a
test body can call `fake_world(monkeypatch, real=(...))` to restore the named
ones, and `real=` always restores and never stacks. It returns a fresh log
each time; the earlier call's log stops recording.

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

    def recorder(capability: str, original):
        async def handler(args: dict, approver: str, proposer: str | None) -> str:
            log.append((capability, args, approver))
            return f"[fake] {capability}"

        handler.fake_world_original = original
        return handler

    def check_recorder(capability: str, original):
        async def check(args: dict) -> None:
            log.checks.append((capability, args))

        check.fake_world_original = original
        return check

    # A second call in the same test (an autouse fixture faked everything, one
    # test needs `charter.update` for real) sees through an earlier fake to the
    # original, so `real=` always restores and never stacks.
    for table, make in ((executor.HANDLERS, recorder), (executor.WORLD_CHECKS, check_recorder)):
        for capability in table:
            current = table[capability]
            original = getattr(current, "fake_world_original", current)
            if capability in real:
                monkeypatch.setitem(table, capability, original)
            else:
                monkeypatch.setitem(table, capability, make(capability, original))
    return log
