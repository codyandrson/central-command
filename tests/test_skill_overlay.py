"""The skill overlay's thinnest path: overlay file -> loader -> render at
import -> library -> agent read.

Covers R1 and R2 of epic-instance-facts-relocation (INSTANCE-008): a bundled
skill carries `<!-- overlay: <field> -->` where a deployment-only fact would
go, and the API tier writes this deployment's text there at IMPORT, so the
library holds what agents read and the agent tier stays file-free. An absent
overlay or field removes the marker (the import lists it unresolved); a
MALFORMED marker refuses the import, naming file and line, before any write.

Values here are example text only. The route is called directly, the pattern
every skills import test in this suite uses (there is no TestClient).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import skills as skills_mod
from central_command.skills import overlay
from tests.conftest import needs_pg

pytestmark_db = needs_pg

LONG = "<!-- overlay: layout — moved to the instance overlay by epic-instance-facts-relocation -->"
VALUE = "node-a runs the model, 4 slots"  # example text, not a real layout


# --- render (pure) -----------------------------------------------------------


def test_render_inserts_the_value_and_removes_the_marker():
    out, resolved, unresolved = overlay.render(
        "Layout: <!-- overlay: layout -->.\n", {"layout": VALUE}, source="a.md"
    )
    assert out == f"Layout: {VALUE}.\n"
    assert resolved == ["layout"] and unresolved == []
    assert "overlay" not in out


def test_the_long_form_is_the_same_marker():
    out, resolved, _ = overlay.render(f"x {LONG}.\n", {"layout": VALUE}, source="a.md")
    assert out == f"x {VALUE}.\n" and resolved == ["layout"]


def test_trailing_newline_of_the_value_is_trimmed_by_load(tmp_path):
    (tmp_path / "layout.md").write_text(VALUE + "\n\n")
    assert overlay.load(tmp_path) == {"layout": VALUE}


def test_absent_field_removes_an_inline_marker_and_lists_it_unresolved():
    out, resolved, unresolved = overlay.render(
        "caps users <!-- overlay: limits --> and storage\nend <!-- overlay: limits -->.\n",
        {}, source="a.md",
    )
    assert out == "caps users and storage\nend.\n"
    assert resolved == [] and unresolved == ["limits"]


def test_an_unresolved_own_line_marker_is_removed_with_its_line():
    text = "before\n  <!-- overlay: layout -->\nafter\n"
    out, _, unresolved = overlay.render(text, {}, source="a.md")
    assert out == "before\nafter\n"
    assert unresolved == ["layout"]


def test_a_multi_line_value_takes_the_markers_indentation_on_every_line():
    text = "- item\n  <!-- overlay: layout -->\n- next\n"
    out, resolved, _ = overlay.render(
        text, {"layout": "line one\nline two\n\nline four"}, source="a.md"
    )
    assert out == "- item\n  line one\n  line two\n\n  line four\n- next\n"
    assert resolved == ["layout"]


def test_an_inline_marker_mid_sentence_inside_an_indented_item():
    text = "  not exist\". <!-- overlay: apps --> Next.\n"
    out, _, _ = overlay.render(text, {"apps": "Two apps installed."}, source="a.md")
    assert out == "  not exist\". Two apps installed. Next.\n"
    gone, _, _ = overlay.render(text, {}, source="a.md")
    assert gone == "  not exist\". Next.\n"


@pytest.mark.parametrize("marker", [
    "<!-- overlay -->",
    "<!-- overlay: Bad-Field -->",
    "<!-- overlay: layout",
    "<!-- overlay: layout - moved -->",
    "<!--overlay: layout -->",
])
def test_a_malformed_marker_raises_naming_file_and_line(marker):
    with pytest.raises(ValueError) as exc:
        overlay.render(f"one\ntwo\ntext {marker}\n", {"layout": VALUE}, source="/x/SKILL.md")
    assert "/x/SKILL.md:3" in str(exc.value)


def test_load_of_a_missing_directory_is_an_empty_overlay(tmp_path):
    assert overlay.load(tmp_path / "nope") == {}


def test_load_names_a_non_utf8_file(tmp_path):
    (tmp_path / "layout.md").write_bytes(b"\xff\xfe nope")
    with pytest.raises(ValueError) as exc:
        overlay.load(tmp_path)
    assert "layout.md" in str(exc.value)


def test_load_ignores_files_that_are_not_field_named(tmp_path):
    (tmp_path / "README.md").write_text("notes")
    (tmp_path / "layout.md").write_text(VALUE)
    (tmp_path / "layout.txt").write_text("not md")
    assert overlay.load(tmp_path) == {"layout": VALUE}


def test_the_default_directory_is_under_the_state_dir(monkeypatch):
    monkeypatch.setattr(settings, "skill_overlay_dir", "")
    monkeypatch.setattr(settings, "state_dir", "/var/state/cc-example")
    assert settings.resolved_skill_overlay_dir() == "/var/state/cc-example/skill-overlay"
    monkeypatch.setattr(settings, "skill_overlay_dir", "/srv/overlay")
    assert settings.resolved_skill_overlay_dir() == "/srv/overlay"


def test_nothing_in_the_runtime_tier_imports_the_overlay():
    root = Path(__file__).resolve().parents[1] / "central_command" / "runtime"
    pattern = re.compile(r"skills\.overlay|skills import overlay")
    offenders = [
        str(p) for p in root.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# --- through the import route (needs Postgres) -------------------------------

_CLEAN = (
    "delete from skill where id like 'cc-test-%' or id like 'cc!_test!_%' escape '!'"
)


@pytest.fixture()
async def clean_skills():
    async def wipe():
        conn = await repo._conn()
        try:
            await conn.execute(_CLEAN)
        finally:
            await conn.close()

    await wipe()
    yield
    await wipe()


def _folder(tmp_path: Path, skill_md: str, reference: str) -> Path:
    folder = tmp_path / "ovl-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(skill_md)
    (folder / "references" / "ROUTING.md").write_text(reference)
    return folder


def _overlay(tmp_path: Path, monkeypatch, **fields: str) -> Path:
    d = tmp_path / "overlay"
    d.mkdir()
    for name, text in fields.items():
        (d / f"{name}.md").write_text(text + "\n")
    monkeypatch.setattr(settings, "skill_overlay_dir", str(d))
    return d


async def _import(folder: Path, sid: str = "cc-test-ovl") -> dict:
    return await routes.import_skill_route(
        routes.SkillImportIn(path=str(folder), skill_id=sid)
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_import_writes_the_value_and_reports_names_never_the_value(
    clean_skills, tmp_path, monkeypatch
):
    _overlay(tmp_path, monkeypatch, layout=VALUE)
    folder = _folder(
        tmp_path,
        "---\nname: ovl\ndescription: d\n---\n# G\nHost: <!-- overlay: layout -->\n",
        f"# R\n- item\n  {LONG}\n- other <!-- overlay: missing_field -->.\n",
    )
    before = (await repo.list_events_of_kinds(["skill.imported"]))
    since = before[-1]["id"] if before else 0

    result = await _import(folder)

    assert result["overlay"] == {"resolved": ["layout"], "unresolved": ["missing_field"]}
    docs = {d["doc_key"]: d["content"] for d in await repo.list_skill_docs("cc-test-ovl")}
    assert VALUE in docs["guidance"] and VALUE in docs["routing"]
    assert all("<!-- overlay" not in c for c in docs.values())
    assert "- other." in docs["routing"]

    events = [
        e for e in await repo.list_events_of_kinds(["skill.imported"], since_id=since)
        if e["ref_id"] == "cc-test-ovl"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["overlay"] == result["overlay"]
    assert VALUE not in json.dumps(events[0], default=str)
    assert VALUE not in json.dumps(result)


@pytestmark_db
@pytest.mark.asyncio
async def test_import_without_an_overlay_removes_the_markers_and_lists_the_fields(
    clean_skills, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "skill_overlay_dir", str(tmp_path / "no-such-dir"))
    folder = _folder(tmp_path, "# G\nbody\n  <!-- overlay: layout -->\nend\n", "# R\ntext\n")

    result = await _import(folder)

    assert result["overlay"] == {"resolved": [], "unresolved": ["layout"]}
    (guidance,) = [d for d in await repo.list_skill_docs("cc-test-ovl") if d["doc_key"] == "guidance"]
    assert guidance["content"] == "# G\nbody\nend\n"


@pytestmark_db
@pytest.mark.asyncio
async def test_a_malformed_marker_is_422_naming_file_and_line_and_writes_nothing(
    clean_skills, tmp_path, monkeypatch
):
    _overlay(tmp_path, monkeypatch, layout=VALUE)
    folder = _folder(
        tmp_path, "# G\nfine <!-- overlay: layout -->\n",
        "# R\nok\n<!-- overlay: Not-A-Field -->\n",
    )

    with pytest.raises(HTTPException) as exc:
        await _import(folder)

    assert exc.value.status_code == 422
    assert "ROUTING.md:3" in exc.value.detail
    assert await repo.list_skill_docs("cc-test-ovl") == []
    assert await repo.get_skill("cc-test-ovl") is None


@pytestmark_db
@pytest.mark.asyncio
async def test_the_agent_reads_the_rendered_text(clean_skills, tmp_path, monkeypatch):
    """capabilities_for (the guidance) and the skill's search tool (the
    references) return the text with the value in it and no marker."""
    _overlay(tmp_path, monkeypatch, layout=VALUE, routing_note="Routes by alias.")
    folder = _folder(
        tmp_path,
        "# G\nWhere it runs: <!-- overlay: layout -->\n",
        "# R\nQuokka routing. <!-- overlay: routing_note -->\n",
    )
    await _import(folder)
    await repo.grant_skill("inbox-triage", "cc-test-ovl")

    caps = [c for c in await skills_mod.capabilities_for("inbox-triage") if c.id == "cc-test-ovl"]
    assert len(caps) == 1
    guidance = "\n".join(str(i) for i in caps[0].get_instructions())
    assert f"Where it runs: {VALUE}" in guidance
    assert "<!-- overlay" not in guidance

    found = await skills_mod._make_search("cc-test-ovl")("quokka")
    assert "Routes by alias." in found and "<!-- overlay" not in found


@pytestmark_db
@pytest.mark.asyncio
async def test_the_bundled_litellm_skill_imports_with_the_demo_value(
    clean_skills, tmp_path, monkeypatch
):
    """The demo the ticket names: skills/litellm with an overlay where one
    field reads DEMO; the guidance reads back DEMO where the marker was."""
    _overlay(tmp_path, monkeypatch, model_server_layout="DEMO")
    litellm = Path(__file__).resolve().parents[1] / "skills" / "litellm"

    result = await _import(litellm, sid="cc-test-litellm")

    assert result["overlay"]["resolved"] == ["model_server_layout"]
    assert "model_server_layout" not in result["overlay"]["unresolved"]
    docs = await repo.list_skill_docs("cc-test-litellm")
    assert all("<!-- overlay" not in d["content"] for d in docs)
    (guidance,) = [d for d in docs if d["doc_key"] == "guidance"]
    assert "DEMO" in guidance["content"]
