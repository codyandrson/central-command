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
