"""The run-mode guard: a deployment carries no run-mode key at all.

The deterministic test model and the dry-run executor are test-only. Any of
the three keys below PRESENT in an environment, whatever its value (`false`,
`live` and an empty value included), is a refusal: no value is judged and
there is no warn tier. The API applies the rule over its own process
environment first thing in its lifespan (`api/app.py`), the one check that
sees every source it reads (the root `.env` through `config.py`'s
`load_dotenv`, the unit's `Environment=` lines and every drop-in);
`deploy/k3s/verify.sh` runs this file over the running API's
`/proc/<pid>/environ`.

Stdlib only, and it imports nothing from `central_command`: shell scripts run
it by path as `python3 -I central_command/run_modes.py` (`-I` keeps the
script's directory, the package, off `sys.path`), and the package's
`__init__` reads VERSION.

    python3 -I central_command/run_modes.py [--env-file F]... [--environ F]...

prints one FAIL line per finding, or one PASS line when there is none, in the
deploy output protocol, and exits 1 on any finding, else 0.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass

KEYS = ("CC_TEST_MODEL", "CC_DEMO_MODE", "CC_EXECUTOR_MODE")

CHECK_NAME = "run-modes"


@dataclass(frozen=True)
class Finding:
    key: str
    value: str
    source: str = "the environment"

    def message(self) -> str:
        return (
            f"{self.key}={self.value!r} is set in {self.source}: remove {self.key} "
            "from .env, the unit or the cc-uvicorn.service.d/ drop-in that sets "
            "it — the deterministic model is test-only and the Executor always "
            "performs approved writes"
        )


def check(env: Mapping[str, str], source: str = "the environment") -> list[Finding]:
    """One finding for each run-mode key PRESENT in `env`, whatever its value."""
    return [Finding(key, env[key], source) for key in KEYS if key in env]


def parse_env_file(text: str) -> dict[str, str]:
    """The KEY=value lines of a dotenv file (`export ` and quotes tolerated).
    Commented lines are not settings."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def parse_environ(data: bytes) -> dict[str, str]:
    """A NUL-separated process environment, as `/proc/<pid>/environ` holds it."""
    out: dict[str, str] = {}
    for entry in data.split(b"\0"):
        key, sep, value = entry.decode("utf-8", "replace").partition("=")
        if sep:
            out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run_modes.py",
        description="Refuse any run-mode key (" + ", ".join(KEYS) + ").",
    )
    ap.add_argument("--env-file", action="append", default=[], metavar="F",
                    help="a dotenv file to check (repeatable)")
    ap.add_argument("--environ", action="append", default=[], metavar="F",
                    help="a NUL-separated process environ to check (repeatable)")
    args = ap.parse_args(argv)
    if not args.env_file and not args.environ:
        ap.error("give at least one --env-file or --environ")

    findings: list[Finding] = []
    sources: list[str] = []
    try:
        for path in args.env_file:
            with open(path, encoding="utf-8") as fh:
                findings += check(parse_env_file(fh.read()), path)
            sources.append(path)
        for path in args.environ:
            with open(path, "rb") as fh:
                findings += check(parse_environ(fh.read()), path)
            sources.append(path)
    except OSError as exc:
        print(f"FAIL {CHECK_NAME}: cannot read {exc.filename}: {exc.strerror}")
        return 1

    for f in findings:
        print(f"FAIL {CHECK_NAME}: {f.message()}")
    if findings:
        return 1
    print(f"PASS {CHECK_NAME}: none of {', '.join(KEYS)} in {', '.join(sources)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
