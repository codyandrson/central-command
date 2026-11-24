"""PARITY: the native Google client (DL-234) and the n8n façade, side by side.

This is the test that protects the cutover. Every scenario drives the SAME
façade call twice against the SAME scripted Google (`google_fakes.FakeGoogle`):

- once with `CC_GOOGLE_PROVIDER=n8n`, the webhook served at the HTTP layer by
  `N8nSim`, which runs the SHIPPED workflows (`deploy/n8n/workflows/*.json`):
  every Code node's own JavaScript under node, every request node's own URL /
  query / body expression evaluated under node, the Switch routes and the
  pagination rule of `List messages (Gmail API)` followed as the JSON declares
  them, and the façade's answer built the way each workflow answers (the email
  webhook through `Respond (envelope)` / `Classify provider error` → `Respond
  (refusal or outage)`, the calendar webhook as `lastNode`, every thrown error
  as n8n's HTTP 500 "Error in workflow");
- once with `CC_GOOGLE_PROVIDER=native`, through `integrations/google.py` and
  Google's client library, stubbed at its own HTTP layer (`google_fakes.FakeHttp`).

Then it asserts the caller saw the same thing: an equal return value, or an
error of the same type, status, classification and outage verdict (and, where
the n8n answer carries the provider's sentence, the same text). It also
asserts both paths sent Google the same requests (method, URL path, query,
body); the one difference is the `alt=json` the client library adds to every
request, which the comparison drops (`google_fakes.params`).

What the simulator does not model: n8n's token itself (it attaches and
refreshes its own; only its one re-send after a 401 is modelled, and the
native token handling is pinned in `test_google_native.py`), and the exact
text n8n puts in a transport error.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

# The `google` extra (DL-235): an install that did not select Google skips this file.
pytest.importorskip("googleapiclient")

from central_command.config import settings
from central_command.contract.failures import classify_failure, is_down
from central_command.integrations import calendar_facade, email_facade
from tests import google_fakes as gf
from tests.google_fakes import CAL, GMAIL, FakeGoogle

WF = Path(__file__).resolve().parents[1] / "deploy" / "n8n" / "workflows"
N8N = "http://n8n.parity.test"
EMAIL_URL = f"{N8N}/webhook/cc-email-facade"
CAL_URL = f"{N8N}/webhook/cc-calendar-facade"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node runs the shipped workflow code")


def _wf(name):
    wf = json.loads((WF / name).read_text(encoding="utf-8"))
    return {n["name"]: n for n in wf["nodes"]}, wf["connections"]


EMAIL_FACADE, _ = _wf("cc-email-facade.json")
EMAIL_LIB, EMAIL_CONN = _wf("lib-email-provider.json")
CAL_FACADE, _ = _wf("cc-calendar-facade.json")
CAL_LIB, CAL_CONN = _wf("lib-google-calendar.json")

_HARNESS = r"""
const fs = require('fs');
const ctx = JSON.parse(fs.readFileSync(0, 'utf8'));
const $ = (name) => { const v = ctx.nodes[name]; return { first: () => ({ json: v }), all: () => [{ json: v }] }; };
const $input = { first: () => ctx.input[0], all: () => ctx.input };
const $json = ctx.json;
const $response = ctx.response;
console.log = (...a) => process.stderr.write(a.join(' ') + '\n');
let res;
try { res = { ok: (function () { __CODE__ })() }; }
catch (e) { res = { thrown: String(e && e.message !== undefined ? e.message : e) }; }
process.stdout.write(JSON.stringify(res));
"""


class Thrown(Exception):
    pass


def _js(code: str, *, json_=None, nodes=None, input_=None, response=None):
    script = _HARNESS.replace("__CODE__", code)
    ctx = {"json": json_, "nodes": nodes or {}, "input": input_ or [], "response": response}
    done = subprocess.run([NODE, "-e", script], input=json.dumps(ctx), capture_output=True,
                          text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    if "thrown" in out:
        raise Thrown(out["thrown"])
    return out.get("ok")


def _expr(expression: str, **ctx) -> str:
    """An n8n parameter: a literal, or `=` and `{{ … }}` blocks of JavaScript.

    The blocks are evaluated with `eval` inside the throwaway node process, on
    purpose: they are the SHIPPED workflow's own expressions (repo files under
    `deploy/n8n/workflows/`, never input from outside), and running them is the
    only way to know the exact URL n8n builds. Test-only; nothing in the app
    evaluates code."""
    if not isinstance(expression, str) or not expression.startswith("="):
        return expression
    code = ("return " + json.dumps(expression[1:]) +
            ".replace(/\\{\\{([\\s\\S]*?)\\}\\}/g, (_, e) => { const v = eval(e); "
            "return typeof v === 'string' ? v : JSON.stringify(v); });")
    return _js(code, **ctx)


def _code(workflow, name, **ctx):
    return _js(workflow[name]["parameters"]["jsCode"], **ctx)


def _compact(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class N8nSim:
    """The two webhooks, run from the shipped workflow files."""

    def __init__(self, google: FakeGoogle):
        self.google = google

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        item = {"headers": {k.lower(): v for k, v in request.headers.items()},
                "body": json.loads(request.content), "params": {}, "query": {}}
        if url == EMAIL_URL:
            return self._email(item)
        if url == CAL_URL:
            return self._calendar(item)
        raise AssertionError(f"no webhook at {url}")

    # -- a request node ------------------------------------------------------

    def _http(self, node: dict, ctx: dict, page_token: str | None = None) -> dict:
        p = node["parameters"]
        kw = {"json_": ctx.get("json"), "nodes": ctx.get("nodes")}
        url = _expr(p["url"], **kw)
        params = {}
        if p.get("sendQuery"):
            for q in p["queryParameters"]["parameters"]:
                params[q["name"]] = _expr(q["value"], **kw)
        if page_token is not None:
            params["pageToken"] = page_token
        body = None
        if p.get("sendBody"):
            body = json.loads(_expr(p["jsonBody"], **kw))
        request = httpx.Request(p.get("method", "GET"), url, params=params or None,
                                content=_compact(body) if body is not None else None,
                                headers={"content-type": "application/json"} if body is not None else None)
        try:
            resp = self.google(request)
            if resp.status_code == 401:
                # n8n's OAuth2 credential (`requestOAuth2`) refreshes its token
                # on a 401 and re-sends once before the node sees the answer.
                resp = self.google(request)
        except httpx.HTTPError as e:
            if node.get("onError") == "continueRegularOutput":
                return {"json": {"error": {"message": str(e)}}}
            raise Thrown(str(e)) from None
        try:
            parsed = resp.json() if resp.content else {}
        except ValueError:
            parsed = resp.text
        return {"json": {"statusCode": resp.status_code, "body": parsed,
                         "headers": dict(resp.headers), "statusMessage": ""}}

    # -- cc-email-facade → lib-email-provider -----------------------------------

    def _email(self, item: dict) -> httpx.Response:
        try:
            inp = _code(EMAIL_FACADE, "Auth + unwrap", input_=[{"json": item}])[0]["json"]
        except Thrown:
            return httpx.Response(500, content=_compact({"message": "Error in workflow"}))
        try:
            envelope = self._email_lib(inp)
        except Thrown as e:
            out = _code(EMAIL_FACADE, "Classify provider error", input_=[{"json": {"error": str(e)}}])[0]["json"]
            return httpx.Response(out["status"], content=_compact({"message": out["message"]}),
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, content=_compact(envelope), headers={"content-type": "application/json"})

    def _email_lib(self, inp: dict) -> dict:
        nodes = {"When Called (source ref + mode)": inp}
        req = _code(EMAIL_LIB, "Validate request", input_=[{"json": inp}])[0]["json"]
        mode = req["mode"]
        if mode == "message":
            got = self._http(EMAIL_LIB["Get message (Gmail API)"], {"json": req})
            norm = _code(EMAIL_LIB, "Normalize response", nodes=nodes, input_=[got])[0]
            assert norm["json"]["__downloads"] == []  # `Downloads needed?` → no
            out = norm
        elif mode == "list":
            node = EMAIL_LIB["List messages (Gmail API)"]
            pag = node["parameters"]["options"]["pagination"]["pagination"]
            assert pag["maxRequests"] == 5 and pag["completeExpression"] == "={{ !$response.body?.nextPageToken }}"
            pages, token = [], None
            for _ in range(pag["maxRequests"]):
                page = self._http(node, {"json": req}, token)
                pages.append(page)
                if "error" in page["json"]:
                    break
                body = page["json"]["body"]
                token = body.get("nextPageToken") if isinstance(body, dict) else None
                if not token:
                    break
            out = _code(EMAIL_LIB, "Build list envelope", nodes=nodes, input_=pages)[0]
        elif mode == "report_spam":
            got = self._http(EMAIL_LIB["Report spam (Gmail API)"], {"json": req})
            out = _code(EMAIL_LIB, "Build spam envelope", nodes=nodes, input_=[got])[0]
        elif mode == "attachment":
            got = self._http(EMAIL_LIB["Get attachment (Gmail API)"], {"json": req})
            out = _code(EMAIL_LIB, "Build attachment envelope", nodes=nodes, input_=[got])[0]
        else:
            raise AssertionError(f"the simulator does not route {mode}")
        return _code(EMAIL_LIB, "Return envelope", input_=[out])[0]["json"]

    # -- cc-calendar-facade → lib-google-calendar -------------------------------

    def _calendar(self, item: dict) -> httpx.Response:
        try:
            inp = _code(CAL_FACADE, "Auth + unwrap", input_=[{"json": item}])[0]["json"]
            req = _code(CAL_LIB, "Validate request", input_=[{"json": inp}])[0]["json"]
            nodes = {"Validate request": req}
            mode = req["mode"]
            request_node, return_node = {
                "list": ("List events (GCal REST)", "Return: list"),
                "get_event": ("Get event (GCal REST)", "Return: get_event"),
                "create_event": ("Create event (GCal REST)", "Return: create_event"),
                "update_event": ("Update event (GCal REST)", "Return: update_event"),
                "delete_event": ("Delete event (GCal REST)", "Return: delete_event"),
            }[mode]
            got = self._http(CAL_LIB[request_node], {"json": req, "nodes": nodes})
            if mode == "create_event":
                rule = CAL_LIB["Route (create answer)"]["parameters"]["rules"]["values"][0]
                route = _expr(rule["conditions"]["conditions"][0]["leftValue"], json_=got["json"], nodes=nodes)
                if route == "existing":
                    got = self._http(CAL_LIB["Get existing event (GCal REST)"], {"json": got["json"], "nodes": nodes})
                    return_node = "Return: create_event (existing)"
            out = _code(CAL_LIB, return_node, nodes=nodes, input_=[got])[0]["json"]
        except Thrown:
            return httpx.Response(500, content=_compact({"message": "Error in workflow"}),
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, content=_compact(out), headers={"content-type": "application/json"})


# --- running one call both ways ---------------------------------------------


def _requests(fake: FakeGoogle) -> list[tuple]:
    """What Google was asked, without the token calls and the auth header."""
    out = []
    for r in fake.requests:
        if str(r.url) == gf.TOKEN:
            continue
        body = json.loads(r.content) if r.content else None
        out.append((r.method, gf.path(r), sorted(gf.params(r).items()), body))
    return out


async def _run(monkeypatch, native: bool, script, call):
    fake = script()
    if native:
        gf.select_native(monkeypatch)
        gf.install_native(monkeypatch, fake)
    else:
        monkeypatch.setattr(settings, "google_provider", "n8n")
        monkeypatch.setattr(settings, "email_facade_url", EMAIL_URL)
        monkeypatch.setattr(settings, "email_facade_token", "__CC_EMAIL_FACADE_TOKEN__")
        monkeypatch.setattr(settings, "calendar_facade_url", CAL_URL)
        monkeypatch.setattr(settings, "calendar_facade_token", "__CC_CALENDAR_FACADE_TOKEN__")
        gf.install(monkeypatch, N8nSim(fake))
    try:
        return ("ok", await call()), fake
    except (email_facade.EmailFacadeError, calendar_facade.CalendarFacadeError) as e:
        return ("err", e), fake
    finally:
        gf.reset_credentials()


async def _parity(monkeypatch, script, call, *, text: bool):
    (kind_n, n8n), fake_n = await _run(monkeypatch, False, script, call)
    monkeypatch.undo()
    (kind_g, nat), fake_g = await _run(monkeypatch, True, script, call)
    assert kind_n == kind_g, (n8n, nat)
    assert _requests(fake_n) == _requests(fake_g), "both paths must ask Google the same things"
    if kind_n == "ok":
        assert n8n == nat
        return n8n
    assert type(n8n) is type(nat)
    assert n8n.status_code == nat.status_code, (str(n8n), str(nat))
    assert classify_failure(n8n) == classify_failure(nat)
    assert is_down(n8n) == is_down(nat)
    if text:
        assert str(n8n) == str(nat)
    return n8n


# --- the scenarios ------------------------------------------------------------

def G(*routes):
    """A FakeGoogle factory: each route is (method, url, *answers)."""
    def make():
        fake = FakeGoogle()
        for method, url, *answers in routes:
            fake.on(method, url, *answers)
        return fake
    return make


CONNECT = httpx.ConnectError("connect ECONNREFUSED")
LIST = f"{GMAIL}/messages"
MSG = f"{GMAIL}/messages/18c0ffee00aa11bb"
ATT = f"{GMAIL}/messages/18c0ffee00aa11bb/attachments/ANGjdJ_pdf-1"
SPAM = f"{GMAIL}/messages/18c0ffee00aa11bb/modify"
EVENTS = f"{CAL}primary/events"
CID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
DAY = ("2025-11-20T00:00:00-07:00", "2025-11-21T00:00:00-07:00")
BLOB = bytes(range(256)) * 2 + b"\xfb\xff\xfe"

EMAIL_CASES = {
    # list_refs ------------------------------------------------------------
    "list one page": (G(("GET", LIST, (200, {"messages": [{"id": "a1", "threadId": "t1"},
                                                          {"id": "b2", "threadId": "b2"}]}))),
                      lambda: email_facade.list_refs("in:inbox newer_than:1d"), True),
    "list three pages": (G(("GET", LIST,
                            (200, {"messages": [{"id": "p1", "threadId": "t"}], "nextPageToken": "n2"}),
                            (200, {"messages": [{"id": "p2", "threadId": "t"}], "nextPageToken": "n3"}),
                            (200, {"messages": [{"id": "p3", "threadId": "t"}]}))),
                         lambda: email_facade.list_refs("label:receipts"), True),
    "list empty": (G(("GET", LIST, (200, {"resultSizeEstimate": 0}))),
                   lambda: email_facade.list_refs("in:inbox"), True),
    "list drops one bad id": (G(("GET", LIST, (200, {"messages": [{"id": "ok", "threadId": "t"},
                                                                  {"id": "x/y", "threadId": "t"}]}))),
                              lambda: email_facade.list_refs("q"), True),
    "list drops all": (G(("GET", LIST, (200, {"messages": [{"id": "x y", "threadId": "t"}]}))),
                       lambda: email_facade.list_refs("q"), True),
    "list page cap": (G(("GET", LIST, (200, {"messages": [{"id": "a", "threadId": "a"}], "nextPageToken": "m"}))),
                      lambda: email_facade.list_refs("in:anywhere"), True),
    "list empty query": (G(), lambda: email_facade.list_refs(" "), True),
    "list 400": (G(("GET", LIST, (400, gf.error_body(400, "Invalid query")))), lambda: email_facade.list_refs("q("), True),
    "list 401": (G(("GET", LIST, (401, gf.error_body(401)))), lambda: email_facade.list_refs("q"), True),
    "list 403": (G(("GET", LIST, (403, gf.error_body(403)))), lambda: email_facade.list_refs("q"), True),
    "list 404": (G(("GET", LIST, (404, gf.error_body(404)))), lambda: email_facade.list_refs("q"), True),
    "list 409": (G(("GET", LIST, (409, gf.error_body(409)))), lambda: email_facade.list_refs("q"), True),
    "list 429": (G(("GET", LIST, (429, gf.error_body(429)))), lambda: email_facade.list_refs("q"), True),
    "list 500": (G(("GET", LIST, (500, gf.error_body(500)))), lambda: email_facade.list_refs("q"), True),
    "list 503 on page 2": (G(("GET", LIST, (200, {"messages": [{"id": "a", "threadId": "a"}], "nextPageToken": "m"}),
                              (503, gf.error_body(503)))), lambda: email_facade.list_refs("q"), True),
    "list transport": (G(("GET", LIST, CONNECT)), lambda: email_facade.list_refs("q"), False),
    # get_message ------------------------------------------------------------
    "message multipart": (G(("GET", MSG, (200, gf.multipart_message()))),
                          lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message single html": (G(("GET", f"{GMAIL}/messages/18c0ffee00bb22cc", (200, gf.single_part_html()))),
                            lambda: email_facade.get_message("18c0ffee00bb22cc"), True),
    "message bad ref": (G(), lambda: email_facade.get_message("a/../b"), True),
    "message 400": (G(("GET", MSG, (400, gf.error_body(400)))), lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message 404": (G(("GET", MSG, (404, gf.error_body(404)))), lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message 403": (G(("GET", MSG, (403, gf.error_body(403)))), lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message 429": (G(("GET", MSG, (429, gf.error_body(429)))), lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message 500": (G(("GET", MSG, (500, gf.error_body(500)))), lambda: email_facade.get_message("18c0ffee00aa11bb"), True),
    "message transport": (G(("GET", MSG, CONNECT)), lambda: email_facade.get_message("18c0ffee00aa11bb"), False),
    # get_attachment ---------------------------------------------------------
    "attachment": (G(("GET", ATT, (200, {"attachmentId": "ANGjdJ_pdf-1", "size": len(BLOB), "data": gf.b64url(BLOB)}))),
                   lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment bad id": (G(), lambda: email_facade.get_attachment("18c0ffee00aa11bb", "a/b"), True),
    "attachment 400": (G(("GET", ATT, (400, gf.error_body(400)))),
                       lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment 404": (G(("GET", ATT, (404, gf.error_body(404)))),
                       lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment 403": (G(("GET", ATT, (403, gf.error_body(403)))),
                       lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment 503": (G(("GET", ATT, (503, gf.error_body(503)))),
                       lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment no data": (G(("GET", ATT, (200, {"size": 3}))),
                           lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), True),
    "attachment transport": (G(("GET", ATT, CONNECT)),
                             lambda: email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1"), False),
    # report_spam ------------------------------------------------------------
    "spam": (G(("POST", SPAM, (200, {"id": "18c0ffee00aa11bb", "threadId": "t", "labelIds": ["SPAM"]}))),
             lambda: email_facade.report_spam("18c0ffee00aa11bb"), True),
    "spam 404": (G(("POST", SPAM, (404, gf.error_body(404)))), lambda: email_facade.report_spam("18c0ffee00aa11bb"), True),
    "spam 403": (G(("POST", SPAM, (403, gf.error_body(403)))), lambda: email_facade.report_spam("18c0ffee00aa11bb"), True),
    "spam 429": (G(("POST", SPAM, (429, gf.error_body(429)))), lambda: email_facade.report_spam("18c0ffee00aa11bb"), True),
    "spam 500": (G(("POST", SPAM, (500, gf.error_body(500)))), lambda: email_facade.report_spam("18c0ffee00aa11bb"), True),
    "spam transport": (G(("POST", SPAM, CONNECT)), lambda: email_facade.report_spam("18c0ffee00aa11bb"), False),
}

_EVENTS = [
    gf.event("ev1", description="Agenda", attendees=[
        {"email": "operator@example.com", "self": True, "responseStatus": "accepted"},
        {"email": "jane.doe@example.com", "responseStatus": "needsAction"}]),
    gf.event("ev2", start={"date": "2025-11-20"}, end={"date": "2025-11-21"},
             organizer={"email": "john.rivers@example.com"}, location="Home"),
    gf.event("ev3", description="  ", organizer=None, creator={"email": "operator@example.com", "self": True},
             attendees=[{"email": "operator@example.com", "self": True, "responseStatus": "declined"}]),
    gf.event("ev4", status="cancelled", attachments=[{"fileUrl": "https://example.com/d"}]),
    {"kind": "calendar#event", "id": "ev5", "start": {"dateTime": "2025-11-20T15:00:00Z"}},
]
_EVENTS[2].pop("organizer")


def _create(**kw):
    return lambda: calendar_facade.create_event("Budget review", "2025-11-20T09:00:00-07:00",
                                                "2025-11-20T09:30:00-07:00", description="Q4",
                                                attendees=["jane.doe@example.com"], **kw)


CAL_CASES = {
    # list_events ------------------------------------------------------------
    "list events": (G(("GET", EVENTS, (200, gf.events_page(_EVENTS)))), lambda: calendar_facade.list_events(*DAY), True),
    "list other calendar": (G(("GET", f"{CAL}team%40example.com/events", (200, gf.events_page([])))),
                            lambda: calendar_facade.list_events(*DAY, "team@example.com"), True),
    "list empty kind": (G(("GET", EVENTS, (200, gf.events_page(None)))), lambda: calendar_facade.list_events(*DAY), True),
    "list truncated": (G(("GET", EVENTS, (200, gf.events_page(_EVENTS[:1], nextPageToken="n")))),
                       lambda: calendar_facade.list_events(*DAY), True),
    "list not a list": (G(("GET", EVENTS, (200, {"kind": "calendar#calendar"}))),
                        lambda: calendar_facade.list_events(*DAY), True),
    "list bad window": (G(), lambda: calendar_facade.list_events("2025-11-20", "2025-11-21"), False),
    "list reversed window": (G(), lambda: calendar_facade.list_events(DAY[1], DAY[0]), False),
    "list 401": (G(("GET", EVENTS, (401, gf.error_body(401))))  , lambda: calendar_facade.list_events(*DAY), False),
    "list 404": (G(("GET", EVENTS, (404, gf.error_body(404)))), lambda: calendar_facade.list_events(*DAY), False),
    "list 429": (G(("GET", EVENTS, (429, gf.error_body(429)))), lambda: calendar_facade.list_events(*DAY), False),
    "list 503": (G(("GET", EVENTS, (503, gf.error_body(503)))), lambda: calendar_facade.list_events(*DAY), False),
    "list transport": (G(("GET", EVENTS, CONNECT)), lambda: calendar_facade.list_events(*DAY), False),
    # get_event --------------------------------------------------------------
    "get": (G(("GET", f"{EVENTS}/ev1", (200, _EVENTS[0]))), lambda: calendar_facade.get_event("ev1"), True),
    "get 404": (G(("GET", f"{EVENTS}/ev1", (404, gf.error_body(404)))), lambda: calendar_facade.get_event("ev1"), True),
    "get 410": (G(("GET", f"{EVENTS}/ev1", (410, gf.error_body(410)))), lambda: calendar_facade.get_event("ev1"), True),
    "get no id": (G(("GET", f"{EVENTS}/ev1", (200, {"kind": "calendar#event"}))), lambda: calendar_facade.get_event("ev1"), True),
    "get 403": (G(("GET", f"{EVENTS}/ev1", (403, gf.error_body(403)))), lambda: calendar_facade.get_event("ev1"), False),
    "get bad id": (G(), lambda: calendar_facade.get_event("ev 1"), False),
    # create_event -----------------------------------------------------------
    "create": (G(("POST", EVENTS, (200, gf.event("new1", summary="Budget review")))), _create(), True),
    "create with client id": (G(("POST", EVENTS, (200, gf.event(CID, summary="Budget review")))),
                              _create(client_event_id=CID), True),
    "create 409 existing": (G(("POST", EVENTS, (409, gf.error_body(409))),
                              ("GET", f"{EVENTS}/{CID}", (200, gf.event(CID, summary="Budget review")))),
                            _create(client_event_id=CID), True),
    "create 409 cancelled": (G(("POST", EVENTS, (409, gf.error_body(409))),
                               ("GET", f"{EVENTS}/{CID}", (200, gf.event(CID, status="cancelled")))),
                             _create(client_event_id=CID), True),
    "create 409 unreadable": (G(("POST", EVENTS, (409, gf.error_body(409))),
                                ("GET", f"{EVENTS}/{CID}", (404, gf.error_body(404)))),
                              _create(client_event_id=CID), True),
    "create 409 readback down": (G(("POST", EVENTS, (409, gf.error_body(409))),
                                   ("GET", f"{EVENTS}/{CID}", (503, gf.error_body(503)))),
                                 _create(client_event_id=CID), False),
    "create 409 without id": (G(("POST", EVENTS, (409, gf.error_body(409)))), _create(), False),
    "create 404": (G(("POST", EVENTS, (404, gf.error_body(404)))), _create(), False),
    "create transport": (G(("POST", EVENTS, CONNECT)), _create(), False),
    "create bad attendee": (G(), lambda: calendar_facade.create_event(
        "x", "2025-11-20T09:00:00Z", "2025-11-20T10:00:00Z", attendees=["not-an-email"]), False),
    "create end before start": (G(), lambda: calendar_facade.create_event(
        "x", "2025-11-20T10:00:00Z", "2025-11-20T09:00:00Z"), False),
    # update_event -----------------------------------------------------------
    "update title": (G(("PATCH", f"{EVENTS}/ev1", (200, gf.event("ev1", summary="Moved")))),
                     lambda: calendar_facade.update_event("ev1", title="Moved"), True),
    "update times and people": (G(("PATCH", f"{EVENTS}/ev1", (200, gf.event("ev1")))),
                                lambda: calendar_facade.update_event(
                                    "ev1", start="2025-11-20T11:00:00Z", end="2025-11-20T11:30:00Z",
                                    attendees=["jane.doe@example.com"], description=""), True),
    "update nothing": (G(), lambda: calendar_facade.update_event("ev1"), False),
    "update 404": (G(("PATCH", f"{EVENTS}/ev1", (404, gf.error_body(404)))),
                   lambda: calendar_facade.update_event("ev1", title="x"), False),
    # delete_event -----------------------------------------------------------
    "delete": (G(("DELETE", f"{EVENTS}/ev1", (204, None))), lambda: calendar_facade.delete_event("ev1"), True),
    "delete 410": (G(("DELETE", f"{EVENTS}/ev1", (410, gf.error_body(410)))), lambda: calendar_facade.delete_event("ev1"), True),
    "delete 404": (G(("DELETE", f"{EVENTS}/ev1", (404, gf.error_body(404)))), lambda: calendar_facade.delete_event("ev1"), False),
    "delete 401": (G(("DELETE", f"{EVENTS}/ev1", (401, gf.error_body(401)))), lambda: calendar_facade.delete_event("ev1"), False),
    "delete transport": (G(("DELETE", f"{EVENTS}/ev1", CONNECT)), lambda: calendar_facade.delete_event("ev1"), False),
}


@pytest.mark.parametrize("name", list(EMAIL_CASES))
async def test_email_parity(monkeypatch, name):
    """`text=True`: the n8n answer carries the provider's sentence and the
    native path must produce the identical façade error text. Transport cases
    compare type, status and verdict only (n8n's own transport wording is not
    reproduced)."""
    script, call, text = EMAIL_CASES[name]
    await _parity(monkeypatch, script, call, text=text)


@pytest.mark.parametrize("name", list(CAL_CASES))
async def test_calendar_parity(monkeypatch, name):
    """`text=True` for answers and NOs (the envelope is the same, so is the
    refusal text). Failures compare type, status (500) and verdict: n8n's
    answer says only "Error in workflow", the native one says why."""
    script, call, text = CAL_CASES[name]
    await _parity(monkeypatch, script, call, text=text)


async def test_the_simulator_reads_the_shipped_nodes_it_claims_to():
    """Guards the simulator itself: the request nodes it evaluates carry the
    endpoints the native client calls."""
    assert EMAIL_LIB["Get message (Gmail API)"]["parameters"]["url"].endswith("?format=full")
    assert "sendUpdates=all" in CAL_LIB["Create event (GCal REST)"]["parameters"]["url"]
    out = _expr(CAL_LIB["Get event (GCal REST)"]["parameters"]["url"],
                json_={"calendar_id": "team@example.com", "event_id": "ev1"})
    assert out == f"{CAL}team%40example.com/events/ev1"
