"""The k3s `app` phase imports the bundled skills (v2.64.0).

The single-node profile has imported `skills/<id>/` at boot since the
2026-10-01 design record (D7); the k3s driver never did, so a fresh spine there
had an empty library until the operator imported seven folders by hand.
`app_skills_import` and its two helpers are lifted out of
`deploy/k3s/setup.sh` and run against a small HTTP server that answers the two
routes the step uses: GET /api/skills and POST /api/skills/import.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.installer_source import functions_in

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "k3s" / "setup.sh"


class _Api:
    """A library holding `held`; an import of an id in `refuse` answers 422."""

    def __init__(self, held: list[str], refuse: tuple[str, ...] = ()):
        self.held, self.refuse, self.posts = held, refuse, []
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: dict):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(200, {"skills": [{"id": i} for i in api.held]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                api.posts.append(body)
                if body["skill_id"] in api.refuse:
                    return self._send(422, {"detail": "SKILL.md has no frontmatter"})
                self._send(200, {"skill_id": body["skill_id"], "references": []})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("curl"):
        pytest.skip("POSIX bash and curl needed")
    for name in ("alpha", "beta"):
        (tmp_path / "skills" / name).mkdir(parents=True)
        (tmp_path / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (tmp_path / "skills" / "not-a-skill").mkdir()      # no SKILL.md: not bundled
    return tmp_path


def _run(repo: Path, api_url: str) -> subprocess.CompletedProcess:
    fns = functions_in(SETUP.read_text(encoding="utf-8"))
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{repo.as_posix()}"',
        f'API_URL="{api_url}"',
        f'PY="{Path(sys.executable).as_posix()}"',
        'pass() { echo "PASS $1: $2"; }',
        'fail() { echo "FAIL $1: $2"; }',
        "note() { :; }",
        *(f"{name}() {{\n{fns[name]}\n}}" for name in
          ("bundled_skill_dirs", "skills_library_ids", "app_skills_import")),
        'app_skills_import; echo "RC=$?"',
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)


def test_an_empty_library_gains_every_bundled_folder(repo: Path):
    api = _Api(held=[])
    try:
        r = _run(repo, api.url)
    finally:
        api.close()
    assert "RC=0" in r.stdout, r.stdout + r.stderr
    assert "PASS skills-imported: imported 2 bundled skill(s): alpha beta" in r.stdout
    assert api.posts == [
        {"path": str(repo / "skills" / "alpha"), "skill_id": "alpha"},
        {"path": str(repo / "skills" / "beta"), "skill_id": "beta"},
    ]


def test_a_skill_the_library_holds_is_never_re_imported(repo: Path):
    api = _Api(held=["alpha", "an-operators-own"])
    try:
        r = _run(repo, api.url)
    finally:
        api.close()
    assert "RC=0" in r.stdout, r.stdout + r.stderr
    assert [p["skill_id"] for p in api.posts] == ["beta"]
    assert "1 already in the library and LEFT AS THEY ARE: alpha" in r.stdout


def test_a_refused_import_is_a_fail_naming_the_skill(repo: Path):
    api = _Api(held=[], refuse=("beta",))
    try:
        r = _run(repo, api.url)
    finally:
        api.close()
    assert "RC=1" in r.stdout, r.stdout + r.stderr
    line = next(l for l in r.stdout.splitlines() if l.startswith("FAIL skills-imported:"))
    assert "beta" in line and "/api/skills/import" in line and "no frontmatter" in line


def test_an_api_that_does_not_answer_is_a_fail_not_an_empty_library(repo: Path):
    api = _Api(held=[])
    url = api.url
    api.close()
    r = _run(repo, url)
    assert "RC=1" in r.stdout, r.stdout + r.stderr
    assert "FAIL skills-imported:" in r.stdout and "did not answer" in r.stdout


def test_the_app_phase_imports_after_the_api_is_started_and_answering():
    body = functions_in(SETUP.read_text(encoding="utf-8"))["phase_app"]
    started = body.index("systemctl enable --now cc-uvicorn")
    waited = body.index('wait_http "$API_URL/health"')
    imported = body.index("app_skills_import")
    assert started < waited < imported
