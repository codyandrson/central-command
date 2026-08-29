"""Charter templates render ONCE at hire, and every shipped template is valid.

2026-08-21 setup & onboarding design, decision 8. Two things are guarded:

1. The mechanics — placeholders fill from settings, [[if-pack]] sections
   resolve against the ACTUAL grants, and every malformed shape fails loudly
   (a charter must never reach the record with an unrendered marker in it).

2. The shipped templates — every charter constant in the repo renders clean
   against its own template's default packs, every [[if-pack]] names a real
   pack id, and the default operator_name keeps pre-onboarding hires reading
   exactly as before. A marker typo fails the suite in the commit that adds
   it, not at the first hire on someone else's machine.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

from central_command.runtime import templates as t
from central_command.runtime.packs import PACKS

PROFILE_MODULES = (
    "auditor_profile", "coach_profile", "ea_profile", "editor_profile",
    "graph_auditor_profile", "graph_curator_profile",
    "litellm_manager_profile", "mcp_manager_profile", "orchestrator_profile",
    "steward_profile",
)


# The two founder charters that live outside a *_profile module (they predate
# the convention). Both reach a model through `agent.builtin_charter`.
LOOSE_CHARTERS = ("agent", "jira_expert")

# Every id `agent.builtin_charter` answers for — the founders, whose v0 is a
# code constant resolved on every read because they have no hire event.
FOUNDER_IDS = ("inbox-triage", "jira-expert", "auditor", "orchestrator",
               "litellm-manager", "coach")

IDENTITY_LINE = ('Your human team lead is <<operator_name>> — '
                 '"the operator" throughout this charter.')


def _shipped_charters():
    yield "templates.HIRED_AGENT_CHARTER", t.HIRED_AGENT_CHARTER
    for mod in PROFILE_MODULES + LOOSE_CHARTERS:
        m = importlib.import_module(f"central_command.runtime.{mod}")
        yield f"{mod}.CHARTER", m.CHARTER


def test_placeholder_fills_and_unknown_raises():
    assert t.render_charter("hi <<operator_name>>", packs=(),
                            values={"operator_name": "Lee"}) == "hi Lee"
    with pytest.raises(ValueError, match="unknown placeholder"):
        t.render_charter("hi <<no_such_thing>>", packs=())


def test_sections_resolve_against_actual_grants():
    text = "always\n[[if-pack jira-read]]\njira stuff\n[[end-pack]]\ntail\n"
    assert t.render_charter(text, packs=("jira-read",)) == "always\njira stuff\ntail\n"
    assert t.render_charter(text, packs=()) == "always\ntail\n"


@pytest.mark.parametrize("bad", [
    "[[if-pack jira-read]]\nunclosed",
    "orphan\n[[end-pack]]\n",
    "[[if-pack a]]\n[[if-pack b]]\nnested\n[[end-pack]]\n[[end-pack]]\n",
])
def test_malformed_sections_fail_loudly(bad):
    with pytest.raises(ValueError):
        t.render_charter(bad, packs=("jira-read", "a", "b"))


def test_default_operator_name_preserves_the_old_reading(monkeypatch):
    # Pre-onboarding hires must read exactly as before the placeholder landed.
    # Pins the SETTING, not the ambient .env (which legitimately names the
    # operator on a real instance — this asserts the DEFAULT's behavior).
    from central_command.config import settings

    monkeypatch.setattr(settings, "operator_name", "the operator")
    rendered = t.TEMPLATES["general"].render(name="X", mission="Y")
    assert "You work for the operator — the human team lead" in rendered
    assert "<<" not in rendered


def test_every_shipped_charter_renders_clean():
    """The commit that adds a bad marker fails here, not at first hire."""
    all_packs = set(PACKS)
    for origin, text in _shipped_charters():
        for pack_id in re.findall(r"\[\[if-pack ([a-z0-9-]+)\]\]", text):
            assert pack_id in all_packs, f"{origin}: [[if-pack {pack_id}]] names no real pack"
        # Render with EVERY pack granted, so no section is dropped and every
        # placeholder in any section is exercised.
        filled = text.format(name="X", mission="Y") if "{name}" in text else text
        rendered = t.render_charter(filled, packs=all_packs)
        leftover = re.findall(r"<<[a-z_]+>>|\[\[[^\]]*\]\]", rendered)
        assert not leftover, f"{origin}: unrendered markers {leftover}"


def test_every_shipped_charter_names_the_operator():
    """The identity line (decision 8's one instance constant) is in all of them.

    One line per charter, not a peppering: existing generic uses of "the
    operator" are ROLE references and stay as they are.
    """
    for origin, text in _shipped_charters():
        if origin == "templates.HIRED_AGENT_CHARTER":
            # The hire skeleton already opens by naming the operator ("You work
            # for <<operator_name>> — the human team lead"); a second sentence
            # saying the same thing is the peppering this test exists to avoid.
            assert "<<operator_name>>" in text
            continue
        assert IDENTITY_LINE in text, f"{origin}: no operator identity line"


def test_founder_v0_is_rendered_on_read(monkeypatch):
    """Founders have no hire event, so `builtin_charter` is where they render.

    schema.sql seeds only the ROLE one-liner into `agent.charter` (see
    `test_schema_seeds_no_charter_templates`), so there is no row a boot sweep
    could render into — the read path is the seam.
    """
    from central_command.config import settings
    from central_command.runtime.agent import builtin_charter

    monkeypatch.setattr(settings, "operator_name", "Testy McTestface")
    assert FOUNDER_IDS
    for agent_id in FOUNDER_IDS:
        text = builtin_charter(agent_id)
        assert "Your human team lead is Testy McTestface" in text, agent_id
        assert not re.findall(r"<<[a-z_]+>>|\[\[[^\]]*\]\]", text), agent_id
    # Idempotent by construction: rendered text carries no markers, so a second
    # pass through the renderer is a no-op.
    once = builtin_charter("coach")
    assert t.render_v0(once) == once


def test_render_v0_refuses_a_charter_with_pack_sections():
    """Sections need the agent's ACTUAL grants — dropping them silently is the
    "guidance needs a mechanism" failure wearing a render's clothes."""
    with pytest.raises(ValueError, match="real grants"):
        t.render_v0("head\n[[if-pack jira-read]]\njira\n[[end-pack]]\n")


def test_no_v0_charter_constant_reaches_a_model_unrendered():
    """Source walk: every `... or <SOMETHING>CHARTER` fallback renders first.

    Those builder fallbacks (`system_prompt=charter or CHARTER`) are the other
    place a shipped template reaches a model without a hire. A new one that
    forgets `render_v0` ships `<<operator_name>>` to the model verbatim, and no
    unit test would notice — this one fails in the commit that adds it.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "central_command"
    bad = []
    for path in root.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "`" in stripped:
                continue          # prose: comments and docstrings quote the shape
            if re.search(r"\bor\s+[A-Z_]*CHARTER\b", line):
                bad.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert not bad, "unrendered v0 charter fallback(s):\n" + "\n".join(bad)


def test_schema_seeds_no_charter_templates():
    """The founding seed puts the ROLE one-liner in `agent.charter`, not a
    charter — nothing renders at initdb, so a marker seeded there would reach
    an agent verbatim. Fails loudly if the extraction finds no founders at all,
    so a schema refactor cannot silently drop this coverage.
    """
    schema = (pathlib.Path(__file__).resolve().parents[1]
              / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    block = re.search(
        r"insert into agent \(id, name, model, charter\) values(.*?)on conflict",
        schema, re.DOTALL)
    assert block, "schema.sql: founding-roster insert not found"
    rows = re.findall(r"\(\s*'([a-z-]+)',[^)]*?,\s*'((?:[^']|'')*)'\s*\)", block.group(1))
    assert len(rows) >= 6, f"schema.sql: extracted only {len(rows)} founder rows"
    assert {r[0] for r in rows} >= set(FOUNDER_IDS)
    for agent_id, seeded in rows:
        assert not re.findall(r"<<[a-z_]+>>|\[\[[^\]]*\]\]", seeded), (
            f"{agent_id}: schema.sql seeds a template marker nothing renders")
