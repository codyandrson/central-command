"""requirements.lock is UNIVERSAL (v2.46.0) — the markers are load-bearing.

The lock used to be a Linux ``pip freeze``: no platform markers at all. With
``CC_AIRGAP=1`` the single-node installer installs from the lock instead of
resolving, so on Windows it tried to install uvloop (no Windows wheels exist)
and onnxruntime 1.29.0 (magika 0.6.3 caps it at 1.20.1 on win32 only) and
failed before the first package landed (work-site report, 2026-09-24). A
universal resolution carries each platform-specific pin with its marker.
These tests fail the suite if a regeneration drops them again.
"""

from __future__ import annotations

import pathlib
import re

LOCK = pathlib.Path(__file__).resolve().parents[1] / "requirements.lock"
PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)\s*(?:;\s*(.+))?$")


def _pins() -> list[tuple[str, str, str]]:
    rows = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN_RE.match(line)
        assert m, f"requirements.lock: not a pin: {line!r}"
        rows.append((m.group(1).lower(), m.group(2), (m.group(3) or "").strip()))
    return rows


def test_lock_is_only_pins():
    pins = _pins()
    assert len(pins) > 100, "requirements.lock looks truncated"


def test_uvloop_is_marked_not_for_windows():
    """uvloop publishes no Windows wheels; uvicorn's own extra excludes it
    there and the lock must too."""
    markers = [m for name, _, m in _pins() if name == "uvloop"]
    assert markers, "uvloop missing from the lock"
    for m in markers:
        assert "sys_platform != 'win32'" in m, f"uvloop pinned without a win32 exclusion: {m!r}"


def test_onnxruntime_is_forked_by_platform():
    """magika 0.6.3 (pinned by markitdown) caps onnxruntime at 1.20.1 on win32
    only. A single unmarked pin is unsatisfiable on one platform or the other:
    the lock must carry one pin per side of the fork."""
    rows = [(v, m) for name, v, m in _pins() if name == "onnxruntime"]
    assert len(rows) == 2, f"expected a win32/non-win32 fork for onnxruntime, got {rows}"
    by_marker = {m: v for v, m in rows}
    assert "sys_platform == 'win32'" in by_marker, rows
    assert "sys_platform != 'win32'" in by_marker, rows
    assert by_marker["sys_platform == 'win32'"] <= "1.20.1"


def test_no_duplicate_unmarked_pins():
    """Two pins for one package are only legal when every one carries a marker
    (a fork). An unmarked duplicate is a corrupted regeneration."""
    seen: dict[str, list[str]] = {}
    for name, v, m in _pins():
        seen.setdefault(name, []).append(m)
    for name, markers in seen.items():
        if len(markers) > 1:
            assert all(markers), f"{name}: duplicate pin without a marker: {markers}"


def test_header_names_the_regeneration_script():
    head = LOCK.read_text(encoding="utf-8")[:1500]
    assert "scripts/regen_lock.sh" in head
    assert "--universal" in head or "UNIVERSAL" in head
