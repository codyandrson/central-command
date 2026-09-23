"""What cannot work is WITHHELD, not offered-to-fail (Jira Data Center flavor
design, 2026-09-23, decision 3).

Under `CC_JIRA_API_FLAVOR=server` the pack machinery drops the Cloud-only
members from every granted pack — the read tools `jira_list_filters`,
`jira_list_dashboards`, `jira_list_gadgets` and the gated capability
`jira.create_dashboard`. The toolset, the generated charter section and the
gateway's granted-capability policy check all derive from the SAME filtered
view, so an agent is never shown a tool that always 404s and never coached
toward a capability it cannot hold.

`known_capability_names()` stays UNFILTERED on purpose: a withheld capability
is ungranted, not invented, and the propose-time validity check must keep
telling those two apart.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.runtime import packs

CLOUD_ONLY_TOOLS = {"jira_list_filters", "jira_list_dashboards", "jira_list_gadgets"}
DATA_CENTER_TOOLS = {"jira_get_issue", "jira_search_issues", "jira_get_transitions",
                     "jira_list_projects", "jira_list_fields"}


@pytest.fixture
def flavor(monkeypatch):
    def _set(value):
        monkeypatch.setattr(settings, "jira_api_flavor", value)

    return _set


def _tool_names(toolset) -> set[str]:
    return set(toolset.tools)


def test_jira_read_withholds_the_cloud_only_tools_under_server(flavor):
    flavor("server")
    names = _tool_names(packs.toolset_for(("jira-read",)))
    assert not (names & CLOUD_ONLY_TOOLS), names & CLOUD_ONLY_TOOLS
    assert DATA_CENTER_TOOLS <= names


def test_jira_read_offers_everything_under_cloud(flavor):
    flavor("cloud")
    names = _tool_names(packs.toolset_for(("jira-read",)))
    assert CLOUD_ONLY_TOOLS <= names
    assert DATA_CENTER_TOOLS <= names


def test_the_charter_section_omits_create_dashboard_under_server(flavor):
    flavor("server")
    text = packs.charter_section(("jira-propose",))
    assert "jira.create_dashboard" not in text
    # The rest of the pack is untouched — withholding is per-member.
    assert "jira.create_filter" in text and "jira.create_issue" in text
    flavor("cloud")
    assert "jira.create_dashboard" in packs.charter_section(("jira-propose",))


def test_granted_capability_names_drops_it_but_known_names_keep_it(flavor):
    flavor("server")
    granted = packs.granted_capability_names(("jira-propose",))
    assert "jira.create_dashboard" not in granted
    assert "jira.create_filter" in granted
    # UNGRANTED, never INVENTED: the propose-time validity check still knows
    # the name, so a draft naming it is refused as out-of-grant, not as a
    # hallucination.
    assert "jira.create_dashboard" in packs.known_capability_names()
    flavor("cloud")
    assert "jira.create_dashboard" in packs.granted_capability_names(("jira-propose",))


def test_the_default_flavor_changes_nothing(flavor):
    flavor("cloud")
    assert packs.PACKS["jira-read"].tool_names == tuple(
        packs._offered(packs.PACKS["jira-read"])[0]
    )


def test_create_filter_tells_the_agent_it_cannot_check_for_duplicates():
    cap = next(c for c in packs.PACKS["jira-propose"].capabilities
               if c.name == "jira.create_filter")
    assert "Data Center" in cap.notes and "duplicates" in cap.notes


def test_only_create_dashboard_is_flavor_restricted():
    restricted = {c.name for p in packs.PACKS.values() for c in p.capabilities
                  if c.flavors != ("cloud", "server")}
    assert restricted == {"jira.create_dashboard"}
