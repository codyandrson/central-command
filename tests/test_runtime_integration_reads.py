"""The runtime tier borrows the credentialed clients — and may only READ with them.

`runtime/__init__.py` states the shape: agents "propose and read" and reach
"read-only integrations, but never `gateway` or `executor`". That second half is
guarded (`test_governance.py::test_runtime_never_imports_the_gateway_tier`). The
first half was not, and it is the weaker half of the pair: every client under
`central_command/integrations/` holds real credentials and exposes its reads and
its WRITES side by side in the same module. `jira.get_issue` and
`jira.transition_issue` are one import apart; so are `confluence.get_page` and
`confluence.trash_page`, `graphiti.search_facts` and `graphiti.add_episode`,
`email_facade.get_message` and `email_facade.report_spam`, `litellm.list_models`
and `litellm.delete_model`.

Nothing mechanical stopped a `propose_*` tool from being "simplified" into the
write it was drafting. The import ban would not notice — the module is already
imported for its reads — and neither would the approval gate, because the write
would never reach it. The proposal would simply be a second copy of something
the agent had already done, and the operator's click would approve a fact.

So this walks every module under `runtime/`, resolves every name bound to a
`central_command.integrations` submodule (plain, aliased, and function-scoped
imports alike), and compares every attribute reached through those names with a
frozen per-module allowlist. Each entry below was confirmed a read by opening
the client function; a NEW attribute fails here, in the commit that adds it,
with the only two legitimate answers spelled out in the message.

Two deliberate exemptions, both recorded with their reason next to the code.

Known limit, stated rather than papered over: the walk resolves ATTRIBUTE
access on a bound module name. A dynamic reach (`getattr(jira, name)`,
`importlib.import_module(...)`) is invisible to it — which is why
`test_runtime_never_binds_the_integrations_package` exists, closing the one
spelling that would let a whole package in behind a single name.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"
RUNTIME = SRC / "runtime"
PKG = "central_command.integrations"

# Every attribute `runtime/` may reach on a credentialed client. Derived from
# today's call sites and confirmed one function at a time in
# `central_command/integrations/`: each is a GET (or a POST that only asks a
# question — a search, a probe, a catalog fetch), and none of them changes a
# record in Jira, Confluence, a forge, the graph, the mailbox or the proxy.
#
# Adding to this list is a claim you have opened the function and it writes
# nothing. It is not a place to put "the tool needs it".
READ_ALLOWLIST: dict[str, set[str]] = {
    # All GET /rest/api/3/... reads; `search_issues` POSTs to /search/jql,
    # which is Jira's own spelling of a query.
    "jira": {
        "get_issue", "get_transitions", "list_dashboards", "list_fields",
        "list_filters", "list_gadgets", "list_projects", "search_issues",
    },
    # Reads only; create_page/update_page/move_page/trash_page/set_labels/
    # remove_labels/upload_attachment/create_space are the writes and are absent.
    "confluence": {
        "get_page", "get_page_versions", "list_attachments", "list_children",
        "list_labels", "list_spaces", "search",
    },
    # forge.py defines no write at all today; the allowlist still names the
    # reads, so the first write added there has to be argued for here.
    "forge": {
        "get_issue", "get_merge_request", "list_commits", "list_instances",
        "list_issues", "list_merge_requests", "read_file", "search_repos",
    },
    # `add_episode` is the graph WRITE and is deliberately absent: the runtime
    # reaches it only as a `graph.add_episode` proposal the Executor performs.
    "graphiti": {
        "get_group_episodes", "known_groups", "search_facts", "search_nodes",
        "steward_map",
    },
    # `report_spam` is the mailbox write and is absent — and so are `send` and
    # `move` (Exchange native client design, 2026-09-25): the runtime reaches
    # all three only as proposals the Executor performs. `list_folders` IS a
    # read — it lists names and counts and changes nothing — and `mail_list_
    # folders` is the tool that answers "which folders exist", the AGENTS.md
    # "never guess X" rule. EmailFacadeError is the exception type the tools
    # catch.
    "email_facade": {"EmailFacadeError", "get_message", "list_refs",
                     "list_folders"},
    # The native Exchange client. The runtime reaches EXACTLY one name on it:
    # `configured()`, a settings predicate that opens no connection — it is how
    # `packs._offered` withholds the pack members the configured mailbox cannot
    # answer. Every actual mailbox call in runtime/ goes through the façade
    # above, so nothing else here may ever be added.
    "exchange": {"configured"},
    # Reads of the proxy's own configuration plus two POSTs that ask rather
    # than change: `probe_model` sends a chat/embedding/rerank request, and
    # `provider_catalog` fetches the vendor's model list. Everything under
    # /model/new, /model/*/update, /key/*, /team/*, /fallback and /v1/mcp/server
    # is a write and is absent. `_call` is the raw transport and carries its
    # own guard below.
    "litellm": {
        "LiteLLMError", "_call", "check_model_health", "configured",
        "get_health", "get_routing", "get_spend", "list_credentials",
        "list_keys", "list_mcp_servers", "list_models", "list_teams",
        "probe_model", "provider_catalog", "thinking_mechanism",
    },
    # Decrypts what LiteLLM already stored; writes nothing.
    "litellm_credstore": {"list_provider_credentials"},
    # One function, an HTTP GET of a public URL.
    "webfetch": {"fetch"},
}

# EXEMPT, by the rule in AGENTS.md: "The agent sandbox is CONTAINMENT, not
# restriction... What constrains a sandbox is that it holds NO credentials."
# Writing a file into a scratch container is not a world-change, so
# `sandbox_client`'s write surface is not gated here. The sandbox's one exit is
# `mcp.sync_source`, which IS a proposal — guarded by
# tests/test_mcp_sync.py::test_servers_root_is_the_same_path_on_both_sides.
EXEMPT_MODULES = {
    "sandbox_client": (
        "the sandbox holds no credentials — AGENTS.md, 'the agent sandbox is "
        "CONTAINMENT, not restriction'; its only exit is the gated "
        "mcp.sync_source proposal"
    ),
}

# Never importable from `runtime/`, at all. `neo4j_writer`'s own module
# docstring says so: "nothing in `runtime/` may import this module, exactly as
# with `gateway/`" — it is the OPERATOR's hand on the graph, with no approval
# gate because the operator is the gate.
BANNED_MODULES = {
    "neo4j_writer": (
        "the operator's ungated bolt curation path — neo4j_writer.py's own "
        "docstring bans it from runtime/"
    ),
}


def _runtime_modules():
    for path in sorted(RUNTIME.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _bindings(tree: ast.AST) -> dict[str, str]:
    """name-in-this-module -> integrations submodule it is bound to.

    Covers `from central_command.integrations import jira`,
    `... import litellm as litellm_client`, and
    `import central_command.integrations.jira as j` — at module scope or inside
    a function, which is where most of `runtime/tools.py` imports them.
    """
    binds: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == PKG:
            for alias in node.names:
                binds[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG + "."):
                    binds[alias.asname or alias.name] = alias.name[len(PKG) + 1:]
    return binds


def _direct_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    """(submodule, imported_name, lineno) for every
    `from central_command.integrations.<mod> import <name>` — the spelling that
    binds a client function directly, with no module name left to walk."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PKG + "."):
            sub = node.module[len(PKG) + 1:]
            for alias in node.names:
                out.append((sub, alias.name, node.lineno))
    return out


def _reached_attributes():
    """(module_rel_path, submodule, attribute, lineno) for every attribute the
    runtime reaches on a bound integrations module."""
    for rel, tree in _runtime_modules():
        binds = _bindings(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in binds):
                yield rel, binds[node.value.id], node.attr, node.lineno


def _allowed(submodule: str, attribute: str) -> bool:
    if submodule in EXEMPT_MODULES:
        return True
    return attribute in READ_ALLOWLIST.get(submodule, set())


def test_the_walk_sees_the_clients_it_is_guarding():
    """A resolver that bound nothing would pass this file forever. Pin the two
    spellings that actually occur — a plain `from ... import jira` and the
    alias `litellm as litellm_client` — so a broken resolver fails loudly
    instead of silently approving everything."""
    reached = {(sub, attr) for _rel, sub, attr, _line in _reached_attributes()}
    assert ("jira", "get_issue") in reached, (
        "the import resolver no longer sees `from central_command.integrations "
        "import jira` in runtime/tools.py — this guard is not guarding anything"
    )
    assert ("litellm", "list_models") in reached, (
        "the import resolver no longer follows the `litellm as litellm_client` "
        "alias in runtime/tools.py — aliased imports are exactly the spelling "
        "this walk exists to catch"
    )
    assert len({sub for _rel, sub, _attr, _line in _reached_attributes()}) >= 5


def test_runtime_reaches_only_read_functions_on_credentialed_clients():
    offenders = sorted({
        f"{rel}:{line} {sub}.{attr}"
        for rel, sub, attr, line in _reached_attributes()
        if not _allowed(sub, attr)
    })
    assert offenders == [], (
        "the runtime tier reached an integrations attribute that is not on the "
        "confirmed-read allowlist in this file. Two legitimate answers, and "
        "only two:\n"
        "  (1) it IS a read — open the function in "
        "central_command/integrations/, confirm it changes nothing, and add it "
        "to READ_ALLOWLIST here with the others;\n"
        "  (2) it is a WRITE — it does not belong in runtime/ at all. Route it "
        "through a `propose_*` tool and let gateway/executor.py perform it "
        "after approval; the runtime tier holds no credentials on purpose.\n"
        f"Reached: {offenders}"
    )


def test_runtime_never_imports_a_write_only_integration():
    offenders = sorted(
        f"{rel}:{node.lineno} imports {banned}"
        for rel, tree in _runtime_modules()
        for node in ast.walk(tree)
        for banned in BANNED_MODULES
        if (isinstance(node, ast.ImportFrom)
            and ((node.module or "") == PKG
                 and any(a.name == banned for a in node.names)
                 or (node.module or "") == f"{PKG}.{banned}"))
        or (isinstance(node, ast.Import)
            and any(a.name == f"{PKG}.{banned}" for a in node.names))
    )
    assert offenders == [], (
        "runtime/ imported a module it may never hold: "
        f"{ {k: v for k, v in BANNED_MODULES.items()} }. Found: {offenders}"
    )


def test_a_write_function_imported_by_name_is_caught_too():
    """`from central_command.integrations.jira import transition_issue` binds
    the FUNCTION, leaving no module attribute for the walk above to see. That
    spelling is banned outright: reach a client through its module name, so
    every call site stays visible to the allowlist."""
    offenders = sorted(
        f"{rel}:{line} from {PKG}.{sub} import {name}"
        for rel, tree in _runtime_modules()
        for sub, name, line in _direct_imports(tree)
        if sub not in EXEMPT_MODULES
    )
    assert offenders == [], (
        "runtime/ imported a name straight out of an integrations submodule. "
        "Import the MODULE (`from central_command.integrations import jira`) "
        "and call `jira.<fn>` instead — a bare function name hides which client "
        "surface the runtime is using from the read-only allowlist in this "
        f"file: {offenders}"
    )


def test_runtime_never_binds_the_integrations_package():
    """`from central_command import integrations` would put every client —
    reads, writes and all — behind one name the allowlist walk cannot resolve.
    Bind submodules, never the package."""
    offenders = sorted(
        f"{rel}:{node.lineno}"
        for rel, tree in _runtime_modules()
        for node in ast.walk(tree)
        if (isinstance(node, ast.ImportFrom)
            and (node.module or "") == "central_command"
            and any(a.name == "integrations" for a in node.names))
        or (isinstance(node, ast.Import)
            and any(a.name == PKG and a.asname for a in node.names))
    )
    assert offenders == [], (
        "runtime/ bound the whole `central_command.integrations` package to one "
        "name, which hides every client call from the read-only allowlist in "
        f"this file. Import the submodules you need instead: {offenders}"
    )


def test_the_raw_litellm_transport_is_only_ever_asked_to_GET():
    """`litellm._call` is on the allowlist because `runtime/context.py` needs
    the `max_*` keys `list_models()` deliberately strips. But `_call` is the
    transport for every write in that client too — `_call("POST",
    "/model/new", ...)` is one character's difference from a credentialed
    registration performed inside the runtime tier. So the allowlist entry is
    narrowed here to the method it was granted for."""
    offenders = []
    for rel, tree in _runtime_modules():
        binds = _bindings(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_call"
                    and isinstance(node.func.value, ast.Name)
                    and binds.get(node.func.value.id) == "litellm"):
                continue
            method = node.args[0] if node.args else None
            if not (isinstance(method, ast.Constant) and method.value == "GET"):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        "runtime/ called litellm._call with something other than a literal "
        '"GET". The raw transport is allowed in the runtime tier for one '
        "reason — reading the `max_*` keys the public read strips — and any "
        "other method makes it a credentialed proxy WRITE performed by the "
        "tier that may not perform writes. Propose it and let the Executor "
        f"call it: {offenders}"
    )


@pytest.mark.parametrize("submodule", sorted(READ_ALLOWLIST))
def test_every_allowlisted_read_still_exists(submodule):
    """An allowlist that names functions the client no longer has is a list
    nobody has read in a while — and a renamed read is exactly when someone
    should look again."""
    source = (SRC / "integrations" / f"{submodule}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    missing = sorted(READ_ALLOWLIST[submodule] - defined)
    assert missing == [], (
        f"the read allowlist for `{submodule}` names {missing}, which "
        f"integrations/{submodule}.py no longer defines — re-derive the list "
        "from the call sites rather than leaving a stale entry standing"
    )
