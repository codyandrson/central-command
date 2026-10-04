#!/usr/bin/env python3
"""Apply the carried graphiti-core fixes to this interpreter's installed copy.

    python scripts/apply_graphiti_patches.py            # patch (idempotent)
    python scripts/apply_graphiti_patches.py --check    # report; exit 1 unless fully patched

Run it after every dependency install. The logic lives in
central_command/integrations/graphiti_patches.py (stdlib only, so it also runs
under an interpreter that has graphiti-core and nothing else); this is the CLI.
Design: docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md, D7.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Loaded by path: importing `central_command` would pull in the app's
# dependencies, and this must work in a bare environment.
_MOD = Path(__file__).resolve().parents[1] / "central_command" / "integrations" / "graphiti_patches.py"
_spec = importlib.util.spec_from_file_location("_cc_graphiti_patches", _MOD)
gp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gp  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(gp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    ap.add_argument("--site-packages", type=Path, help="directory holding graphiti_core (default: this interpreter's)")
    ap.add_argument("--force-version", action="store_true", help=f"allow a graphiti-core other than {gp.PINNED_VERSION}")
    args = ap.parse_args(argv)
    try:
        if args.check:
            site = args.site_packages or gp.find_site_packages()
            plan = gp.build_plan(Path(site))
            for _, rel, status in plan.lines:
                print(f"{rel}: {'already patched' if status == 'already patched' else 'NOT patched'}")
            return 0 if not plan.writes else 1
        for _, rel, status in gp.apply_patches(args.site_packages, force_version=args.force_version):
            print(f"{rel}: {status}")
        return 0
    except gp.PatchError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
