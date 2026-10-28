"""Story 7.6 (GATE-006, DL-142): the read-version stale-bounce field is gone from
every drafting instruction. The Executor re-reads the world at execution time, so
no agent is told to fill `target_ref.read_version`. Stored rows keep whatever they
carry, and the contract's `target_ref` stays a dict, so old rows still load."""

from __future__ import annotations

import re
from pathlib import Path

from central_command.contract import Action, Reversibility
from central_command.gateway import executor
from central_command.integrations import jira

ROOT = Path(__file__).resolve().parent.parent
QUOTED = re.compile(r"""['"]read_version['"]""")

# Everything an agent reads when drafting: pack notes, the charter/coach/reflection
# prompts, every tool's target_ref, the contract field, and skill references.
INSTRUCTION_SOURCES = [
    "central_command/runtime/packs.py",
    "central_command/runtime/run.py",
    "central_command/runtime/reflection.py",
    "central_command/runtime/tools.py",
    "central_command/runtime/spike_model.py",
    "central_command/contract/models.py",
]


def test_no_drafting_instruction_names_the_retired_field():
    offenders = []
    files = [ROOT / p for p in INSTRUCTION_SOURCES]
    files += sorted((ROOT / "skills").rglob("*.md"))
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if QUOTED.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert offenders == []


def test_pack_notes_do_not_mention_read_version_at_all():
    from central_command.runtime import packs

    blob = repr(packs.PACKS)
    assert "read_version" not in blob


async def test_a_stored_row_with_read_version_still_loads_and_executes(monkeypatch):
    action = Action(
        capability="jira.create_project",
        arguments={"key": "CS", "name": "Customer Service"},
        target_ref={"system": "jira", "id": "CS", "read_version": "unknown"},
        reversibility=Reversibility.irreversible,
    )
    assert action.target_ref["read_version"] == "unknown"
    assert Action(**action.model_dump(mode="json")).target_ref == action.target_ref

    async def fake_create_project(key, name, project_type_key="software"):
        return {"ok": True, "kind": "jira", "operation": "createProject",
                "project": {"key": key, "id": "10099", "name": name, "url": "u"}}

    monkeypatch.setattr(jira, "create_project", fake_create_project)
    text = await executor.HANDLERS[action.capability](action.arguments, "lee", "jira-expert")
    assert "CS" in text
