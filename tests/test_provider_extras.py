"""The mail and calendar providers' packages are optional extras (DL-235).

`exchange` and `google` in pyproject.toml; installed where the root .env
selects the provider (`deploy/env-lib.sh:cc_python_extras`, the one reader
both profiles' installers call); imported lazily; and a selected provider
whose packages are missing stops the API at start with the extra named
(`integrations/provider_extras.py`). Every value here is synthetic.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from central_command.api import app as app_mod
from central_command.config import Settings, settings
from central_command.integrations import provider_extras

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_EXCHANGE = ("exchange_url", "exchange_username", "exchange_password")


def _name(requirement: str) -> str:
    return (
        requirement.split(";")[0]
        .split("[")[0]
        .split("<")[0]
        .split(">")[0]
        .split("=")[0]
        .split("!")[0]
        .split("~")[0]
        .strip()
        .lower()
    )


def test_the_table_names_exactly_what_each_extra_declares():
    extras = PYPROJECT["project"]["optional-dependencies"]
    for extra, dists in provider_extras.EXTRAS.items():
        declared = {_name(r) for r in extras[extra]}
        assert set(dists) <= declared, (extra, declared)
    core = {_name(r) for r in PYPROJECT["project"]["dependencies"]}
    moved = {d for dists in provider_extras.EXTRAS.values() for d in dists}
    assert not moved & core, "a provider package is still a core dependency"


def _select(monkeypatch, *, exchange: bool, google: bool) -> None:
    for field in _EXCHANGE:
        monkeypatch.setattr(settings, field, "x@example.com" if exchange else "")
    monkeypatch.setattr(settings, "google_provider", "native" if google else "n8n")


@pytest.mark.parametrize(
    ("exchange", "google", "expected"),
    [
        (False, False, []),
        (True, False, ["exchange"]),
        (False, True, ["google"]),
        (True, True, ["exchange", "google"]),
    ],
)
def test_the_selection_is_the_providers_own_switch(monkeypatch, exchange, google, expected):
    _select(monkeypatch, exchange=exchange, google=google)
    assert provider_extras.selected() == expected


def test_a_selected_provider_missing_its_package_stops_the_api_naming_the_extra(monkeypatch):
    _select(monkeypatch, exchange=False, google=True)
    real = metadata.version

    def version(dist):
        if dist == "google-auth-oauthlib":
            raise metadata.PackageNotFoundError(dist)
        return real(dist)

    monkeypatch.setattr(metadata, "version", version)
    with pytest.raises(RuntimeError) as refused:
        app_mod._refuse_missing_provider_extras()
    text = str(refused.value)
    assert text.startswith("refusing to start: the google provider is selected")
    assert "google-auth-oauthlib is not installed" in text and ".[dev,runtime,google]" in text
    # A provider that is not selected needs nothing, installed or not.
    _select(monkeypatch, exchange=False, google=False)
    app_mod._refuse_missing_provider_extras()


def test_the_installed_dev_set_passes():
    _ = [metadata.version(d) for dists in provider_extras.EXTRAS.values() for d in dists]
    assert provider_extras.missing() == []


def test_the_application_imports_without_the_provider_packages():
    """Lazy imports: importing the app, the façades and the provider modules
    loads none of the packages, so an install without the extra imports."""
    code = (
        "import sys\n"
        "import central_command.api.app, central_command.integrations.exchange, "
        "central_command.integrations.google, central_command.google_consent\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('exchangelib', 'googleapiclient', 'google_auth_oauthlib', 'google_auth_httplib2')]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


# --- the shell mirror ---------------------------------------------------------

ENV_CASES = {
    "nothing selected": "CC_LLM_BASE_URL=http://127.0.0.1:4000\n",
    "exchange": (
        "CC_EXCHANGE_URL=https://mail.example.com/ews/exchange.asmx\n"
        "CC_EXCHANGE_USERNAME='EXAMPLE\\jdoe'\nCC_EXCHANGE_PASSWORD=\"p w # x\"\n"
    ),
    "exchange missing a password": (
        "CC_EXCHANGE_URL=https://mail.example.com/ews\n"
        "CC_EXCHANGE_USERNAME=jdoe@example.com\nCC_EXCHANGE_PASSWORD=\n"
    ),
    "exchange password only a comment": (
        "CC_EXCHANGE_URL=https://mail.example.com/ews\n"
        "CC_EXCHANGE_USERNAME=jdoe@example.com\n"
        "CC_EXCHANGE_PASSWORD= # set me\n"
    ),
    "google": "CC_GOOGLE_PROVIDER=native\n",
    "google quoted, later line wins": 'CC_GOOGLE_PROVIDER=n8n\nCC_GOOGLE_PROVIDER="native"\n',
    "google back to n8n": "CC_GOOGLE_PROVIDER=native\nCC_GOOGLE_PROVIDER=n8n  # default\n",
    "both, CRLF": (
        "CC_GOOGLE_PROVIDER=native\r\nCC_EXCHANGE_URL=https://mail.example.com/ews\r\n"
        "CC_EXCHANGE_USERNAME=jdoe\r\nCC_EXCHANGE_PASSWORD=secret\r\n"
    ),
}


def _shell(env_file: Path) -> str:
    run = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1/deploy/env-lib.sh" && cc_python_extras "$2"',
            "_",
            str(ROOT),
            str(env_file),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


@pytest.mark.parametrize("case", sorted(ENV_CASES))
def test_the_installers_reader_agrees_with_the_api(tmp_path, monkeypatch, case):
    env_file = tmp_path / ".env"
    env_file.write_bytes(ENV_CASES[case].encode())
    for key in (
        "CC_EXCHANGE_URL",
        "CC_EXCHANGE_USERNAME",
        "CC_EXCHANGE_PASSWORD",
        "CC_GOOGLE_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)
    loaded = Settings(_env_file=env_file)
    for field in (*_EXCHANGE, "google_provider"):
        monkeypatch.setattr(settings, field, getattr(loaded, field))
    assert _shell(env_file) == ",".join(["dev", "runtime", *provider_extras.selected()])


def test_a_missing_env_file_installs_no_provider_extra(tmp_path):
    assert _shell(tmp_path / "absent.env") == "dev,runtime"


@pytest.mark.parametrize(
    "script",
    [
        "deploy/k3s/cc-update.sh",
        "deploy/k3s/setup.sh",
        "deploy/single/phases/app.sh",
        "deploy/single/setup.sh",
    ],
)
def test_every_installer_takes_its_extras_from_the_one_reader(script):
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "cc_python_extras" in text
    assert ".[dev,runtime]'" not in text and '.[dev,runtime]"' not in text, script
