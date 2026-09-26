"""deploy/n8n/workflows/ is the source of truth for the email façade (v2.27.0).
These read the SHIPPED files — the only thing a test can hold still — and pin
what the apply script and the control plane both depend on: stable ids, the
token placeholder, credentials by name, and every mode the provider validates
being routed AND every mode the control plane sends being validated."""

import json
import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / "deploy" / "n8n" / "workflows"


def _load(name):
    return json.loads((WF / name).read_text(encoding="utf-8"))


def _node(wf, name):
    return next(n for n in wf["nodes"] if n["name"] == name)


def test_facade_calls_the_provider_by_its_shipped_id():
    facade, provider = _load("cc-email-facade.json"), _load("lib-email-provider.json")
    call = _node(facade, "Call lib-email-provider")
    assert call["parameters"]["workflowId"]["value"] == provider["id"]
    assert facade["id"] and provider["id"]


def test_facade_token_is_a_placeholder_and_only_x_cc_token_is_read():
    code = _node(_load("cc-email-facade.json"), "Auth + unwrap")["parameters"]["jsCode"]
    assert "const TOKEN = '__CC_EMAIL_FACADE_TOKEN__';" in code
    assert "x-cc-token" in code and "Gv" not in code


def test_credentials_are_resolved_by_name_never_by_an_instance_id():
    provider = _load("lib-email-provider.json")
    creds = [n["credentials"] for n in provider["nodes"] if n.get("credentials")]
    assert creds, "no node carries the Gmail credential"
    for c in creds:
        assert c == {"gmailOAuth2": {"id": None, "name": "Gmail account"}}


def test_every_validated_mode_is_routed_and_the_control_plane_modes_are_validated():
    provider = _load("lib-email-provider.json")
    validate = _node(provider, "Validate request")["parameters"]["jsCode"]
    m = re.search(r"if \(!\[([^\]]+)\]\.includes\(mode\)\)", validate)
    validated = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    routed = {r["outputKey"] for r in _node(provider, "Route (mode)")["parameters"]["rules"]["values"]}
    assert validated == routed
    # what central_command/integrations/email_facade.py sends
    assert {"message", "list", "report_spam"} <= validated
    outputs = provider["connections"]["Route (mode)"]["main"]
    assert outputs[-1] == [{"node": "Unmatched route (error)", "type": "main", "index": 0}]
    assert len(outputs) == len(routed) + 1


def test_message_mode_reports_the_unsubscribe_facts_the_contract_reads():
    code = _node(_load("lib-email-provider.json"), "Normalize response")["parameters"]["jsCode"]
    for field in ("list_unsubscribe:", "list_unsubscribe_post:", "authentication_results:", "dkim_signatures:"):
        assert field in code


def test_report_spam_is_the_one_write_and_it_moves_to_spam():
    provider = _load("lib-email-provider.json")
    spam = _node(provider, "Report spam (Gmail API)")
    assert spam["parameters"]["method"] == "POST"
    assert spam["parameters"]["url"].endswith("/modify")
    assert json.loads(spam["parameters"]["jsonBody"]) == {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}
    writes = [n for n in provider["nodes"]
              if n["type"].endswith("httpRequest") and n["parameters"].get("method", "GET") != "GET"]
    assert [n["name"] for n in writes] == ["Report spam (Gmail API)"]


# ── the calendar façade (v2.49.0) ─────────────────────────────────────────────
# It had lived only on the live canvas since 2026-08-07 — the 2026-09-26 clean
# re-deploy found that nothing in the repo could rebuild the EA's calendar path
# (the Executor's create/update/delete_event and the EA's list all go through
# it). Same contract as the email pair: stable ids, a token placeholder, a
# credential by name, and every mode validated == routed.

def test_calendar_facade_calls_the_provider_by_its_shipped_id():
    facade, provider = _load("cc-calendar-facade.json"), _load("lib-google-calendar.json")
    call = _node(facade, "Call lib-google-calendar")
    assert call["parameters"]["workflowId"]["value"] == provider["id"]
    assert facade["id"] and provider["id"]
    assert facade["id"] != _load("cc-email-facade.json")["id"]


def test_calendar_facade_token_is_a_placeholder_and_only_x_cc_token_is_read():
    code = _node(_load("cc-calendar-facade.json"), "Auth + unwrap")["parameters"]["jsCode"]
    assert "const TOKEN = '__CC_CALENDAR_FACADE_TOKEN__';" in code
    assert "x-cc-token" in code and "Gv" not in code
    # the mode allowlist IS the write boundary — pinned so widening it is a diff
    assert "const MODES = ['list', 'create_event', 'update_event', 'delete_event'];" in code


def test_calendar_credential_is_resolved_by_name_never_by_an_instance_id():
    provider = _load("lib-google-calendar.json")
    creds = [n["credentials"] for n in provider["nodes"] if n.get("credentials")]
    assert len(creds) == 4, "the four REST nodes each carry the credential"
    for c in creds:
        assert c == {"googleCalendarOAuth2Api": {"id": None, "name": "Google Calendar account"}}


def test_every_calendar_mode_is_validated_and_routed_and_the_writes_are_the_three_gated_ones():
    provider = _load("lib-google-calendar.json")
    validate = _node(provider, "Validate request")["parameters"]["jsCode"]
    m = re.search(r"const MODES = \[([^\]]+)\]", validate)
    validated = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    routed = {r["outputKey"] for r in _node(provider, "Route (mode)")["parameters"]["rules"]["values"]}
    assert validated == routed == {"list", "create_event", "update_event", "delete_event"}
    # what central_command/integrations/calendar_facade.py sends
    methods = {n["name"]: n["parameters"].get("method", "GET")
               for n in provider["nodes"] if n["type"].endswith("httpRequest")}
    assert sorted(v for v in methods.values()) == ["DELETE", "GET", "PATCH", "POST"]


def test_no_shipped_workflow_carries_a_previous_name_or_an_instance_id():
    """The files are public; the canvas they came from was not."""
    for path in sorted(WF.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert "GrandVision" not in text and "X-Gv-Token" not in text, path.name
        for node in json.loads(text)["nodes"]:
            for cred in (node.get("credentials") or {}).values():
                assert cred.get("id") is None, f"{path.name}: {node['name']} binds a credential by id"
