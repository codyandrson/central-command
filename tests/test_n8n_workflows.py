"""deploy/n8n/workflows/ is the source of truth for the email façade (v2.27.0).
These read the SHIPPED files — the only thing a test can hold still — and pin
what the apply script and the control plane both depend on: stable ids, the
token placeholder, credentials by name, and every mode the provider validates
being routed AND every mode the control plane sends being validated."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert "const MODES = ['list', 'create_event', 'update_event', 'delete_event', 'get_event'];" in code


def test_calendar_credential_is_resolved_by_name_never_by_an_instance_id():
    provider = _load("lib-google-calendar.json")
    creds = [n["credentials"] for n in provider["nodes"] if n.get("credentials")]
    assert len(creds) == 5, "the five REST nodes each carry the credential"
    for c in creds:
        assert c == {"googleCalendarOAuth2Api": {"id": None, "name": "Google Calendar account"}}


def test_every_calendar_mode_is_validated_and_routed_and_the_writes_are_the_three_gated_ones():
    provider = _load("lib-google-calendar.json")
    validate = _node(provider, "Validate request")["parameters"]["jsCode"]
    m = re.search(r"const MODES = \[([^\]]+)\]", validate)
    validated = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    routed = {r["outputKey"] for r in _node(provider, "Route (mode)")["parameters"]["rules"]["values"]}
    assert validated == routed == {"list", "get_event", "create_event", "update_event", "delete_event"}
    # what central_command/integrations/calendar_facade.py sends
    methods = {n["name"]: n["parameters"].get("method", "GET")
               for n in provider["nodes"] if n["type"].endswith("httpRequest")}
    assert sorted(v for v in methods.values()) == ["DELETE", "GET", "GET", "PATCH", "POST"]
    assert methods["Get event (GCal REST)"] == "GET"
    assert "sendUpdates=all" in _node(provider, "Delete event (GCal REST)")["parameters"]["url"]


def _run_node(tmp_path, name, response):
    """A shipped Code node, run by node with n8n's two accessors stubbed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node needed")
    code = _node(_load("lib-google-calendar.json"), name)["parameters"]["jsCode"]
    script = tmp_path / "code_node.js"
    item = f"{{ json: {json.dumps(response)} }}"
    script.write_text(
        "const $ = () => ({ first: () => ({ json: { calendar_id: 'primary', max_results: 250 } }) });\n"
        f"const $input = {{ first: () => ({item}), all: () => [{item}] }};\n"
        f"const out = (function () {{\n{code}\n}})();\n"
        "process.stderr.write(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stderr)[0]["json"]


def _run_list_node(tmp_path, response):
    return _run_node(tmp_path, "Return: list", response)


def test_a_get_event_request_stays_a_read(tmp_path):
    """The validator's last line is the delete: a read that fell through to it
    would route to the DELETE node."""
    out = _run_node(tmp_path, "Validate request", {"mode": "get_event", "event_id": "ev1"})
    assert out == {"mode": "get_event", "calendar_id": "primary", "event_id": "ev1"}


def test_get_event_returns_the_fields_of_the_restore_record(tmp_path):
    out = _run_node(tmp_path, "Return: get_event", {"statusCode": 200, "body": {
        "id": "ev1", "status": "confirmed", "summary": "Budget review",
        "start": {"dateTime": "2026-11-02T10:00:00Z"}, "end": {"dateTime": "2026-11-02T10:30:00Z"},
        "organizer": {"email": "jane.doe@example.com"},
        "attendees": [{"email": "a@example.com"}, {"email": "b@example.com"}],
    }})
    assert out["ok"] is True
    event = out["event"]
    assert event["event_id"] == "ev1" and event["summary"] == "Budget review"
    assert event["start"] == "2026-11-02T10:00:00Z" and event["end"] == "2026-11-02T10:30:00Z"
    assert event["attendee_count"] == 2 and event["organizer"] == "jane.doe@example.com"


@pytest.mark.parametrize("response", [
    {"statusCode": 404, "body": {"error": {"message": "Not Found"}}},
    {"statusCode": 410, "body": {}},
    {"statusCode": 200, "body": {}},
])
def test_get_event_without_an_event_is_a_no(tmp_path, response):
    out = _run_node(tmp_path, "Return: get_event", response)
    assert out["ok"] is False and "event" not in out and out["message"]


@pytest.mark.parametrize("body", [
    {}, {"items": None}, {"items": {}}, "not json", {"kind": "calendar#event"},
    {"kind": "calendar#events", "items": None}, {"kind": "calendar#events", "items": {}},
])
def test_a_2xx_that_is_not_an_events_list_is_a_no_never_an_empty_list(tmp_path, body):
    """The availability read calls an empty list a free day (epic-work-site-safety R2)."""
    out = _run_list_node(tmp_path, {"statusCode": 200, "body": body})
    assert out["ok"] is False and "items" in out["message"]
    assert "events" not in out


def test_an_empty_items_array_is_still_a_complete_empty_list(tmp_path):
    out = _run_list_node(tmp_path, {"statusCode": 200, "body": {"items": []}})
    assert out == {"ok": True, "events": [], "truncated": False}


def test_an_events_list_kind_without_items_is_a_complete_empty_list(tmp_path):
    """Google may omit `items` on an empty day; its `kind` says the body IS the list."""
    out = _run_list_node(tmp_path, {"statusCode": 200, "body": {"kind": "calendar#events"}})
    assert out == {"ok": True, "events": [], "truncated": False}


def test_an_events_list_kind_with_another_page_is_still_truncated(tmp_path):
    out = _run_list_node(tmp_path, {"statusCode": 200, "body": {
        "kind": "calendar#events", "nextPageToken": "next"}})
    assert out == {"ok": True, "events": [], "truncated": True}


def test_no_shipped_workflow_carries_a_previous_name_or_an_instance_id():
    """The files are public; the canvas they came from was not."""
    for path in sorted(WF.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert "GrandVision" not in text and "X-Gv-Token" not in text, path.name
        for node in json.loads(text)["nodes"]:
            for cred in (node.get("credentials") or {}).values():
                assert cred.get("id") is None, f"{path.name}: {node['name']} binds a credential by id"


# --- DL-205: the façade answers a provider REFUSAL with its own status ----------


def test_facade_answers_through_respond_nodes_and_routes_a_provider_error():
    """n8n reports any thrown sub-workflow error as HTTP 500 "Error in
    workflow", which the app reads as "n8n down" (every 5xx is transient). A
    refusal the provider states in words — a list matching more than the page
    cap, an unparseable query — is the caller's to act on, so the façade now
    answers it with 422 through a Respond node, and everything else with 503."""
    wf = _load("cc-email-facade.json")
    assert _node(wf, "Webhook")["parameters"]["responseMode"] == "responseNode"
    call = _node(wf, "Call lib-email-provider")
    assert call.get("onError") == "continueErrorOutput"
    outs = wf["connections"]["Call lib-email-provider"]["main"]
    assert [c["node"] for c in outs[0]] == ["Respond (envelope)"]
    assert [c["node"] for c in outs[1]] == ["Classify provider error"]
    ok = _node(wf, "Respond (envelope)")["parameters"]
    assert ok["respondWith"] == "firstIncomingItem", "the envelope must stay the first item's JSON, as lastNode answered"
    assert [c["node"] for c in wf["connections"]["Classify provider error"]["main"][0]] == ["Respond (refusal or outage)"]
    err = _node(wf, "Respond (refusal or outage)")["parameters"]
    assert err["respondWith"] == "json"
    assert "$json.message" in err["responseBody"]
    assert err["options"]["responseCode"] == "={{ $json.status }}"


def _run_facade_classifier(tmp_path, error):
    node = shutil.which("node")
    if not node:
        pytest.skip("node needed")
    code = _node(_load("cc-email-facade.json"), "Classify provider error")["parameters"]["jsCode"]
    script = tmp_path / "classify.js"
    item = f"{{ json: {{ error: {json.dumps(error)} }} }}"
    script.write_text(
        f"const $input = {{ first: () => ({item}), all: () => [{item}] }};\n"
        f"const out = (function () {{\n{code}\n}})();\n"
        "process.stderr.write(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stderr)[0]["json"]


@pytest.mark.parametrize("message", [
    "lib-email-provider: list pagination cap hit (5 pages fetched, more remain) - refusing to silently truncate; narrow scope_query",
    "lib-email-provider: provider rejected the list query (400) - check scope_query syntax",
    "lib-email-provider: bad or missing mode 'frobnicate' (must be 'message', 'thread', 'list', or 'report_spam')",
    'lib-email-provider: mode "list" requires scope_query (non-empty Gmail search string)',
])
def test_a_refusal_the_provider_states_is_422(tmp_path, message):
    out = _run_facade_classifier(tmp_path, message)
    assert out["status"] == 422 and out["message"] == message


@pytest.mark.parametrize("message", [
    "lib-email-provider: provider auth failed (401) - check the shared email credential",
    "lib-email-provider: provider rate limit hit - retry later",
    "lib-email-provider: list request failed before a response (connection/transport): connect ECONNREFUSED",
    "lib-email-provider: provider list failed (502)",
    "lib-email-provider: unexpected list response shape (no statusCode) - refusing to treat as an empty page",
    "Something this facade has never seen",
])
def test_everything_else_stays_an_outage_503(tmp_path, message):
    out = _run_facade_classifier(tmp_path, message)
    assert out["status"] == 503 and out["message"] == message


def test_the_classifier_reads_an_error_object_too(tmp_path):
    out = _run_facade_classifier(tmp_path, {"message": "lib-email-provider: list pagination cap hit (5 pages fetched, more remain)", "name": "NodeOperationError"})
    assert out["status"] == 422 and out["message"].startswith("lib-email-provider: list pagination cap hit")
