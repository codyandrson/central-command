"""The skill overlay's thinnest path: overlay file -> loader -> render at
import -> library -> agent read.

Covers R1, R2 and R3 of epic-instance-facts-relocation (INSTANCE-008): a bundled
skill carries `<!-- overlay: <field> -->` where a deployment-only fact would
go, and the API tier writes this deployment's text there at IMPORT, so the
library holds what agents read and the agent tier stays file-free. An absent
overlay or field removes the marker (the import lists it unresolved); a
MALFORMED marker refuses the import, naming file and line, before any write.
R3 is the index: every marker under skills/ is the one short grammar and names
a field in `overlay.FIELDS`, every field has a marker, nothing under .claude/
is marked, and `overlay check` says which fields an overlay resolves without
ever printing a value.

Values here are example text only. The route is called directly, the pattern
every skills import test in this suite uses (there is no TestClient).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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

ROOT = Path(__file__).resolve().parents[1]
SHORT = re.compile(r"<!-- overlay: ([a-z0-9_]+) -->")
VALUE = "node-a runs the model, 4 slots"  # example text, not a real layout


# --- render (pure) -----------------------------------------------------------


def test_render_inserts_the_value_and_removes_the_marker():
    out, resolved, unresolved = overlay.render(
        "Layout: <!-- overlay: layout -->.\n", {"layout": VALUE}, source="a.md"
    )
    assert out == f"Layout: {VALUE}.\n"
    assert resolved == ["layout"] and unresolved == []
    assert "overlay" not in out


def test_the_retired_long_form_is_malformed():
    long = "<!-- overlay: layout — moved to the instance overlay by epic-instance-facts-relocation -->"
    with pytest.raises(ValueError) as exc:
        overlay.render(f"x {long}.\n", {"layout": VALUE}, source="a.md")
    assert "a.md:1" in str(exc.value)


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
    "<!-- overlay: layout — moved to the instance overlay by epic-instance-facts-relocation -->",
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


# --- the index: FIELDS against the tree (R3) -----------------------------------


def _markers_in_tree() -> dict[str, list[str]]:
    """Every `<!-- overlay` occurrence under skills/ (any file type), by field;
    a non-short occurrence is returned under the key '?'."""
    found: dict[str, list[str]] = {}
    for f in sorted((ROOT / "skills").rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = str(f.relative_to(ROOT))
        for lineno, line in enumerate(text.splitlines(), start=1):
            opened = len(re.findall(r"<!--\s*overlay", line))
            short = SHORT.findall(line)
            for field in short:
                found.setdefault(field, []).append(f"{rel}:{lineno}")
            if opened != len(short):
                found.setdefault("?", []).append(f"{rel}:{lineno}")
    return found


def test_every_marker_under_skills_is_the_short_grammar_and_names_a_field():
    found = _markers_in_tree()
    assert found.pop("?", []) == []
    assert sorted(set(found) - set(overlay.FIELDS)) == []


def test_every_field_has_a_marker_and_the_count_matches_the_tree():
    found = _markers_in_tree()
    assert sorted(set(overlay.FIELDS) - set(found)) == []
    assert list(overlay.FIELDS) == sorted(overlay.FIELDS)
    assert all(re.fullmatch(r"[a-z0-9_]+", k) for k in overlay.FIELDS)
    assert all(d.strip() and "\n" not in d for d in overlay.FIELDS.values())
    # the same count `git grep -h -o '<!-- overlay:' -- skills | wc -l` gives
    total = sum(len(v) for v in found.values())
    grep = subprocess.run(
        ["git", "grep", "-h", "-o", "<!-- overlay:", "--", "skills"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if grep.returncode in (0, 1):  # outside a git checkout there is nothing to compare
        assert len(grep.stdout.splitlines()) == total


def test_no_marker_under_the_claude_directory():
    offenders = [
        str(f.relative_to(ROOT))
        for f in (ROOT / ".claude").rglob("*")
        if f.is_file() and re.search(r"<!--\s*overlay", f.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []


def test_no_planning_tail_survives_in_the_skills_or_claude_trees():
    offenders = [
        str(f.relative_to(ROOT))
        for top in ("skills", ".claude")
        for f in (ROOT / top).rglob("*")
        if f.is_file() and "moved to the instance overlay" in f.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


# --- check: which fields an overlay resolves ---------------------------------


def _run_check(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "central_command.skills.overlay", "check", *args],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_check_against_an_empty_dir_lists_every_field_unresolved_and_exits_1(tmp_path):
    r = _run_check("--dir", str(tmp_path))
    assert r.returncode == 1
    for field in overlay.FIELDS:
        assert any(
            line.startswith("UNRESOLVED") and f" {field} " in line for line in r.stdout.splitlines()
        ), field
    assert "RESOLVED  " not in r.stdout.replace("UNRESOLVED", "")
    assert "skills/litellm/SKILL.md" in r.stdout  # the marking files are named


def test_check_with_a_missing_dir_is_the_same_empty_overlay(tmp_path):
    r = _run_check("--dir", str(tmp_path / "nope"))
    assert r.returncode == 1 and "(absent)" in r.stdout


def test_check_against_a_dir_with_every_field_exits_0(tmp_path):
    planted = "SECRET-PLANTED-VALUE-7731"
    for field in overlay.FIELDS:
        (tmp_path / f"{field}.md").write_text(f"{planted} for {field}\n")
    r = _run_check("--dir", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNRESOLVED" not in r.stdout and "UNUSED" not in r.stdout
    assert len([l for l in r.stdout.splitlines() if l.startswith("RESOLVED")]) == len(overlay.FIELDS)
    assert planted not in r.stdout and planted not in r.stderr


def test_check_names_an_extra_file_unused_and_never_prints_a_value(tmp_path):
    planted = "SECRET-PLANTED-VALUE-9152"
    for field in overlay.FIELDS:
        (tmp_path / f"{field}.md").write_text(planted + "\n")
    (tmp_path / "not_a_field.md").write_text(planted + "\n")
    (tmp_path / "README.txt").write_text(planted)
    r = _run_check("--dir", str(tmp_path))
    assert r.returncode == 0  # an unused file is reported, not a failure
    assert any(l.startswith("UNUSED") and "not_a_field.md" in l for l in r.stdout.splitlines())
    assert "README" not in r.stdout
    assert planted not in r.stdout and planted not in r.stderr


def test_check_one_field_missing_exits_1_and_an_empty_file_is_unresolved(tmp_path):
    names = list(overlay.FIELDS)
    for field in names[1:]:
        (tmp_path / f"{field}.md").write_text("text\n")
    (tmp_path / f"{names[0]}.md").write_text("\n")
    r = _run_check("--dir", str(tmp_path))
    assert r.returncode == 1
    unresolved = [l for l in r.stdout.splitlines() if l.startswith("UNRESOLVED")]
    assert len(unresolved) == 1 and names[0] in unresolved[0] and "(empty file)" in unresolved[0]


def test_check_flags_a_marker_naming_no_field_and_a_malformed_one(tmp_path):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "SKILL.md").write_text("text <!-- overlay: stray_field -->\n")
    r = _run_check("--dir", str(tmp_path / "ovl"), "--skills", str(skills))
    assert r.returncode == 1 and "UNKNOWN     stray_field" in r.stdout
    (skills / "demo" / "SKILL.md").write_text("a\nb <!-- overlay: Bad -->\n")
    r = _run_check("--dir", str(tmp_path / "ovl"), "--skills", str(skills))
    assert r.returncode == 1 and "SKILL.md:2" in r.stderr and r.stdout == ""


def test_check_defaults_to_the_resolved_overlay_dir(tmp_path):
    state = tmp_path / "state"
    (state / "skill-overlay").mkdir(parents=True)
    (state / "skill-overlay" / "model_server_layout.md").write_text("x\n")
    env = {**os.environ, "CC_STATE_DIR": str(state), "CC_SKILL_OVERLAY_DIR": ""}
    r = subprocess.run(
        [sys.executable, "-m", "central_command.skills.overlay", "check"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert r.returncode == 1
    assert str(state / "skill-overlay") in r.stdout
    assert any(l.startswith("RESOLVED") and "model_server_layout" in l for l in r.stdout.splitlines())


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
        "# R\n- item\n  <!-- overlay: layout -->\n- other <!-- overlay: missing_field -->.\n",
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
async def test_a_marker_in_the_frontmatter_is_refused_and_a_body_line_is_the_file_line(
    clean_skills, tmp_path, monkeypatch
):
    """Frontmatter becomes the skill's id, title and summary: a value must not
    be rendered into them, so a marker there refuses; and a malformed marker
    after the frontmatter is named by its line in the FILE, not in the body."""
    _overlay(tmp_path, monkeypatch, layout=VALUE)
    bad_front = _folder(
        tmp_path, "---\nname: ovl\ndescription: runs on <!-- overlay: layout -->\n---\n# G\n", "# R\n"
    )
    with pytest.raises(HTTPException) as exc:
        await _import(bad_front)
    assert exc.value.status_code == 422 and "SKILL.md:3" in exc.value.detail
    assert await repo.get_skill("cc-test-ovl") is None

    (bad_front / "SKILL.md").write_text(
        "---\nname: ovl\ndescription: d\n---\n\n# G\n<!-- overlay: Bad -->\n"
    )
    with pytest.raises(HTTPException) as exc:
        await _import(bad_front)
    assert "SKILL.md:7" in exc.value.detail


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
