"""Central Command — a human-supervised agentic team framework."""

import re
from pathlib import Path


def _read_version_file() -> str:
    """The installed version is the `VERSION` file — not git, not the tag,
    and not a literal in this module. `/health`, the OpenAPI document and the
    cockpit's gateway `server.version` all report this value, so a version
    check against any of them means the same thing the updater means
    (`api/update.py:read_product_version` reads the same line for the update
    dialog). Until v2.53.1 this was a hard-coded "0.1.0" that no release ever
    touched, so `/health` answered 0.1.0 on a 2.53.0 install."""
    try:
        text = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    m = re.search(r"^version=(.+)$", text, re.M)
    return m.group(1).strip() if m else "0.0.0"


__version__ = _read_version_file()
