"""Every agent holds the clock.

The charter stamp (build_charter) tells every agent to call `current_time`
when its session outlives the stamped date — a promise the tool surface must
implement (the guidance-needs-a-mechanism trap: never promise a check nothing
implements). Guarded as a source walk, the repo's idiom for invariants that
span call sites: every base `tools=[...]` list in runtime/ must carry it.
"""

import re
from datetime import datetime
from pathlib import Path

from central_command.runtime.agent import build_charter
from central_command.runtime.tools import current_time

RUNTIME = Path(__file__).resolve().parent.parent / "central_command" / "runtime"

WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"}


def test_current_time_returns_a_parseable_now():
    day, local, _utc = current_time().split(" ", 2)
    assert day in WEEKDAYS
    stamped = datetime.fromisoformat(local)
    assert abs((datetime.now().astimezone() - stamped).total_seconds()) < 60


def test_every_base_toolset_holds_the_clock():
    for path in sorted(RUNTIME.glob("*.py")):
        for match in re.finditer(r"tools=\[([^\]]*)\]", path.read_text(encoding="utf-8")):
            assert "current_time" in match.group(1), (
                f"{path.name} builds an agent without current_time: "
                f"{match.group(0)} — the charter stamp promises this tool "
                "to every agent."
            )


def test_charter_stamp_carries_the_temporal_frame():
    stamp = build_charter()
    assert f"Today's date is {datetime.now().date().isoformat()}" in stamp
    assert "current_time" in stamp
    assert "PAST" in stamp
