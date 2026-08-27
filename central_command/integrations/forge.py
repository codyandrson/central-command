"""Read-only git-hosting client (work-transition compatibility design, the
"Git hosting" catalog-implications bullet, corrected 2026-08-21: no Gitea).

Central Command is a CLIENT of existing forges, never a host: GitHub and
self-hosted GitLab, multiple NAMED instances (work has several GitLabs plus a
GitHub mirror). Instance config is a compact string
(`CC_FORGE_INSTANCES=name=kind:base_url,...`, parsed by `_parse_instances`);
per-instance auth is an optional PAT in `CC_FORGE_TOKEN_<NAME>` — missing
token = unauthenticated requests (fine for public GitHub, subject to rate
limits), never a refusal.

READ ONLY in this pass, deliberately: no `propose_*` tools and no Executor
handlers. Gated forge writes wait for a real use case (a work Jira/GitLab
workflow that actually needs one) rather than being built speculatively.

Every read tool returns the SAME normalized shape across both kinds — a
`repos`/`issues`/`merge_requests`/`commits` row looks identical whether it
came from GitHub or GitLab — so charters and skills never fork on provider.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client

KINDS = ("github", "gitlab")

# Keeps one file read context-lean regardless of the file's real size — the
# same honest-truncation shape as jira.py's MAX_CUSTOM_CHARS.
MAX_FILE_CHARS = 20_000


class ForgeError(Exception):
    pass


class Instance:
    __slots__ = ("name", "kind", "base_url")

    def __init__(self, name: str, kind: str, base_url: str):
        self.name = name
        self.kind = kind
        self.base_url = base_url.rstrip("/")


def _parse_instances(raw: str) -> dict[str, Instance]:
    """`name=kind:base_url,name2=kind2:base_url2` -> {name: Instance}.

    Defensive by design: a malformed entry fails loud and NAMES the offending
    entry, rather than silently dropping it or booting with a partial catalog.
    """
    instances: dict[str, Instance] = {}
    if not raw or not raw.strip():
        return instances
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ForgeError(
                f"CC_FORGE_INSTANCES entry {entry!r} is malformed — expected "
                "name=kind:base_url"
            )
        name, rest = entry.split("=", 1)
        name = name.strip()
        if ":" not in rest:
            raise ForgeError(
                f"CC_FORGE_INSTANCES entry {entry!r} is malformed — expected "
                "name=kind:base_url"
            )
        kind, base_url = rest.split(":", 1)
        kind, base_url = kind.strip(), base_url.strip()
        if not name or not base_url:
            raise ForgeError(
                f"CC_FORGE_INSTANCES entry {entry!r} is malformed — expected "
                "name=kind:base_url"
            )
        if kind not in KINDS:
            raise ForgeError(
                f"CC_FORGE_INSTANCES entry {entry!r} names an unknown kind "
                f"{kind!r} — must be one of {KINDS}"
            )
        if name in instances:
            raise ForgeError(f"CC_FORGE_INSTANCES names {name!r} more than once")
        instances[name] = Instance(name, kind, base_url)
    return instances


def _instances() -> dict[str, Instance]:
    return _parse_instances(settings.forge_instances)


def configured() -> bool:
    return bool(_instances())


def _token(name: str) -> str:
    """CC_FORGE_TOKEN_<NAME> (name upper-cased, '-'→'_'), the same per-instance
    convention as CC_LLM_PROXY_KEY_<AGENT_ID> in config.py. "" if unset."""
    return os.environ.get(f"CC_FORGE_TOKEN_{name.upper().replace('-', '_')}", "")


def _auth_headers(inst: Instance) -> dict:
    """GitHub: `Authorization: Bearer <PAT>`. GitLab: `PRIVATE-TOKEN: <PAT>` —
    verified against docs.gitlab.com/api/rest/authentication (2026-08-21):
    "the recommended approach is passing the token via the PRIVATE-TOKEN
    header, though OAuth-compliant Authorization Bearer headers are also
    supported" — PRIVATE-TOKEN is what GitLab's own examples use, so that is
    what we send. No token configured = no auth header at all (unauthenticated,
    not refused)."""
    token = _token(inst.name)
    if not token:
        return {}
    if inst.kind == "github":
        return {"Authorization": f"Bearer {token}"}
    return {"PRIVATE-TOKEN": token}


def _get_instance(name: str, op: str) -> Instance:
    instances = _instances()
    if not instances:
        raise ForgeError(f"{op} — no forge instances configured (set CC_FORGE_INSTANCES)")
    inst = instances.get(name)
    if inst is None:
        known = ", ".join(sorted(instances)) or "(none)"
        raise ForgeError(f"{op} — unknown forge instance {name!r}. Configured: {known}")
    return inst


async def _call(
    inst: Instance, method: str, path: str,
    params: dict | None = None, extra_headers: dict | None = None,
) -> httpx.Response:
    headers = _auth_headers(inst)
    if extra_headers:
        headers = {**headers, **extra_headers}
    async with httpx.AsyncClient(
        timeout=30, headers=headers, **http_client.client_kwargs()
    ) as client:
        return await client.request(method, inst.base_url + path, params=params)


def _check(resp: httpx.Response, op: str, ref: str | None = None) -> None:
    if 200 <= resp.status_code < 300:
        return
    r = f" for {ref}" if ref else ""
    detail = resp.text[:300]
    st = resp.status_code
    if st == 404:
        raise ForgeError(f"{op} — not found{r}")
    if st in (401, 403):
        raise ForgeError(f"{op} — auth failed ({st}){r} — check the instance's CC_FORGE_TOKEN_<NAME>")
    if st == 429:
        raise ForgeError(f"{op} — rate limited{r} — retry later")
    raise ForgeError(f"{op} — provider rejected the request ({st}){r}: {detail}")


def _project_id(repo: str) -> str:
    """GitLab addresses a project by numeric id OR URL-encoded full path
    ('group/subgroup/project'); repo is always given as a path here."""
    return quote(repo, safe="")


# --- row normalizers (provider shape -> the flat shape agents reason over) ---


def _gh_repo_row(r: dict) -> dict:
    return {"full_name": r.get("full_name"), "description": r.get("description"),
            "url": r.get("html_url"), "default_branch": r.get("default_branch")}


def _gl_repo_row(r: dict) -> dict:
    return {"full_name": r.get("path_with_namespace"), "description": r.get("description"),
            "url": r.get("web_url"), "default_branch": r.get("default_branch")}


def _gh_issue_row(it: dict) -> dict:
    return {"number": it.get("number"), "title": it.get("title"), "state": it.get("state"),
            "author": (it.get("user") or {}).get("login"), "url": it.get("html_url"),
            "created_at": it.get("created_at"), "updated_at": it.get("updated_at")}


def _gl_issue_row(it: dict) -> dict:
    return {"number": it.get("iid"), "title": it.get("title"), "state": it.get("state"),
            "author": (it.get("author") or {}).get("username"), "url": it.get("web_url"),
            "created_at": it.get("created_at"), "updated_at": it.get("updated_at")}


def _gh_mr_row(it: dict) -> dict:
    return {"number": it.get("number"), "title": it.get("title"), "state": it.get("state"),
            "author": (it.get("user") or {}).get("login"), "url": it.get("html_url"),
            "source_branch": (it.get("head") or {}).get("ref"),
            "target_branch": (it.get("base") or {}).get("ref"),
            "created_at": it.get("created_at"), "updated_at": it.get("updated_at")}


def _gl_mr_row(it: dict) -> dict:
    return {"number": it.get("iid"), "title": it.get("title"), "state": it.get("state"),
            "author": (it.get("author") or {}).get("username"), "url": it.get("web_url"),
            "source_branch": it.get("source_branch"), "target_branch": it.get("target_branch"),
            "created_at": it.get("created_at"), "updated_at": it.get("updated_at")}


def _gh_commit_row(it: dict) -> dict:
    commit = it.get("commit") or {}
    author = commit.get("author") or {}
    return {"sha": it.get("sha"), "message": commit.get("message"),
            "author": author.get("name"), "date": author.get("date"),
            "url": it.get("html_url")}


def _gl_commit_row(it: dict) -> dict:
    return {"sha": it.get("id"), "message": it.get("message"),
            "author": it.get("author_name"), "date": it.get("created_at"),
            "url": it.get("web_url")}


_STATE_MAP = {"open": "opened", "closed": "closed", "all": "all"}


# --- reads --------------------------------------------------------------------


async def list_instances() -> dict:
    """Every configured instance — name + kind, NEVER tokens."""
    instances = _instances()
    out = {"ok": True, "kind": "forge", "operation": "listInstances",
           "instances": [{"name": i.name, "kind": i.kind} for i in instances.values()],
           "count": len(instances)}
    if not instances:
        out["note"] = "no forge instances configured (CC_FORGE_INSTANCES is unset)"
    return out


async def search_repos(instance: str, query: str) -> dict:
    inst = _get_instance(instance, "searchRepos")
    if inst.kind == "github":
        resp = await _call(inst, "GET", "/search/repositories", {"q": query, "per_page": 20})
        _check(resp, "searchRepos")
        repos = [_gh_repo_row(r) for r in resp.json().get("items") or []]
    else:
        resp = await _call(inst, "GET", "/api/v4/projects", {"search": query, "per_page": 20})
        _check(resp, "searchRepos")
        items = resp.json() if isinstance(resp.json(), list) else []
        repos = [_gl_repo_row(r) for r in items]
    return {"ok": True, "kind": "forge", "operation": "searchRepos",
            "instance": instance, "repos": repos, "count": len(repos)}


async def read_file(instance: str, repo: str, path: str, ref: str | None = None) -> dict:
    inst = _get_instance(instance, "readFile")
    ref_out = ref or "HEAD"
    if inst.kind == "github":
        params = {"ref": ref} if ref else None
        # The raw Accept header is the whole trick: it turns the contents
        # endpoint's normal base64-wrapped JSON into the file's plain bytes,
        # so no manual base64 decode is needed here.
        resp = await _call(
            inst, "GET", f"/repos/{repo}/contents/{quote(path, safe='/')}", params,
            extra_headers={"Accept": "application/vnd.github.raw+json"},
        )
        _check(resp, "readFile", f"{repo}/{path}")
        if "application/json" in resp.headers.get("content-type", ""):
            raise ForgeError(f"readFile — {path!r} in {repo} is a directory, not a plain file")
        content = resp.text
    else:
        resp = await _call(
            inst, "GET",
            f"/api/v4/projects/{_project_id(repo)}/repository/files/{quote(path, safe='')}/raw",
            {"ref": ref_out},
        )
        _check(resp, "readFile", f"{repo}/{path}")
        content = resp.text
    truncated = len(content) > MAX_FILE_CHARS
    if truncated:
        content = content[:MAX_FILE_CHARS] + f"\n… [TRUNCATED at {MAX_FILE_CHARS} chars]"
    return {"ok": True, "kind": "forge", "operation": "readFile", "instance": instance,
            "file": {"repo": repo, "path": path, "ref": ref, "content": content,
                     "truncated": truncated}}


async def list_issues(instance: str, repo: str, state: str = "open") -> dict:
    inst = _get_instance(instance, "listIssues")
    if inst.kind == "github":
        gh_state = state if state in ("open", "closed", "all") else "open"
        resp = await _call(inst, "GET", f"/repos/{repo}/issues", {"state": gh_state, "per_page": 30})
        _check(resp, "listIssues", repo)
        # GitHub's issues endpoint also returns pull requests; a PR carries a
        # `pull_request` key that a genuine issue never does.
        issues = [_gh_issue_row(it) for it in resp.json() if isinstance(it, dict) and "pull_request" not in it]
    else:
        gl_state = _STATE_MAP.get(state, "opened")
        resp = await _call(inst, "GET", f"/api/v4/projects/{_project_id(repo)}/issues",
                            {"state": gl_state, "per_page": 30})
        _check(resp, "listIssues", repo)
        items = resp.json() if isinstance(resp.json(), list) else []
        issues = [_gl_issue_row(it) for it in items]
    return {"ok": True, "kind": "forge", "operation": "listIssues",
            "instance": instance, "repo": repo, "issues": issues, "count": len(issues)}


async def get_issue(instance: str, repo: str, number: int) -> dict:
    inst = _get_instance(instance, "getIssue")
    ref = f"{repo}#{number}"
    if inst.kind == "github":
        resp = await _call(inst, "GET", f"/repos/{repo}/issues/{number}")
        _check(resp, "getIssue", ref)
        it = resp.json()
        issue = _gh_issue_row(it)
        issue["body"] = it.get("body")
    else:
        resp = await _call(inst, "GET", f"/api/v4/projects/{_project_id(repo)}/issues/{number}")
        _check(resp, "getIssue", ref)
        it = resp.json()
        issue = _gl_issue_row(it)
        issue["body"] = it.get("description")
    return {"ok": True, "kind": "forge", "operation": "getIssue", "instance": instance, "issue": issue}


async def list_merge_requests(instance: str, repo: str, state: str = "open") -> dict:
    """PRs on GitHub, MRs on GitLab — one tool, one shape."""
    inst = _get_instance(instance, "listMergeRequests")
    if inst.kind == "github":
        gh_state = state if state in ("open", "closed", "all") else "open"
        resp = await _call(inst, "GET", f"/repos/{repo}/pulls", {"state": gh_state, "per_page": 30})
        _check(resp, "listMergeRequests", repo)
        rows = [_gh_mr_row(it) for it in resp.json() if isinstance(it, dict)]
    else:
        gl_state = _STATE_MAP.get(state, "opened")
        resp = await _call(inst, "GET", f"/api/v4/projects/{_project_id(repo)}/merge_requests",
                            {"state": gl_state, "per_page": 30})
        _check(resp, "listMergeRequests", repo)
        items = resp.json() if isinstance(resp.json(), list) else []
        rows = [_gl_mr_row(it) for it in items]
    return {"ok": True, "kind": "forge", "operation": "listMergeRequests",
            "instance": instance, "repo": repo, "merge_requests": rows, "count": len(rows)}


async def get_merge_request(instance: str, repo: str, number: int) -> dict:
    inst = _get_instance(instance, "getMergeRequest")
    ref = f"{repo}#{number}"
    if inst.kind == "github":
        resp = await _call(inst, "GET", f"/repos/{repo}/pulls/{number}")
        _check(resp, "getMergeRequest", ref)
        it = resp.json()
        mr = _gh_mr_row(it)
        mr["body"] = it.get("body")
    else:
        resp = await _call(inst, "GET", f"/api/v4/projects/{_project_id(repo)}/merge_requests/{number}")
        _check(resp, "getMergeRequest", ref)
        it = resp.json()
        mr = _gl_mr_row(it)
        mr["body"] = it.get("description")
    return {"ok": True, "kind": "forge", "operation": "getMergeRequest",
            "instance": instance, "merge_request": mr}


async def list_commits(instance: str, repo: str, ref: str | None = None, limit: int = 20) -> dict:
    inst = _get_instance(instance, "listCommits")
    bound = min(100, max(1, limit))
    if inst.kind == "github":
        params: dict = {"per_page": bound}
        if ref:
            params["sha"] = ref
        resp = await _call(inst, "GET", f"/repos/{repo}/commits", params)
        _check(resp, "listCommits", repo)
        rows = [_gh_commit_row(it) for it in resp.json() if isinstance(it, dict)]
    else:
        params = {"per_page": bound}
        if ref:
            params["ref_name"] = ref
        resp = await _call(inst, "GET", f"/api/v4/projects/{_project_id(repo)}/repository/commits", params)
        _check(resp, "listCommits", repo)
        items = resp.json() if isinstance(resp.json(), list) else []
        rows = [_gl_commit_row(it) for it in items]
    return {"ok": True, "kind": "forge", "operation": "listCommits",
            "instance": instance, "repo": repo, "commits": rows, "count": len(rows)}
