#!/usr/bin/env python3
"""Render deploy/single/CHECKLIST.md from deploy/single/steps.tsv.

Design record `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`,
D8: the install procedure is defined ONCE, in the manifest, and the operator's
checklist is GENERATED from it, so a document can no longer drift from what
`./setup.sh` actually walks. The prose around the steps (what the host must
already have, what a stop means, what happens afterwards, how to update) lives
in ONE place, `scripts/checklist_template.md`; the steps are rendered from the
manifest's rows, in order, grouped by phase, each with its `doc` sentence.

`human` and `gate` rows are the operator's moves, so they are set in bold and
say WHERE the operator acts. WHERE is derived from the row's `reads` and `doc`
(`derived_where`), and a row whose place cannot be derived is named in
`WHERE_BY_STEP`. An unmapped row still renders, with its sentence, and
`tests/test_single_checklist.py` fails until it has a WHERE — so a new human
row cannot ship without one.

Standard library only: this runs on a developer's machine, never on an install.

    python scripts/render_checklist.py           # write deploy/single/CHECKLIST.md
    python scripts/render_checklist.py --check   # exit 1 with a diff when it is stale
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "deploy" / "single" / "steps.tsv"
QUESTIONS = ROOT / "deploy" / "single" / "questions.tsv"
TEMPLATE = ROOT / "scripts" / "checklist_template.md"
OUT = ROOT / "deploy" / "single" / "CHECKLIST.md"

COLUMNS = ("phase", "step", "kind", "requires", "reads", "writes", "probe", "doc")
OPERATOR_KINDS = ("human", "gate")
KIND_LABEL = {"gate": "gate — the run stops here", "human": "yours"}

# Where an operator row's place cannot be derived from its `reads` and `doc`.
# Keyed `<phase>/<step>`. Values may use the same {placeholders} as
# `derived_where` (see `_urls`).
WHERE_BY_STEP = {
    # Derivable (it reads CC_LITELLM_PORT and names the LiteLLM UI), but the
    # place needs one more fact the row cannot carry: `.env` declares ONE
    # upstream, so this gate is where every other endpoint goes (P5, 2026-10-02:
    # the acceptance driver had to work that out for an embedder on its own port).
    "llm/catalog-filled": (
        "the LiteLLM UI, {litellm_ui} — log in as `admin` with `.env`'s "
        "`CC_LLM_PROXY_ADMIN_KEY`. Every alias `.env` did not declare is entered "
        "here — including one served from a different host or port than "
        "`CC_LLM_UPSTREAM_BASE_URL` (typically the embedder), because `.env` "
        "declares one upstream base URL and key for all of them; a server that "
        "checks no key still needs a non-empty key field (`none`)"
    ),
    "boot/operator-name": (
        "the terminal running `./setup.sh`, which asks once; with no terminal, "
        "the cockpit's first-run prompt bar at {cockpit}"
    ),
}


# ── the manifest ─────────────────────────────────────────────────────────────

def load_rows(path: Path = STEPS) -> list[dict[str, str]]:
    """Every manifest row, in run order. `-` (an empty field) reads as ''."""
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != len(COLUMNS):
            raise SystemExit(
                f"{path}: expected {len(COLUMNS)} tab-separated fields, got "
                f"{len(fields)}: {line!r}"
            )
        rows.append({k: ("" if v == "-" else v) for k, v in zip(COLUMNS, fields)})
    if not rows:
        raise SystemExit(f"{path} declares no rows")
    return rows


def phases_in_order(rows: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    for r in rows:
        if r["phase"] not in seen:
            seen.append(r["phase"])
    return seen


def question_defaults(path: Path = QUESTIONS) -> dict[str, str]:
    """`questions.tsv`'s default per key — the port a URL is built from."""
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) >= 4:
            out[f[0]] = "" if f[3] == "-" else f[3]
    return out


# ── WHERE an operator acts ───────────────────────────────────────────────────

def _port_url(defaults: dict[str, str], key: str, path: str) -> str:
    port = defaults.get(key) or f"<{key}>"
    return f"http://127.0.0.1:{port}{path} (port {port} unless `.env` sets `{key}`)"


def _urls(defaults: dict[str, str]) -> dict[str, str]:
    return {
        "litellm_ui": _port_url(defaults, "CC_LITELLM_PORT", "/ui"),
        "cockpit": _port_url(defaults, "CC_COCKPIT_PORT", "/"),
        "n8n": _port_url(defaults, "CC_N8N_PORT", "/"),
    }


def derived_where(row: dict[str, str], defaults: dict[str, str]) -> str | None:
    """WHERE the operator acts on this row, from what the row itself says.

    Most specific first: a file the operator places, then the UI a port key
    (or the doc) names. None when nothing in the row says.
    """
    reads = set(filter(None, row["reads"].split(",")))
    doc = row["doc"].lower()
    urls = _urls(defaults)
    if "CC_CA_BUNDLE" in reads:
        return "on this host's disk — the PEM file whose path `.env`'s `CC_CA_BUNDLE` names"
    if "CC_N8N_PORT" in reads or ("CC_ENABLE_N8N" in reads and re.search(r"\bn8n\b", doc)):
        return f"the n8n UI, {urls['n8n']}"
    if "CC_LITELLM_PORT" in reads and "litellm ui" in doc:
        return f"the LiteLLM UI, {urls['litellm_ui']} — log in as `admin` with `.env`'s `CC_LLM_PROXY_ADMIN_KEY`"
    if "CC_COCKPIT_PORT" in reads:
        return f"the cockpit, {urls['cockpit']}"
    return None


def where_for(row: dict[str, str], defaults: dict[str, str]) -> str | None:
    """The WHERE line for an operator row: the explicit mapping wins, then the
    derivation. None means the row has no WHERE (the test fails on that)."""
    key = f"{row['phase']}/{row['step']}"
    if key in WHERE_BY_STEP:
        return WHERE_BY_STEP[key].format(**_urls(defaults))
    return derived_where(row, defaults)


# ── Markdown ─────────────────────────────────────────────────────────────────

_KEY = re.compile(r"\b(CC_[A-Z0-9_]+(?:=[0-9A-Za-z]+)?)")
_URL = re.compile(r"(https?://[^\s]*[^\s.,;:)])")


def md_inline(text: str) -> str:
    """A doc sentence as safe inline Markdown: `.env` keys and URLs become code
    spans, and outside code spans `<`, `>` and `*` are escaped so a placeholder
    like `<CC_LITELLM_PORT>` or a glob like `skills/*/` survives rendering."""
    parts = text.split("`")
    for i in range(0, len(parts), 2):          # even = outside an existing code span
        seg = parts[i]
        out, last = [], 0
        for m in re.finditer(f"{_URL.pattern}|{_KEY.pattern}", seg):
            out.append(_escape(seg[last:m.start()]))
            out.append(f"`{m.group(0)}`")
            last = m.end()
        out.append(_escape(seg[last:]))
        parts[i] = "".join(out)
    return "`".join(parts)


def _escape(s: str) -> str:
    return s.replace("*", r"\*").replace("<", "&lt;").replace(">", "&gt;")


def render_steps(rows: list[dict[str, str]], defaults: dict[str, str]) -> str:
    lines: list[str] = []
    n = 0
    for phase in phases_in_order(rows):
        lines.append(f"### `{phase}`")
        lines.append("")
        for r in (r for r in rows if r["phase"] == phase):
            n += 1
            lead = f"{n}. "
            name = f"`{r['phase']}/{r['step']}`"
            doc = md_inline(r["doc"])
            if r["kind"] in OPERATOR_KINDS:
                lines.append(f"{lead}**{name} ({KIND_LABEL[r['kind']]}): {doc}**")
                where = where_for(r, defaults)
                if where:
                    lines.append(f"{' ' * len(lead)}Where: {where}.")
            else:
                lines.append(f"{lead}{name}: {doc}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render() -> str:
    rows = load_rows()
    defaults = question_defaults()
    template = TEMPLATE.read_text(encoding="utf-8").replace("\r\n", "\n")
    values = {
        "{{steps}}": render_steps(rows, defaults).rstrip("\n"),
        "{{phase_count}}": str(len(phases_in_order(rows))),
        "{{row_count}}": str(len(rows)),
        **{f"{{{{{k}}}}}": v for k, v in _urls(defaults).items()},
    }
    out = template
    for token, value in values.items():
        out = out.replace(token, value)
    left = re.findall(r"\{\{\w+\}\}", out)
    if left:
        raise SystemExit(f"{TEMPLATE}: unknown placeholder(s) {sorted(set(left))}")
    return out.rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 with a diff when the committed CHECKLIST.md is stale")
    args = ap.parse_args(argv)
    fresh = render()
    if args.check:
        current = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        if current == fresh:
            print(f"{OUT.relative_to(ROOT)} is current")
            return 0
        sys.stdout.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), fresh.splitlines(keepends=True),
            fromfile=f"{OUT.relative_to(ROOT)} (committed)",
            tofile=f"{OUT.relative_to(ROOT)} (fresh render)"))
        print(f"\n{OUT.relative_to(ROOT)} is STALE — run: python scripts/render_checklist.py",
              file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(fresh)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
