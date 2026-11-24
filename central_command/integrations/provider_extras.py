"""The optional dependency extra each mail and calendar provider needs (DL-235).

The Exchange client (`integrations/exchange.py`) and the native Google client
(`integrations/google.py`, `google_consent.py`) are the only users of their
packages, and a deployment runs one provider or neither. So the packages are
two extras of `pyproject.toml`, `exchange` and `google`, installed only where
the provider is selected (the installers read the root `.env` through
`deploy/env-lib.sh:cc_python_extras`), and imported lazily inside the modules
that use them, so the application imports without them.

This module is the loud half: `missing()` names, for every provider THIS
deployment selected, the extra whose packages are not installed, and the API
refuses to start on it (`api/app.py`). The selection is the providers' own
switch, never a new setting: Exchange is `exchange.configured()` (url,
username and password set), Google is `google.selected()`
(`CC_GOOGLE_PROVIDER=native`). `EXTRAS` names distributions as
`pyproject.toml` declares them; `tests/test_provider_extras.py` pins the two
equal, and pins the shell mirror's answer to `selected()`.
"""

from __future__ import annotations

from importlib import metadata

# extra -> the distributions it declares in pyproject.toml. `requests`, which
# exchange.py also imports, is a core dependency (inscriptis needs it too).
EXTRAS: dict[str, tuple[str, ...]] = {
    "exchange": ("exchangelib",),
    "google": ("google-api-python-client", "google-auth", "google-auth-oauthlib",
               "httplib2", "google-auth-httplib2"),
}


def selected() -> list[str]:
    """The extras this deployment's configuration needs, in `EXTRAS` order."""
    from central_command.integrations import exchange, google

    wanted = {"exchange": exchange.configured(), "google": google.selected()}
    return [extra for extra in EXTRAS if wanted[extra]]


def _absent(extra: str) -> list[str]:
    out = []
    for dist in EXTRAS[extra]:
        try:
            metadata.version(dist)
        except metadata.PackageNotFoundError:
            out.append(dist)
    return out


def refusal(extra: str) -> str | None:
    """One sentence naming the extra to install when any of its packages is
    missing, else None."""
    absent = _absent(extra)
    if not absent:
        return None
    return (f"the {extra} provider is selected but {', '.join(absent)} "
            f"{'is' if len(absent) == 1 else 'are'} not installed — install the "
            f"`{extra}` extra: uv pip install -e \".[dev,runtime,{extra}]\" "
            f"(the installers add it when the root .env selects the provider)")


def missing() -> list[str]:
    """A refusal sentence per selected provider whose extra is not installed."""
    return [text for extra in selected() if (text := refusal(extra))]
