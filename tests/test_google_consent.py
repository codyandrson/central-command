"""The one-time consent command (`central_command/google_consent.py`, DL-234)
against a stub of Google's authorization and token endpoints served over real
loopback HTTP. The test plays the browser: it opens the printed URL, the stub
redirects to the command's loopback listener with a code, and the command
exchanges it at the stub token endpoint.

The stub enforces what Google's installed-app flow requires
(https://developers.google.com/identity/protocols/oauth2/native-app): the
loopback `redirect_uri` http://127.0.0.1:<port>, `response_type=code`, PKCE
with `code_challenge_method=S256` checked against the `code_verifier` at the
token request, `access_type=offline` for a refresh token, the client secret,
and the same `redirect_uri` on both legs.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from central_command import google_consent as consent
from central_command.integrations import google

CLIENT_ID = "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-fake-secret-for-the-test"
CODE = "4/0Afake-authorization-code-for-the-test"


class _Stub(http.server.HTTPServer):
    def __init__(self, refresh_token: str, *, deny: bool = False, scopes=google.SCOPES):
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.refresh_token = refresh_token
        self.deny = deny
        self.scopes = scopes
        self.challenge = None
        self.redirect_uri = None
        self.token_form = None
        self.problems: list[str] = []

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):  # the authorization endpoint
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        s = self.server
        want = {"client_id": CLIENT_ID, "response_type": "code", "access_type": "offline",
                "prompt": "consent", "code_challenge_method": "S256",
                "scope": " ".join(google.SCOPES)}
        for k, v in want.items():
            if q.get(k) != v:
                s.problems.append(f"auth {k}={q.get(k)!r}")
        if not (q.get("redirect_uri") or "").startswith("http://127.0.0.1:"):
            s.problems.append("redirect_uri is not the loopback address")
        s.challenge, s.redirect_uri = q.get("code_challenge"), q.get("redirect_uri")
        answer = ({"error": "access_denied", "state": q.get("state")} if s.deny else
                  {"code": CODE, "state": q.get("state"), "scope": " ".join(s.scopes)})
        self.send_response(302)
        self.send_header("location", f"{q['redirect_uri']}?{urlencode(answer)}")
        self.end_headers()

    def do_POST(self):  # the token endpoint
        s = self.server
        length = int(self.headers.get("content-length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        s.token_form = form
        verifier = form.get("code_verifier", "")
        derived = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        # Client authentication in the body or as HTTP Basic (RFC 6749 §2.3.1;
        # requests-oauthlib sends Basic, Google accepts both).
        basic = self.headers.get("authorization", "")
        if basic.startswith("Basic "):
            cid, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
            form.setdefault("client_id", cid)
            form.setdefault("client_secret", secret)
        ok = (form.get("grant_type") == "authorization_code" and form.get("code") == CODE
              and form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET
              and form.get("redirect_uri") == s.redirect_uri and derived == s.challenge
              and 43 <= len(verifier) <= 128)
        body = (
            {"access_token": "ya29.fake-access", "expires_in": 3599, "refresh_token": s.refresh_token,
             "scope": " ".join(s.scopes), "token_type": "Bearer"}
            if ok else {"error": "invalid_grant", "error_description": "Bad Request"})
        raw = json.dumps(body).encode()
        self.send_response(200 if ok else 400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


ENV = (
    "# Central Command\n"
    "CC_FOO=bar\n"
    "CC_FEED_QUERY=\"in:inbox newer_than:1d\"\n"
    "\n"
    "# google\n"
    f"CC_GOOGLE_CLIENT_ID={CLIENT_ID}\n"
    f"CC_GOOGLE_CLIENT_SECRET={CLIENT_SECRET}\n"
    "CC_LAST=1"  # no trailing newline on purpose
)


@pytest.fixture(autouse=True)
def _oauthlib_env(monkeypatch):
    """The stub token endpoint is plain http on loopback, which oauthlib
    refuses unless told (test only); the command's own scope relaxation is set
    here too so the test restores it."""
    monkeypatch.setenv("OAUTHLIB_INSECURE_TRANSPORT", "1")
    monkeypatch.setenv("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def _start(env: Path, auth_uri: str, token_uri: str):
    """Run the command in a thread; return (worker, out, err, rc, consent URL)."""
    out, err, rc = io.StringIO(), io.StringIO(), []
    worker = threading.Thread(target=lambda: rc.append(consent.run(
        env, 0, out=out, err=err, auth_uri=auth_uri, token_uri=token_uri)), daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        lines = [ln for ln in out.getvalue().splitlines() if ln.startswith(auth_uri + "?")]
        if lines:
            return worker, out, err, rc, lines[0]
        time.sleep(0.05)
    raise AssertionError(f"no consent URL printed: {err.getvalue()}")


def _consent(env: Path, refresh_token: str, **stub_kw):
    """Run the command against a fresh stub and drive the browser leg.
    Returns (exit code, stdout, stderr, stub, the page the browser got)."""
    stub = _Stub(refresh_token, **stub_kw)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    worker, out, err, rc, url = _start(env, f"{stub.base}/auth", f"{stub.base}/token")
    page = httpx.get(url, follow_redirects=True, timeout=10)  # the browser
    worker.join(15)
    stub.shutdown()
    stub.server_close()
    assert not worker.is_alive(), "the command did not finish after the redirect"
    assert stub.problems == [], stub.problems
    return rc[0], out.getvalue(), err.getvalue(), stub, page


def _secrets_absent(*texts, extra=()):
    for text in texts:
        for secret in (CLIENT_SECRET, CODE, "ya29.fake-access", *extra):
            assert secret not in text, f"{secret[:8]}… reached the output"


def test_first_run_writes_the_token_keeps_every_other_line_and_the_mode(tmp_path, capfd):
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    os.chmod(env, 0o640)
    rt = "1//0gFAKE-refresh-token-one_xyz"
    rc, out, err, stub, page = _consent(env, rt)
    assert rc == 0, err
    assert page.status_code == 200 and "Consent received" in page.text
    assert env.read_text(encoding="utf-8") == ENV + f"\nCC_GOOGLE_REFRESH_TOKEN={rt}\n"
    assert (env.stat().st_mode & 0o777) == 0o640
    assert "Wrote CC_GOOGLE_REFRESH_TOKEN" in out
    fd = capfd.readouterr()
    _secrets_absent(out, err, fd.out, fd.err, extra=(rt,))
    assert stub.token_form["client_secret"] == CLIENT_SECRET, "the secret went to the token endpoint only"


def test_a_rerun_replaces_the_one_key_and_nothing_else(tmp_path, capfd):
    env = tmp_path / ".env"
    env.write_text(ENV + "\nCC_GOOGLE_REFRESH_TOKEN=1//old-token\nCC_AFTER=2\n", encoding="utf-8")
    os.chmod(env, 0o600)
    rt = "1//0gFAKE-refresh-token-two_abc"
    rc, out, err, _, _ = _consent(env, rt)
    assert rc == 0, err
    text = env.read_text(encoding="utf-8")
    assert text == ENV + f"\nCC_GOOGLE_REFRESH_TOKEN={rt}\nCC_AFTER=2\n"
    assert text.count("CC_GOOGLE_REFRESH_TOKEN=") == 1
    assert (env.stat().st_mode & 0o777) == 0o600
    assert "Replaced CC_GOOGLE_REFRESH_TOKEN" in out
    fd = capfd.readouterr()
    _secrets_absent(out, err, fd.out, fd.err, extra=(rt, "1//old-token"))


def test_a_denied_consent_writes_nothing(tmp_path, capfd):
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    rc, out, err, _, _ = _consent(env, "1//unused", deny=True)
    assert rc == 1 and "AccessDeniedError" in err and "nothing was written" in err
    assert env.read_text(encoding="utf-8") == ENV
    fd = capfd.readouterr()
    _secrets_absent(out, err, fd.out, fd.err)


def test_a_consent_missing_a_scope_writes_nothing(tmp_path, capfd):
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    rt = "1//0gFAKE-partial"
    rc, out, err, _, _ = _consent(env, rt, scopes=google.SCOPES[:1])
    assert rc == 1 and "calendar.events" in err
    assert env.read_text(encoding="utf-8") == ENV
    fd = capfd.readouterr()
    _secrets_absent(out, err, fd.out, fd.err, extra=(rt,))


def test_a_redirect_with_another_state_ends_the_run_and_writes_nothing(tmp_path, capfd):
    """The library's listener answers one redirect; oauthlib refuses a state
    that is not this run's (MismatchingStateError)."""
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    worker, out, err, rc, url = _start(env, "http://127.0.0.1:9/auth", "http://127.0.0.1:9/token")
    redirect = parse_qs(urlparse(url).query)["redirect_uri"][0]
    assert redirect.startswith("http://127.0.0.1:") and redirect.endswith("/")
    httpx.get(f"{redirect}?code={CODE}&state=not-this-run", timeout=10)
    worker.join(15)
    assert rc == [1] and "MismatchingStateError" in err.getvalue()
    assert env.read_text(encoding="utf-8") == ENV
    fd = capfd.readouterr()
    _secrets_absent(out.getvalue(), err.getvalue(), fd.out, fd.err)


@pytest.mark.parametrize("drop", ["CC_GOOGLE_CLIENT_ID", "CC_GOOGLE_CLIENT_SECRET"])
def test_without_the_client_settings_it_refuses_before_listening(tmp_path, drop):
    env = tmp_path / ".env"
    env.write_text("\n".join(ln for ln in ENV.splitlines() if not ln.startswith(drop)), encoding="utf-8")
    err = io.StringIO()
    assert consent.run(env, 0, out=io.StringIO(), err=err) == 2
    assert drop in err.getvalue() and CLIENT_SECRET not in err.getvalue()


def test_help_states_the_headless_steps(capsys):
    with pytest.raises(SystemExit):
        consent.main(["--help"])
    text = capsys.readouterr().out
    assert "ssh -L 8765:127.0.0.1:8765" in text
    assert "Desktop app" in text and "Personal" in text and "7 days" in text
    assert "CC_GOOGLE_PROVIDER=native" in text
    for scope in google.SCOPES:
        assert scope in text


def test_the_flow_is_the_librarys_loopback_flow_never_out_of_band():
    src = Path(consent.__file__).read_text(encoding="utf-8")
    assert "urn:ietf:wg:oauth:2.0:oob" not in src
    assert "InstalledAppFlow" in src and "run_local_server(" in src
    assert 'host="127.0.0.1", bind_addr="127.0.0.1"' in src and "open_browser=False" in src
