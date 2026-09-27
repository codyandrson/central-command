"""A fresh database must carry the whole first-run experience (v2.51.0).

The 2026-09-26 clean re-deploy of the reference deployment found three things
a stranger's install would have hit: no team-tour schedule (the 2026-08-23
design said "setup's last act creates it" and no installer did — no tour, and
no EA, which the tour's first run hires), no façade tokens (apply-workflows
died on the empty one), and an inbox-triage tool surface narrower than the
charter assumes. These pin the seeds and the generation lines by reading the
files — the only thing a test can hold still."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
K3S_SETUP = (ROOT / "deploy" / "k3s" / "setup.sh").read_text(encoding="utf-8")

INBOX_WORKING_SET = ("web-read", "web-search", "calendar-read",
                     "catalog-read", "catalog-propose", "confluence-read")


def test_the_team_tour_is_seeded_disabled_as_a_one_shot():
    m = re.search(r"\('team-tour',\s*'[^']+',\s*'at',\s*'(\{[^']*\})',\s*'ea\.contact',\s*'(\{[^']*\})',\s*'seed:heartbeat'\)", SCHEMA)
    assert m, "schema.sql does not seed the team-tour schedule"
    assert "2000-01-01" in m.group(1), "the `at` must be in the past so the engine never fires it on its own"
    assert '"kind": "onboarding_tour"' in m.group(2)
    # The founding insert never sets `enabled`, so the column default (false) applies.
    block = SCHEMA[m.start() - 400:m.end()]
    assert "enabled" not in block.split("values")[-1]


def test_inbox_triage_working_set_is_seeded_and_defaulted():
    from central_command.runtime.packs import DEFAULT_PACKS, PACKS
    for pack in INBOX_WORKING_SET:
        assert pack in PACKS, pack
        assert pack in DEFAULT_PACKS["inbox-triage"], pack
        assert re.search(rf"\('inbox-triage',\s*'{pack}',\s*'seed:", SCHEMA), f"{pack} is not seeded"


def test_the_k3s_app_phase_generates_both_facade_tokens_and_applies_the_workflows():
    for var in ("CC_EMAIL_FACADE_TOKEN", "CC_CALENDAR_FACADE_TOKEN"):
        assert var in K3S_SETUP, var
    gen = re.search(r"for tokvar in CC_EMAIL_FACADE_TOKEN CC_CALENDAR_FACADE_TOKEN; do(.*?)done", K3S_SETUP, re.S)
    assert gen and "openssl rand -hex" in gen.group(1)
    assert "is_placeholder" in gen.group(1), "an existing token must never be overwritten"
    assert 'apply-workflows.sh" --k3s' in K3S_SETUP
