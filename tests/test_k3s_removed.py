"""deploy/k3s/removed.txt is the tombstone list cc-update.sh deletes from.

Two ways a line can lie: malformed (the updater would delete the wrong
thing or die mid-apply), or naming a resource a manifest still declares
(the updater would delete it and the next apply would resurrect it —
a restart loop wearing a cleanup's clothes).
"""

import re
from pathlib import Path

K3S = Path(__file__).resolve().parent.parent / "deploy" / "k3s"


def _tombstones():
    lines = []
    for raw in (K3S / "removed.txt").read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def test_lines_are_ns_kind_name():
    for line in _tombstones():
        assert len(line.split()) == 3, f"not '<namespace> <kind> <name>': {line!r}"


def test_no_tombstone_still_has_a_manifest():
    declared = set()
    for manifest in K3S.glob("*.yaml"):
        text = manifest.read_text()
        for kind, name in re.findall(
            r"^kind:\s*(\S+).*?^\s+name:\s*(\S+)", text, re.MULTILINE | re.DOTALL
        ):
            declared.add((kind.lower(), name))
    for line in _tombstones():
        _, kind, name = line.split()
        assert (kind.lower(), name) not in declared, (
            f"{line!r} is tombstoned but a manifest in deploy/k3s/ still "
            "declares it — apply would resurrect it right after the delete"
        )
