"""The dispatch token-budget valve is gone (story 11.5, R11, MODELS-018; DL-148).

What these pin: no `dispatch_token_budget` anywhere under `central_command/`
(a source walk), the dispatcher's status valves are exactly concurrency and the
approval limit (and no `tokens_spent`), and an existing `.env` line for
`CC_DISPATCH_TOKEN_BUDGET` warns once at start with its own reason instead of
being silently ignored.
"""

import logging
import re
from pathlib import Path

from central_command.api import app as app_module
from central_command.config import RETIRED_ENV_KEYS
from central_command.ingest import dispatcher as dispatcher_mod

ROOT = Path(__file__).resolve().parent.parent
KEY = "CC_DISPATCH" + "_TOKEN_BUDGET"


def test_no_token_budget_setting_remains_under_central_command():
    pattern = re.compile(r"dispatch_token_budget|token_budget|tokens_spent|" + KEY, re.I)
    hits = []
    for p in (ROOT / "central_command").rglob("*.py"):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if pattern.search(line) and "RETIRED_ENV_KEYS" not in line and not (
                p.name == "config.py" and f'"{KEY}"' in line
            ):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert hits == []


async def test_status_valves_are_concurrency_and_the_approval_limit_only():
    status = await dispatcher_mod.dispatcher.status()
    assert set(status["valves"]) == {"concurrency", "approval_limit"}
    assert "tokens_spent" not in status


def test_the_retired_key_warns_once_with_its_own_reason(monkeypatch, caplog):
    for key in RETIRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(KEY, "0")
    with caplog.at_level(logging.WARNING):
        app_module._warn_retired_settings()
    warnings = [r.getMessage() for r in caplog.records if "ignored retired setting" in r.getMessage()]
    assert len(warnings) == 1
    assert KEY in warnings[0]
    assert RETIRED_ENV_KEYS[KEY] in warnings[0]
    assert "retries until the dependency is back" not in warnings[0]
