"""`update.sh rollback` must retire the cockpit's durable success record.

The dialog reads `status.target == latest and state == "success"` as "Update
Complete" and hides Apply. After a rollback the rolled-back version is offered
again — with the old record intact it could never be applied from the cockpit
(2026-09-19 Windows run). This runs the script's own sed line on a real record.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/single/update.sh"


@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")
def test_rollback_rewrites_a_success_record(tmp_path):
    body = SCRIPT.read_text(encoding="utf-8")
    rollback = body[body.index("cmd_rollback()"):]
    m = re.search(r"^\s*(sed -i .*) \"\$HERE/\.update/status\.json\"$", rollback, re.M)
    assert m, "cmd_rollback no longer rewrites .update/status.json"
    record = tmp_path / "status.json"
    record.write_text(json.dumps({"state": "success", "phase": "done", "target": "9.9.9"},
                                 separators=(",", ":")))
    subprocess.run(f"{m.group(1)} {record}", shell=True, check=True)
    assert json.loads(record.read_text())["state"] == "rolled_back"
