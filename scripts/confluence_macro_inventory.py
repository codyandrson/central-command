"""Inventory the configured Confluence instance's macros (epic-cockpit-rerun-
reversibility-macros story 6, SKILLS-071, assumption A9).

    python scripts/confluence_macro_inventory.py [--dry-run] [--out PATH]
        [--sampler-title TITLE] [--macro NAME ...]

An OPERATOR script, run on the API host with the checkout's `.env`, in the
pattern of `scripts/atlassian_probe.py`: it imports the native client
(`integrations/confluence.py`) directly, and it is not an agent tool (agents
already hold `confluence_search` and `confluence_get_page`). It automates the
procedure in `skills/confluence/references/macro-discovery.md`:

1. read the instance version (Cloud `/wiki/rest/api/settings/systemInfo`;
   Server/DC `/rest/api/server-information`, VERIFY-ON-SITE);
2. find the sampler page by title (CQL) and read its storage body;
3. extract every `ac:structured-macro` block from it, whole;
4. for each candidate name (the sampler's blocks, the macro names the skill's
   intent-to-macro table gives in backticks, and `--macro` names) count the
   pages using it (CQL `macro = "<name>"`) and take one example block from the
   first hit's body;
5. render the inventory in the shape the Confluence skill's profile field
   expects, stamped `harvested` and `instance_version`, and write it to `--out`
   (default: the active profile's overlay field file under
   `settings.resolved_skill_overlay_dir()`) or print it with `--dry-run`.

The inventory is an INSTANCE FACT: it is never written inside the checkout (a
path there is refused before any request), and nothing from a real inventory
belongs in a tracked file. Re-import the Confluence skill afterwards so agents
read it (an overlay edit reaches the library only through a re-import).

Exit 0: written or printed. 1: no sampler page of that title. 2: Confluence is
not configured, the connection failed, the credentials were refused, or the
arguments were refused. Progress goes to stderr; stdout carries only the
inventory under `--dry-run`.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys
from datetime import datetime, timezone

# Run from a checkout without installing it, and prefer THIS checkout over an
# editable install that points somewhere else (worktrees).
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from central_command.config import settings
from central_command.integrations import confluence

DEFAULT_SAMPLER_TITLE = "Central Command Macro Sampler"
USAGE_LIMIT = 500          # five pages of 100 — the client's bounded crawl
SKILL_TABLE = ROOT / "skills" / "confluence" / "references" / "macro-discovery.md"
_TABLE_ROW = re.compile(r"^\|\s*(?P<intent>[^|]*?)\s*\|\s*(?P<macro>[^|]*?)\s*\|")
_BACKTICKED = re.compile(r"`([A-Za-z0-9][A-Za-z0-9_.-]*)`")


class Refused(Exception):
    """The run cannot continue; `code` is the exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def skill_table_macros(path: pathlib.Path = SKILL_TABLE) -> list[str]:
    """The `ac:name`s the skill's intent-to-macro table gives in backticks (its
    Macro column). A display name without backticks ("Table Filter") is not an
    `ac:name`: the sampler page is where those are found. Empty if the file is
    absent."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("| Intent"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        m = _TABLE_ROW.match(line)
        if m and not set(m.group("macro")) <= {"-", " "}:
            for n in _BACKTICKED.findall(m.group("macro")):
                if n not in names:
                    names.append(n)
    return names


def cql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile_field() -> str:
    """The overlay field the active flavor's profile lives in."""
    return ("confluence_profile_server" if settings.confluence_api_flavor == "server"
            else "confluence_profile_cloud")


def checkout_root() -> pathlib.Path:
    return pathlib.Path(settings.repo_root).expanduser() if settings.repo_root else ROOT


def resolve_out(out: str | None) -> pathlib.Path:
    """`--out`, or the active profile's overlay file. A path inside the
    checkout is refused: the inventory is an instance fact."""
    path = (pathlib.Path(out).expanduser() if out
            else pathlib.Path(settings.resolved_skill_overlay_dir()) / f"{profile_field()}.md")
    resolved = path.resolve()
    for root in {ROOT, checkout_root().resolve()}:
        if resolved == root or root in resolved.parents:
            raise Refused(
                f"{path} is inside the checkout ({root}) — the inventory is an instance "
                "fact; name a path outside it or leave --out to default to the overlay "
                "directory", 2)
    return path


def _auth_failed(e: confluence.ConfluenceError) -> bool:
    return getattr(e, "status_code", None) in (401, 403)


async def gather(client, *, sampler_title: str, extra: list[str], table: list[str]) -> dict:
    """Everything the render needs, from the client alone (a fake in the tests)."""
    try:
        info = await client.instance_info()
    except confluence.ConfluenceError as e:
        if _auth_failed(e) or "not configured" in str(e):
            raise Refused(str(e), 2) from e
        info = {"version": None, "detail": None, "flavor": settings.confluence_api_flavor,
                "verify_on_site": settings.confluence_api_flavor == "server",
                "error": str(e)}

    found = await client.search(f"type = page AND title = {cql_quote(sampler_title)}", 5)
    rows = found.get("results") or []
    if not rows:
        raise Refused(f"no sampler page titled {sampler_title!r} — build it through the "
                      "macro browser first (docs/CONFLUENCE_MACROS.md)", 1)
    sampler = rows[0]
    page = (await client.get_page(str(sampler["id"])))["page"]
    sampler_blocks = confluence.extract_macros(page.get("body"))

    candidates: list[str] = []
    for n in [b.name for b in sampler_blocks] + table + extra:
        if n not in candidates:
            candidates.append(n)

    macros = []
    for name in candidates:
        own = next((b for b in sampler_blocks if b.name == name), None)
        count, truncated, example, example_from, error = 0, False, None, None, None
        try:
            hits = await client.search(f"macro = {cql_quote(name)}", USAGE_LIMIT)
            count, truncated = hits.get("count", 0), bool(hits.get("truncated"))
            first = (hits.get("results") or [None])[0]
            if first and first.get("id"):
                body = (await client.get_page(str(first["id"])))["page"].get("body")
                example = next((b for b in confluence.extract_macros(body) if b.name == name), None)
                if example is not None:
                    example_from = f"page {first['id']}"
        except confluence.ConfluenceError as e:
            if _auth_failed(e):
                raise Refused(str(e), 2) from e
            error = str(e)
        if example is None and own is not None:
            example, example_from = own, "sampler"
        macros.append({"name": name, "count": count, "truncated": truncated, "error": error,
                       "schema_version": (own or example).schema_version if (own or example) else None,
                       "in_sampler": own is not None, "example": example,
                       "example_from": example_from})
    return {"info": info, "sampler": sampler, "sampler_count": len(rows),
            "sampler_blocks": len(sampler_blocks), "macros": macros}


def render(data: dict, *, harvested: str, flavor: str) -> str:
    info, sampler = data["info"], data["sampler"]
    version = info.get("version") or "unknown"
    if info.get("detail"):
        version += f" ({info['detail']})"
    if info.get("verify_on_site"):
        version += " — VERIFY-ON-SITE: Server/DC /rest/api/server-information, not proven against a live instance"
    if info.get("error"):
        version += f" — version read failed: {info['error']}"
    out = [
        "# Confluence macro inventory",
        "",
        f"harvested: {harvested}",
        f"instance_version: {version}",
        f"flavor: {flavor}",
        f"sampler_page: {sampler.get('title')} (id {sampler.get('id')})",
        "",
        "Harvested by scripts/confluence_macro_inventory.py. The blocks below are "
        "instance-verified: copy a block whole and adapt values only, never a "
        "parameter name. A macro absent from this list is a capability gap.",
    ]
    if data["sampler_count"] > 1:
        out += ["", f"note: {data['sampler_count']} pages matched the sampler title; the first was read."]
    for m in data["macros"]:
        usage = f"{m['count']}{'+' if m['truncated'] else ''}"
        out += ["", f"## {m['name']}", "",
                f"- ac:name: {m['name']}",
                f"- schema version: {m['schema_version'] or 'not stated'}",
                f"- pages using it: {usage}"
                + (f" (usage count failed: {m['error']})" if m["error"] else ""),
                f"- on the sampler page: {'yes' if m['in_sampler'] else 'no'}"]
        if m["example"] is not None:
            ex = m["example"]
            out += [f"- example, from {m['example_from']}:", "", "```xml", ex.text, "```"]
        else:
            out += ["- example: none found (no page uses it, and it is not on the sampler page)"]
    return "\n".join(out) + "\n"


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


async def run(args, client=confluence) -> int:
    out_path = resolve_out(args.out)
    if not client.configured():
        raise Refused("Confluence is not configured (CC_CONFLUENCE_BASE_URL + credentials "
                      "matching CC_CONFLUENCE_AUTH_MODE)", 2)
    flavor = settings.confluence_api_flavor
    log(f"confluence base={settings.confluence_base_url} flavor={flavor}")
    table = skill_table_macros()
    if not table:
        log(f"note: no macro names read from {SKILL_TABLE} — candidates are the sampler's and --macro")
    data = await gather(client, sampler_title=args.sampler_title,
                        extra=list(args.macro or []), table=table)
    text = render(data, harvested=datetime.now(timezone.utc).date().isoformat(), flavor=flavor)
    n = len(data["macros"])
    if args.dry_run:
        print(text, end="")
        log(f"dry run: {n} macro(s), nothing written")
    else:
        write(out_path, text)
        log(f"wrote {n} macro(s) to {out_path}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Inventory the Confluence instance's macros from the sampler page and CQL usage.")
    p.add_argument("--out", help="file to write (default: the active profile's overlay file); "
                                 "never inside the checkout")
    p.add_argument("--dry-run", action="store_true", help="print the inventory, write nothing")
    p.add_argument("--sampler-title", default=DEFAULT_SAMPLER_TITLE)
    p.add_argument("--macro", action="append", help="an extra macro ac:name to inventory (repeatable)")
    return p


async def main(argv: list[str] | None = None, client=confluence) -> int:
    args = parser().parse_args(argv)
    try:
        return await run(args, client)
    except Refused as e:
        log(f"error: {e}")
        return e.code
    except confluence.ConfluenceError as e:
        log(f"error: {e}")
        return 2
    except httpx.HTTPError as e:
        log(f"error: could not reach Confluence: {e.__class__.__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
