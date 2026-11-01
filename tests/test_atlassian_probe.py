"""`scripts/atlassian_probe.py` — the two guarantees the installers now rely on.

The probe stopped being a hand-run diagnostic in v2.53.0: `deploy/single/setup.sh
check`'s `integrations` section and both profiles' `verify.sh` run it and REPRINT
its FAIL lines verbatim into their own reports. That makes two of its properties
load-bearing rather than polite:

* **it never prints a token or an email address** — `_scrub` is what makes
  reprinting safe, so it is tested rather than assumed;
* **`--quiet` prints FAIL lines and the counts line only** — that is the shape a
  report can reprint, and the exit code is unchanged by it.

Plus the fix that occasioned the release: a page whose storage body is
legitimately EMPTY is a PASS. The old check was truthiness, so the reference
deployment's own empty "Test page 1" made a healthy Confluence report a failure.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import confluence

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "scripts" / "atlassian_probe.py"


def _probe():
    """Load the script as a module. It is a script, not a package member, so it
    is loaded by path — the same way it is RUN (python scripts/...)."""
    spec = importlib.util.spec_from_file_location("atlassian_probe", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["atlassian_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_probe_scrubs_every_credential_and_every_email(monkeypatch):
    monkeypatch.setattr(settings, "jira_api_token", "s3cret-jira-token")
    monkeypatch.setattr(settings, "jira_email", "op@acme.example")
    monkeypatch.setattr(settings, "confluence_api_token", "s3cret-wiki-token")
    monkeypatch.setattr(settings, "confluence_email", "wiki@acme.example")
    probe = _probe()

    text = ('{"errorMessages":["s3cret-jira-token rejected for op@acme.example"],'
            '"reporter":"someone.else@corp.example","wiki":"s3cret-wiki-token"}')
    out = probe._scrub(text)
    for secret in ("s3cret-jira-token", "s3cret-wiki-token", "op@acme.example",
                   "someone.else@corp.example"):
        assert secret not in out, f"{secret!r} survived _scrub: {out}"
    assert "<redacted>" in out and "<redacted-email>" in out


def test_quiet_prints_failures_and_nothing_else(capsys):
    probe = _probe()
    probe.QUIET = True
    probe._emit("PASS jira GET /serverInfo — 200 ok", is_failure=False)
    probe.skip("confluence", "(all checks)", "not configured")
    probe.info("jira base=...")
    probe._emit("FAIL jira GET /myself — 401 Unauthorized", is_failure=True)
    out = capsys.readouterr().out
    assert out == "FAIL jira GET /myself — 401 Unauthorized\n", out

    # ...and the default mode prints all of it.
    probe.QUIET = False
    probe._emit("PASS jira GET /serverInfo — 200 ok", is_failure=False)
    assert "PASS jira" in capsys.readouterr().out


def test_an_empty_page_body_is_present_not_missing():
    """The v2.53.0 fix: `body.storage.value == ""` is a page with no content, not
    a response missing the storage representation. `is not None` is the question."""
    assert confluence._storage_value({"body": {"storage": {"value": ""}}}) == ""
    assert confluence._storage_value({"body": {}}) is None
    source = PROBE_PATH.read_text(encoding="utf-8")
    assert "storage is not None" in source, (
        "the body check must test PRESENCE — truthiness failed on a legitimately "
        "empty page (measured 2026-09-27)"
    )


@pytest.mark.parametrize("flag", ["--jira-only", "--confluence-only", "--quiet"])
def test_the_flags_are_accepted(flag):
    source = PROBE_PATH.read_text(encoding="utf-8")
    assert flag in source


# --- the Confluence version line (epic-cockpit-rerun-reversibility-macros story 7)

_REAL_CLIENT = httpx.AsyncClient


def _fake_confluence(monkeypatch, handler):
    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(settings, "confluence_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_email", "bot@example.com")
    monkeypatch.setattr(settings, "confluence_api_token", "tok")


def _wiki(version_body, status=200):
    def handler(req):
        path = req.url.path
        if path.endswith(("/settings/systemInfo", "/server-information")):
            return httpx.Response(status, json=version_body)
        if path.endswith(("/spaces", "/space")):
            return httpx.Response(200, json={"results": [{"id": "1"}]})
        if path.endswith("/search"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    return handler


async def test_the_probe_prints_the_cloud_version_line(monkeypatch, capsys):
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    _fake_confluence(monkeypatch, _wiki({"commitHash": "abc123"}))
    probe = _probe()
    probe.QUIET = False
    await probe.probe_confluence()
    out = capsys.readouterr().out
    assert "INFO confluence version=cloud (abc123)" in out
    assert "VERIFY-ON-SITE" not in out.split("INFO confluence version")[1].split("\n")[0]
    assert "instance_version" in out


async def test_the_probe_prints_the_server_version_line_marked_verify_on_site(monkeypatch, capsys):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    monkeypatch.setattr(settings, "confluence_auth_mode", "bearer")
    _fake_confluence(monkeypatch, _wiki({"version": "9.2.1", "buildNumber": "9021"}))
    probe = _probe()
    probe.QUIET = False
    await probe.probe_confluence()
    line = next(l for l in capsys.readouterr().out.splitlines()
                if l.startswith("INFO confluence version="))
    assert "version=9.2.1 (9021)" in line and "VERIFY-ON-SITE" in line


async def test_a_version_endpoint_that_refuses_is_a_line_not_a_failure(monkeypatch, capsys):
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    _fake_confluence(monkeypatch, _wiki({"message": "forbidden"}, status=403))
    probe = _probe()
    probe.QUIET = False
    probe._checks = probe._failures = 0
    await probe.probe_confluence()
    out = capsys.readouterr().out
    assert "INFO confluence version=unavailable" in out
    assert probe._failures == 0, "the version line never fails a probe run"
