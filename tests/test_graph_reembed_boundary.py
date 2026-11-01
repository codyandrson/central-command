"""`graph_reembed` is the operator's command line and nothing else's (R4): no agent
tool, no Executor path and no route reaches it, and it holds no gateway."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "central_command" / "graph_reembed.py"


def test_the_module_imports_nothing_from_the_gateway_or_the_executor():
    imported: list[str] = []
    for node in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported += [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
    assert any(i.startswith("central_command.") for i in imported), imported
    assert not [i for i in imported if re.search(r"\b(gateway|executor)\b", i)], imported


def test_the_module_loads_neither_the_gateway_nor_the_runtime_nor_graphiti_core():
    code = (
        "import sys, central_command.graph_reembed\n"
        "print([m for m in sys.modules if m.startswith(("
        "'central_command.gateway', 'central_command.executor', 'central_command.runtime', "
        "'graphiti_core'))])"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_nothing_under_runtime_or_gateway_names_the_module():
    for tier in ("runtime", "gateway"):
        for f in (ROOT / "central_command" / tier).rglob("*.py"):
            assert "graph_reembed" not in f.read_text(encoding="utf-8"), f


def test_no_route_or_heartbeat_action_imports_the_module():
    """Never a cockpit button or a heartbeat action. Words naming the remedy are
    fine; an import is the door."""
    for tier in ("api", "heartbeat"):
        for f in (ROOT / "central_command" / tier).rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                assert not [n for n in names if "graph_reembed" in n], f
