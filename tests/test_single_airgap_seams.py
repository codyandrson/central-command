"""The single-node profile's dependency seams stay consistent across five files.

What 2026-08-30's air-gap redesign made true, and what would silently rot
without a walk:

* every image the profile pulls is in ``deploy/single/images.txt`` with a
  constraint, a locked tag and a locked digest (2026-09-03, design record
  ``2026-09-03-deploy-refactor-design.md`` D2). ``compose.yaml`` references
  each one as ``${CC_IMG_*:-<host>/<path>:<locked-tag>}`` and the Dockerfiles
  as ``${CC_REGISTRY_*}/<path>:<tag>``; ``resolve-images.sh`` is what turns
  the manifest into the refs the registry can serve. A ref that is not in the
  manifest is a floating pull at deploy time, exactly the drift the manifest
  exists to end — and a compose default whose tag is not the LOCKED tag would
  make a bare ``compose up`` deploy something the release never tested;
* every ``CC_*`` seam the scripts read is declared (commented or not) in the
  REPO-ROOT ``.env.example`` — the operator's only map of what can be set.
  ``deploy/single/env.example`` is gone: v2.42.0 merged it into that file
  (design record ``2026-09-23-airgap-check-configure-setup-design.md``, D1),
  so a new seam without a documented line fails here;
* ``deploy/airgap.env.example`` does not re-teach the deprecated
  ``UV_INDEX_URL`` as a live variable (uv reads ``UV_DEFAULT_INDEX``; the
  old name is still documented there ONLY as the thing not to use).

No podman here; this is a source walk plus ``bash -n``.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

# The bash that can run this repo's shell scripts. On Windows a bare "bash" is
# System32's WSL launcher (its error reads "The RPC call contains a handle
# that differs from the declared handle type") — 26 tests failed that way on
# the 2026-09-25 testbed run, all of them in files that shelled out with the
# bare name. `update._bash()` resolves Git Bash from git's own install.
from central_command.api.update import _bash as _resolve_bash  # noqa: E402
BASH = _resolve_bash() or "bash"


ROOT = pathlib.Path(__file__).resolve().parent.parent
SINGLE = ROOT / "deploy" / "single"
IMAGES_TXT = SINGLE / "images.txt"
COMPOSE = SINGLE / "compose.yaml"
# THE answer file's template — one file for the app and the deployment.
ENV_EXAMPLE = ROOT / ".env.example"
DOCKERFILES = [
    ROOT / "deploy" / "pi" / "graphiti" / "Dockerfile",
    ROOT / "deploy" / "k3s" / "sandbox.Dockerfile",
    ROOT / "central_command" / "crawler" / "Dockerfile",
]
SCRIPTS = [
    ROOT / "deploy" / "env-lib.sh",
    ROOT / "deploy" / "discover.sh",
    SINGLE / "setup.sh",
    SINGLE / "resolve-images.sh",
    SINGLE / "make-secrets.sh",
    SINGLE / "verify.sh",
    SINGLE / "update.sh",
    SINGLE / "build-graphiti-image.sh",
    SINGLE / "build-sandbox-image.sh",
    SINGLE / "build-crawler-image.sh",
    SINGLE / "discover-llm.sh",
    SINGLE / "update-run.sh",
    SINGLE / "machine-lib.sh",
    SINGLE / "questions-lib.sh",
]
REGISTRY_KEYS = {"dockerio", "ghcr", "mcr"}
COMPONENTS = {"core", "n8n", "graphiti-base", "sandbox-base", "crawler-base", "speech"}
REGISTRY_VAR = {"CC_REGISTRY_DOCKERIO": "dockerio", "CC_REGISTRY_GHCR": "ghcr", "CC_REGISTRY_MCR": "mcr"}
REGISTRY_HOST = {"docker.io": "dockerio", "ghcr.io": "ghcr", "mcr.microsoft.com": "mcr"}


def _manifest() -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """(registry-key, path) -> (constraint, locked tag, locked digest, component)."""
    rows = {}
    for line in IMAGES_TXT.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        assert len(parts) == 6, f"images.txt: expected 6 columns: {line!r}"
        key, path, constraint, tag, digest, comp = parts
        assert key in REGISTRY_KEYS, f"images.txt: unknown registry key {key!r}"
        # `-` declares the locked tag ROLLING (a series or channel like 16 /
        # main-stable): those move on every upstream rebuild, so the tag is the
        # pin and a digest there would fail weekly on a healthy registry.
        assert digest == "-" or re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (
            f"images.txt: bad digest for {path}"
        )
        assert comp in COMPONENTS, f"images.txt: unknown component {comp!r}"
        assert ":" not in path and "@" not in path, f"images.txt: path must carry no tag: {path!r}"
        # The constraint must actually admit the tag this release locked —
        # otherwise resolution would reject the very artifact it was tested on.
        # A constraint is a version SERIES: the tag equals it or continues it
        # at a non-digit (16 admits 16.10, not 160). Flavour lives in the tag.
        assert re.fullmatch(re.escape(constraint) + r"(\D.*)?", tag), (
            f"images.txt: locked tag {tag!r} does not satisfy its own constraint {constraint!r}"
        )
        rows[(key, path)] = (constraint, tag, digest, comp)
    return rows


def test_resolver_tag_matcher_self_test():
    """The substitution rule is the one piece of resolve-images.sh with real
    logic; its own --self-test pins it (same flavour, series boundary)."""
    # cwd= + basename, not the full path: on Windows the first `bash` on PATH
    # may be WSL's launcher, which cannot open a Windows path.
    r = subprocess.run(
        [BASH, "resolve-images.sh", "--self-test"],
        cwd=SINGLE, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_registry_curl_has_a_connect_timeout():
    """A registry that will never answer must cost 5 seconds, not 30.

    In the air gap with the ``CC_REGISTRY_*`` seams unset, every request
    ``reg_req`` makes is a TCP connect to a public registry that nothing can
    reach. ``--max-time 30`` alone means each one burns the full 30s: the work
    site's resolver dry run timed out at 120s before it could report a single
    row (2026-09-25). ``--connect-timeout`` is the seam — a source walk,
    because there is no unreachable registry to dial from a test.
    """
    text = (SINGLE / "resolve-images.sh").read_text(encoding="utf-8")
    start = text.index("reg_req() {")
    end = text.index("\nreg_tags() {", start)
    curls = [line for line in text[start:end].splitlines() if "curl " in line]
    assert curls, "reg_req no longer runs curl — this walk needs rewriting"
    for line in curls:
        assert "--connect-timeout" in line, (
            "every curl in reg_req needs --connect-timeout (an unreachable "
            f"registry otherwise costs the whole --max-time):\n  {line.strip()}"
        )


def test_an_honoured_pin_stays_invisible_to_the_self_write_check(tmp_path):
    """An operator pin must be honoured EVERY run, not just the first.

    `resolve-images.sh` decides "is this the operator's pin?" by comparing the
    `.env` value against the ref it last wrote (`manifest_ref_for`). An honoured
    pin is recorded verbatim — `<var> <host>/<path> <tag> (pinned) pinned` — so
    if that row counts as a self-write, then on the NEXT run `pinval == prev`
    and the pin is silently re-resolved against images.txt.

    Measured on the 2026-09-24 Windows run against a seeded mirror:
    `CC_IMG_REDIS=localhost:5000/mirror/redis:7-alpine` survived the first
    `fetch` and was quietly replaced by `localhost:5000/library/redis:7-alpine`
    on the next `check`. It only looked harmless because that mirror also held
    the canonical path; on a genuinely path-RENAMING mirror — the case D2 created
    the pin for — the second run would FAIL the row. The fix reads the mode
    column, which already carried the fact.
    """
    manifest = tmp_path / "installed.manifest"
    manifest.write_text(
        "# GENERATED by resolve-images.sh\n"
        "CC_IMG_REDIS localhost:5000/mirror/redis 7-alpine (pinned) pinned 2026-09-24T20:06:11Z\n"
        "CC_IMG_PYTHON localhost:5000/library/python 3.12-slim-bookworm sha256:abc locked 2026-09-24T20:06:13Z\n"
        "CC_IMG_POSTGRES localhost:5000/library/postgres 16.10 (substituted) substituted 2026-09-24T20:06:09Z\n",
        encoding="utf-8",
    )

    def ref_for(var: str) -> str:
        r = subprocess.run(
            [BASH, "-c",
             f'MANIFEST="{manifest.as_posix()}"; '
             + _manifest_ref_for_body() + f'; manifest_ref_for {var}'],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip()

    assert ref_for("CC_IMG_REDIS") == "", (
        "a row recorded `pinned` is the OPERATOR's, not a self-write — it must "
        "not come back from manifest_ref_for, or the pin dies on the next run"
    )
    # ...while the resolver's own writes still do, so a self-written ref is still
    # recognised and not mistaken for a pin.
    assert ref_for("CC_IMG_PYTHON") == "localhost:5000/library/python:3.12-slim-bookworm"
    assert ref_for("CC_IMG_POSTGRES") == "localhost:5000/library/postgres:16.10"
    assert ref_for("CC_IMG_ABSENT") == ""


def _manifest_ref_for_body() -> str:
    """The function under test, lifted out of the resolver so the test runs it
    rather than a copy of it."""
    text = (SINGLE / "resolve-images.sh").read_text(encoding="utf-8")
    start = text.index("manifest_ref_for() {")
    end = text.index("\n}\n", start) + len("\n}")
    return text[start:end]


def test_images_txt_is_well_formed():
    rows = _manifest()
    assert rows, "images.txt is empty"
    assert len({p for _, p in rows}) == len(rows), "duplicate image path in images.txt"


def _compose_refs() -> set[tuple[str, str]]:
    """Every third-party image compose.yaml deploys, as (registry-key, path).

    The fallback in ``${CC_IMG_X:-<ref>}`` is what a bare ``compose config``
    (and therefore a hand-run ``compose up``) deploys, so it is checked as
    strictly as the manifest itself: right registry, right path, LOCKED tag.
    """
    rows = _manifest()
    refs = set()
    for m in re.finditer(r"^\s*image:\s*(\S+)\s*$", COMPOSE.read_text(encoding="utf-8"), flags=re.M):
        ref = m.group(1)
        if ref.startswith("localhost/"):
            continue  # the three locally-built images: exact, never resolved
        m2 = re.fullmatch(r"\$\{(CC_IMG_[A-Z0-9_]+):-([^/]+)/(.+):([^:]+)\}", ref)
        assert m2, (
            f"compose.yaml: image ref {ref!r} must be "
            "${CC_IMG_<NAME>:-<registry>/<path>:<locked-tag>} — resolve-images.sh owns the variable"
        )
        var, host, path, tag = m2.groups()
        assert host in REGISTRY_HOST, f"compose.yaml: unknown registry host {host!r} in {ref!r}"
        key = REGISTRY_HOST[host]
        assert (key, path) in rows, f"compose.yaml deploys {host}/{path} with no line in images.txt"
        assert var == _img_var(path), f"compose.yaml: {ref!r} should use {_img_var(path)}"
        assert tag == rows[(key, path)][1], (
            f"compose.yaml: {path} defaults to tag {tag!r}, but images.txt locks {rows[(key, path)][1]!r} — "
            "a bare `compose up` would deploy something this release never tested"
        )
        refs.add((key, path))
    return refs


def _img_var(path: str) -> str:
    """resolve-images.sh's name derivation, mirrored (its `img_var`)."""
    name = path.upper().replace("/", "_").replace("-", "_")
    return "CC_IMG_" + name.removeprefix("LIBRARY_")


def _dockerfile_bases() -> set[tuple[str, str, str]]:
    """Every build base, as (registry-key, path, tag), read off the ARG default.

    Since v2.43.0 the base REF goes through the image manifest like any pulled
    image (design record ``2026-09-23-airgap-check-configure-setup-design.md``
    D2): ``FROM ${CC_IMG_<NAME>}``, with an ``ARG CC_IMG_<NAME>`` whose default
    is ``${CC_REGISTRY_*}/<path>:<locked-tag>``. Both halves are load-bearing.
    The variable is what ``resolve-images.sh`` writes and the build script
    passes, so a mirror lacking the locked base tag — or re-namespacing its
    path — reaches the build; the DEFAULT is what a bare ``podman build`` and
    the k3s build scripts (which pass no image arg) use, so it must be the
    tested base. The name must be the resolver's own derivation, or the build
    script would be passing a variable the resolver never writes.
    """
    bases = set()
    for df in DOCKERFILES:
        text = df.read_text(encoding="utf-8")
        froms = re.findall(r"^FROM\s+(\S+)", text, flags=re.M)
        assert froms, f"{df}: no FROM"
        for ref in froms:
            m = re.fullmatch(r"\$\{(CC_IMG_[A-Z0-9_]+)\}", ref)
            assert m, (
                f"{df}: FROM {ref!r} must be ${{CC_IMG_<NAME>}} — the base ref comes "
                "from images.txt through resolve-images.sh (design record 2026-09-23, D2)"
            )
            var = m.group(1)
            dm = re.search(rf"^ARG\s+{var}=(\S+)\s*$", text, flags=re.M)
            assert dm, f"{df}: ARG {var} must be declared, with a default, before FROM"
            dflt = dm.group(1)
            rm = re.fullmatch(r"\$\{(CC_REGISTRY_[A-Z]+)\}/(.+)", dflt)
            assert rm, (
                f"{df}: ARG {var}'s default {dflt!r} must be "
                "${CC_REGISTRY_*}/<path>:<locked-tag> so the registry host stays a seam"
            )
            assert re.search(rf"^ARG\s+{rm.group(1)}=", text, flags=re.M), (
                f"{df}: ARG {rm.group(1)} must be declared before {var} uses it"
            )
            path, _, tag = rm.group(2).rpartition(":")
            assert var == _img_var(path), (
                f"{df}: the base ARG is {var}, but resolve-images.sh writes "
                f"{_img_var(path)} for {path} — the build script would pass a variable "
                "nothing sets"
            )
            bases.add((REGISTRY_VAR[rm.group(1)], path, tag))
    return bases


def test_each_build_script_passes_its_base_ref_through():
    """The manifest only reaches a build if the script hands the variable over."""
    for script, var in (
        ("build-graphiti-image.sh", "CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP"),
        ("build-sandbox-image.sh", "CC_IMG_PYTHON"),
        ("build-crawler-image.sh", "CC_IMG_PLAYWRIGHT_PYTHON"),
    ):
        text = (SINGLE / script).read_text(encoding="utf-8")
        assert f'--build-arg "{var}=' in text, (
            f"{script} must pass --build-arg {var} — otherwise images.txt's base row "
            "is read by nothing and the Dockerfile default is the only path"
        )


def test_every_pulled_image_is_pinned_in_images_txt():
    rows = _manifest()
    for ref in _compose_refs():
        assert ref in rows, f"{ref[0]}/{ref[1]} is referenced but has no line in images.txt"
    # A Dockerfile's FROM tag is hardcoded, so it must be the LOCKED tag: a
    # substituted base is not what the build would use.
    for key, path, tag in _dockerfile_bases():
        assert (key, path) in rows, f"{key}/{path} is a build base with no line in images.txt"
        assert tag == rows[(key, path)][1], (
            f"{key}/{path}: the Dockerfile builds FROM :{tag} but images.txt locks "
            f":{rows[(key, path)][1]} — the build would not use the tested base"
        )


def test_images_txt_has_no_orphans():
    used = _compose_refs() | {(k, p) for k, p, _ in _dockerfile_bases()}
    for ref in _manifest():
        assert ref in used, f"images.txt pins {ref[1]} but nothing references it"


SEAM_RE = re.compile(r"\bCC_(?:REGISTRY_[A-Z]+|APT_(?:SECURITY_)?MIRROR|PYPI_INDEX_URL|PYTHON_MIRROR|NPM_REGISTRY)\b")


def _declared() -> set[str]:
    return set(re.findall(r"^#?\s*(CC_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.M))


def test_every_seam_the_scripts_read_is_declared_in_env_example():
    declared = _declared()
    for path in SCRIPTS + DOCKERFILES:
        for seam in set(SEAM_RE.findall(path.read_text(encoding="utf-8"))):
            if seam.endswith("_") or "${" in seam:
                continue
            assert seam in declared, f"{path.relative_to(ROOT)} reads {seam}, which .env.example does not declare"


# A PER-RUN override, never an answer: each of these is passed on the command
# line for one invocation (CC_VERIFY_LIVE=1 ./verify.sh) or handed over by a
# parent process, and writing it into .env would be a mistake rather than a
# configuration. Everything else a deploy script interpolates must have a
# documented line in .env.example — that is what makes the file the operator's
# whole map.
RUNTIME_ONLY = {
    # DERIVED and exported for compose, never written into .env (v2.43.0, design
    # record 2026-09-23 D4): each is composed from CC_CA_BUNDLE /
    # CC_TLS_INSECURE, and the rule since v2.42.0 is that one fact has one key.
    "CC_LITELLM_SSL_VERIFY", "CC_CA_BUNDLE_IN_CONTAINER", "CC_CA_BUNDLE_MOUNT_SRC",
    # deploy/single/machine-lib.sh's own constants: the paths it writes inside
    # the podman machine, and its source guard. Not answers — a machine's
    # containers.conf path is not the operator's choice.
    "CC_MACHINE_REGISTRIES_CONF", "CC_MACHINE_PROXY_CONF", "CC_MACHINE_CA_PEM",
    "CC_MACHINE_LIB_LOADED",
    # ...and the memory bar its verdict judges against (ledger F6). A threshold
    # the product decided, not a question: raising it would change what `check`
    # PASSES, which is a release decision rather than a site's answer.
    "CC_MEM_WANT_GB", "CC_MEM_BAR_MIB",
    "CC_VERIFY_LIVE", "CC_VERIFY_MAX_WAIT",   # verify.sh: which checks to run
    "CC_PROBE_TIMEOUT",                        # a slow backend, for one run
    "CC_SKIP_DB_BACKUP", "CC_BACKUP_DIR",      # update.sh's deliberate opt-outs
    "CC_UPDATE_DRIVEN", "CC_UPDATE_FORCE",     # set by update-run.sh / by hand
    "CC_UPDATE_DIR",                           # api/update.py hands it to the runner
    "CC_ENV_LIB_LOADED",                       # env-lib.sh's own source guard
    "CC_QUESTIONS_LIB_LOADED",                 # questions-lib.sh's own source guard
    # F25's print-once marker for the tls-insecure WARN: EXPORTED by
    # cc_tls_insecure_warn_once once it has fired, read by every child process
    # this run execs. A per-run fact, never an operator answer.
    "CC_TLS_INSECURE_WARNED",
    # setup.sh's own LISTS, not answers: which keys are ports and which
    # credentials make-secrets.sh owns (v2.44.0's `check` reads both).
    "CC_PORT_KEYS", "CC_GENERATED_KEYS",
    # The build scripts' print-what-you-would-run mode: a test/debug switch, not
    # an install answer (F34 — it is how the staged context is pinned without
    # podman).
    "CC_BUILD_DRY_RUN",
    # The retired UPSTREAM keys: discover-llm.sh still accepts them in DIRECT
    # mode (a bare probe against a server), but the provider lives in the
    # proxy's database now and validate() calls them out if they reappear.
    "CC_EMBED_BASE_URL", "CC_EMBED_API_KEY",
}


def test_env_example_declares_every_key_the_deployment_interpolates():
    """A new seam with no documented line in .env.example fails here.

    The whole point of one answer file is that it is also the whole map. So
    every ``${CC_...}`` / ``$CC_...`` in deploy/single/*.sh, compose.yaml,
    deploy/discover.sh and deploy/env-lib.sh must be declared — commented or
    not — in .env.example, except the resolver-owned ``CC_IMG_*`` family (one
    line per image, written by resolve-images.sh and documented as a family)
    and the per-run overrides above.
    """
    declared = _declared()
    sources = sorted(SINGLE.glob("*.sh")) + [
        COMPOSE, ROOT / "deploy" / "discover.sh", ROOT / "deploy" / "env-lib.sh",
    ]
    missing: dict[str, set[str]] = {}
    for path in sources:
        for m in re.finditer(r"\$\{?(CC_[A-Z0-9_]+)", path.read_text(encoding="utf-8")):
            key = m.group(1)
            if key.startswith("CC_IMG_") or key in RUNTIME_ONLY or key in declared:
                continue
            missing.setdefault(key, set()).add(path.name)
    assert not missing, (
        "undocumented deployment seams — add a line (commented is fine) to the "
        f".env.example deployment section, or to RUNTIME_ONLY if it is a per-run override: {missing}"
    )


# ── D3: the LLM catalog may be declared in the answer file (v2.44.0) ─────────
UPSTREAM_KEYS = [
    "CC_LLM_UPSTREAM_BASE_URL",
    "CC_LLM_UPSTREAM_API_KEY",
    "CC_LLM_UPSTREAM_MODEL_CC_DEFAULT",
    "CC_LLM_UPSTREAM_MODEL_GRAPHITI_LLM",
    "CC_LLM_UPSTREAM_MODEL_CC_EMBEDDING",
    "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO",
    "CC_LLM_UPSTREAM_MODEL_CC_TTS",
    "CC_LLM_UPSTREAM_MODEL_CC_STT",
]


def test_the_upstream_llm_keys_are_declared_in_env_example():
    """The catalog can be declared instead of typed into the LiteLLM UI (design
    record D3), so the answer file's template has to TEACH the keys — including
    one per alias, which is the part an operator cannot guess."""
    declared = _declared()
    for key in UPSTREAM_KEYS:
        assert key in declared, (
            f".env.example does not declare {key} — the alias-to-key mapping is "
            "the one part of D3 an operator cannot infer"
        )
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # The mapping table lives in ONE place, next to the keys.
    assert "cc_required_aliases" in text, (
        ".env.example must point at deploy/env-lib.sh's cc_required_aliases — the "
        "code that decides which aliases a deployment must declare"
    )


def test_the_required_alias_list_is_one_function():
    """bash decides it; setup.sh and `check` both ask that one function."""
    lib = (ROOT / "deploy" / "env-lib.sh").read_text(encoding="utf-8")
    assert "cc_required_aliases()" in lib and "cc_alias_env_key()" in lib
    setup = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    for fn in ("cc_required_aliases", "cc_alias_env_key"):
        assert fn in setup, f"setup.sh must derive the keys through {fn}, never by hand"
    # ...and the KEY derivation exists in python too (register-models.py turns
    # the values into rows), which is why both sides are pinned to each other in
    # tests/test_register_models_upstream.py.
    rm = (ROOT / "deploy" / "pi" / "litellm" / "register-models.py").read_text(encoding="utf-8")
    assert "def alias_env_key" in rm


def test_the_answer_file_is_one_file():
    """deploy/single/env.example and its .env are RETIRED (v2.42.0, D1)."""
    assert not (SINGLE / "env.example").exists(), (
        "deploy/single/env.example is back — its keys belong in the repo-root "
        ".env.example's deployment section"
    )
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert '"$HERE/.env"' not in code, (
                f"{script.name}: reads a deploy/single/.env — the answer file is $REPO_ROOT/.env"
            )
    # ...and compose can no longer find an .env beside itself, so every
    # invocation must name one.
    setup = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    assert '--env-file "$ENV_FILE" -f "$HERE/compose.yaml"' in setup, (
        "the compose wrapper must pass --env-file: there is no .env beside compose.yaml"
    )


def test_no_kube_play_path_survives():
    """The old substrate is DELETED, not kept as a second maintained path.

    (The removed air-gap bundle mechanism is the cautionary tale.)
    """
    assert not list(SINGLE.glob("*.yaml.tmpl")), "the kube-play templates are gone"
    assert not (SINGLE / "render.sh").exists(), "render.sh is gone — compose reads .env natively"
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert "kube play" not in code, f"{script.name}: still calls podman kube play"


def test_fetch_phase_runs_before_anything_deploys():
    text = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    # `machine` joined the order in v2.43.0, between preflight and fetch: the
    # podman machine has to trust the mirror BEFORE anything is pulled. In
    # v2.44.0 validate+preflight left the loop and became sections of the dry
    # `check` GATE, which runs before it (design record D5).
    m = re.search(
        r"for p in (machine fetch llm stack app verify test boot demo); do",
        text,
    )
    assert m, (
        "setup.sh's full run must go check -> machine -> fetch -> llm -> ..."
    )
    assert re.search(r"^\s*run_phase check; local crc=", text, flags=re.M), (
        "the full run must start with the check gate, before any mutating phase"
    )
    assert "build_if_missing" not in text, "stack must ASSERT images (need_image), never build them mid-deploy"


def test_airgap_env_example_teaches_the_live_uv_variable():
    text = (ROOT / "deploy" / "airgap.env.example").read_text(encoding="utf-8")
    assert "UV_DEFAULT_INDEX=" in text
    assert not re.search(r"^#?\s*UV_INDEX_URL=", text, flags=re.M), "UV_INDEX_URL is deprecated — only UV_DEFAULT_INDEX may be offered as a seam"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse(script: pathlib.Path):
    # cwd= + basename, not the full path: on Windows the first `bash` on PATH
    # may be WSL's launcher, which cannot open a Windows path.
    subprocess.run([BASH, "-n", script.name], cwd=script.parent, check=True)


# ── D4: the two trust knobs, and the fan-out that must reach every consumer ──
# The design record's table is the contract. A consumer that loses its variable
# here is a build that starts failing on a TLS-intercepted network months later,
# with nothing in the diff that looks like trust — so the table is a test.
ENV_LIB = ROOT / "deploy" / "env-lib.sh"
MACHINE_LIB = SINGLE / "machine-lib.sh"

# consumer -> (CA variable, insecure variable) as cc_export_tls_env must set them
HOST_FANOUT = {
    "curl": ("CURL_CA_BUNDLE", None),          # -k has no env var: the .curlrc
    "openssl/uv/python": ("SSL_CERT_FILE", None),
    "requests-based": ("REQUESTS_CA_BUNDLE", None),
    "pip": ("PIP_CERT", "PIP_TRUSTED_HOST"),
    "node": ("NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED"),
    "npm": ("NPM_CONFIG_CAFILE", "NPM_CONFIG_STRICT_SSL"),
    "git": ("GIT_SSL_CAINFO", "GIT_SSL_NO_VERIFY"),
    "uv-insecure": (None, "UV_INSECURE_HOST"),
}


def _export_tls_env_body() -> str:
    text = ENV_LIB.read_text(encoding="utf-8")
    start = text.index("cc_export_tls_env() {")
    end = text.index("\ncc__write_curlrc() {", start)
    return text[start:end]


def test_the_host_curl_gets_the_ca_as_an_OPTION_not_only_an_env_var(tmp_path):
    """A SCHANNEL curl ignores CURL_CA_BUNDLE, so the CA must also reach the
    generated .curlrc as a `cacert` line.

    Git for Windows ships a Schannel-built curl and Windows is this profile's
    target. Measured on the 2026-09-24 Windows run against a private CA with
    curl 8.21.0 (Schannel):

        CURL_CA_BUNDLE=<pem>   -> 000, certificate failure
        --cacert <pem>         -> 200 ("schannel: added 1 certificate(s) from
                                  CA file", per curl -v)

    So the CA does not need to be in the Windows Root store — it needs to arrive
    as an OPTION. Without the `cacert` line every host-side probe in `check`
    (the package indexes, the registry manifest HEADs, the upstream LLM) fails
    against a private CA while CC_CA_BUNDLE is set, which is the exact
    configuration the seam exists for.

    Both knobs may apply at once, and the file is REWRITTEN each run, so a knob
    turned back off must not leave its line behind.
    """
    def curlrc(**env) -> str:
        state = tmp_path / ("-".join(sorted(env)) or "none")
        r = subprocess.run(
            [BASH, "-c",
             f'. "{ENV_LIB.as_posix()}"; cc_export_tls_env "{state.as_posix()}"; '
             'printf "CURL_HOME=%s\n" "${CURL_HOME:-unset}"; '
             f'cat "{(state / "curl" / ".curlrc").as_posix()}" 2>/dev/null'],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    ca = tmp_path / "corp-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

    with_ca = curlrc(CC_CA_BUNDLE=str(ca))
    assert f"cacert = {ca}" in with_ca, (
        f"CC_CA_BUNDLE must become a `cacert` line in the .curlrc:\n{with_ca}"
    )
    assert "insecure" not in with_ca, "a CA is the opposite of turning verification off"
    assert "CURL_HOME=unset" not in with_ca, "curl has to be told where the file is"

    both = curlrc(CC_CA_BUNDLE=str(ca), CC_TLS_INSECURE="1")
    assert f"cacert = {ca}" in both and "insecure" in both, both

    only_insecure = curlrc(CC_TLS_INSECURE="1")
    assert "insecure" in only_insecure and "cacert" not in only_insecure, only_insecure


def test_the_trust_fanout_reaches_every_consumer_in_the_table():
    body = _export_tls_env_body()
    for consumer, (ca_var, insecure_var) in HOST_FANOUT.items():
        for var in (ca_var, insecure_var):
            if var is None:
                continue
            assert re.search(rf"\bexport {var}=", body), (
                f"deploy/env-lib.sh's cc_export_tls_env no longer exports {var} "
                f"(the {consumer} seam in the D4 fan-out table)"
            )
    # curl's insecure flag is a generated .curlrc, not a variable, and CURL_HOME
    # is how curl is told where to find it.
    assert "insecure" in body and "export CURL_HOME=" in body, (
        "curl's insecure seam is the generated .curlrc plus CURL_HOME"
    )
    # UV_NATIVE_TLS is deprecated in favour of UV_SYSTEM_CERTS — neither may
    # reappear as the CA seam (SSL_CERT_FILE is what uv reads for a bundle).
    for line in ENV_LIB.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0]
        assert "UV_NATIVE_TLS" not in code, (
            "UV_NATIVE_TLS is deprecated — uv's replacement is UV_SYSTEM_CERTS "
            "(naming it in a COMMENT, as the thing not to use, is fine)"
        )


def test_every_command_in_the_profile_calls_the_one_fanout():
    """One function, called everywhere — per-tool lists are what drifted."""
    for script in (
        SINGLE / "setup.sh", SINGLE / "update.sh", SINGLE / "verify.sh",
        SINGLE / "make-secrets.sh", SINGLE / "resolve-images.sh",
        SINGLE / "discover-llm.sh", SINGLE / "build-graphiti-image.sh",
        SINGLE / "build-sandbox-image.sh", SINGLE / "build-crawler-image.sh",
    ):
        assert "cc_export_tls_env" in script.read_text(encoding="utf-8"), (
            f"{script.name} reads .env but never calls cc_export_tls_env — the "
            "CA and insecure knobs would not reach the tools it drives"
        )


def test_every_command_that_sees_the_insecure_knob_says_so():
    """Never silent, never a PASS. One WARN line per run, naming consumers."""
    for script in (
        SINGLE / "setup.sh", SINGLE / "update.sh", SINGLE / "verify.sh",
        SINGLE / "make-secrets.sh", SINGLE / "resolve-images.sh",
        ROOT / "deploy" / "discover.sh",
        SINGLE / "build-graphiti-image.sh", SINGLE / "build-sandbox-image.sh",
        SINGLE / "build-crawler-image.sh",
    ):
        text = script.read_text(encoding="utf-8")
        assert "tls-insecure" in text, f"{script.name}: no tls-insecure line"
        # F25: the actual print moved behind cc_tls_insecure_warn_once (which
        # builds the line from cc_tls_insecure_warn_text internally), so a
        # caller may carry either name — or the literal wording, for the one
        # site (discover-llm.sh) that keeps a hand-written fallback.
        assert (
            "cc_tls_insecure_warn_text" in text
            or "cc_tls_insecure_warn_once" in text
            or "TLS verification is OFF" in text
        ), f"{script.name}: the insecure notice must use the shared wording"
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert 'pass "tls-insecure"' not in code, (
                f"{script.name}: CC_TLS_INSECURE=1 is never a PASS"
            )


BUILD_SCRIPTS = ("build-graphiti-image.sh", "build-sandbox-image.sh",
                 "build-crawler-image.sh")


def test_the_builds_carry_the_knobs_into_the_image():
    """A build container inherits no variable: it needs flags and a FILE.

    The CA used to travel as ``podman build --secret id=cc_ca,src=<pem>``. That
    is broken on Windows against a podman machine — podman joins a Windows
    separator into the machine-side temp path
    (``open /mnt/c/.../tmp.X\\podman-build-secret-N``), measured on the
    2026-09-24 run — so with ``CC_CA_BUNDLE`` set NONE of the three local images
    could build on the one platform this profile targets. A CA certificate is
    public material (the private key would be the secret, and no build ever saw
    one), so it is a file in a STAGED context now, and the secret must not come
    back: this test is the guard.
    """
    for script in BUILD_SCRIPTS:
        text = (SINGLE / script).read_text(encoding="utf-8")
        assert "--tls-verify=false" in text, f"{script}: no --tls-verify=false path"
        assert 'CC_TLS_INSECURE=1' in text, f"{script}: the insecure build-arg is missing"
        assert "cc_stage_build_context" in text, (
            f"{script}: the CA must travel as cc-ca.crt in a STAGED context "
            "(deploy/env-lib.sh's cc_stage_build_context)"
        )
        assert "$STATE_DIR/build/" in text, (
            f"{script}: the staged context belongs under the state dir, never in "
            "the checkout"
        )
        # Comments still TELL the story of the secret; code must not use it.
        code = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
        for banned in ("--secret", "id=cc_ca"):
            assert banned not in code, (
                f"{script}: `podman build {banned}` is what F34 removed — it "
                "cannot build at all on a Windows podman machine"
            )
    for df in DOCKERFILES:
        text = df.read_text(encoding="utf-8")
        assert "COPY cc-ca.cr[t] /usr/local/share/ca-certificates/" in text, (
            f"{df.name}: the CA must arrive as an OPTIONAL context file — the "
            "glob is what makes it optional for the k3s builds, which have none"
        )
        # The comments still TELL the story of the secret; instructions must not
        # use it.
        instructions = "\n".join(
            l for l in text.splitlines() if not l.lstrip().startswith("#")
        )
        assert "--mount=type=secret" not in instructions, (
            f"{df.name}: the build secret is gone (F34) — see the build scripts"
        )
        assert "/run/secrets/cc_ca" not in instructions, f"{df.name}: stale secret path"
        # The file may be EMPTY (that is how a podman build with no CA gets a
        # glob match), so the guard has to be -s and not -f.
        assert "[ -s /usr/local/share/ca-certificates/cc-ca.crt ]" in text, (
            f"{df.name}: an EMPTY cc-ca.crt must read as 'no CA' — test it with -s"
        )
        assert "update-ca-certificates" in text, f"{df.name}: the CA is never installed"
        assert 'Acquire::https::Verify-Peer "false"' in text, (
            f"{df.name}: apt has no insecure path"
        )
        # ...and the apt-insecure fragment still comes FIRST, because the CA
        # branch may have to apt-get install ca-certificates (v2.43.0 ordering).
        assert text.index('Acquire::https::Verify-Peer "false"') < text.index(
            "[ -s /usr/local/share/ca-certificates/cc-ca.crt ]"
        ), f"{df.name}: the insecure fragment must be written before apt is used"
        assert "$CC_CA_BUNDLE" not in text, (
            f"{df.name}: the CA PATH is the host's business; the context file is "
            "always cc-ca.crt"
        )


def test_every_podman_build_of_these_dockerfiles_has_a_cc_ca_crt_to_match():
    """A zero-match glob COPY is a no-op under BuildKit and an ERROR under buildah.

    Verified here: ``docker build`` (29.8.1, BuildKit) treats
    ``COPY cc-ca.cr[t] <dir>/`` with no match as a silent no-op. buildah — which
    is what ``podman build`` is — errors instead (containers/podman#25229,
    containers/buildah#3284). So every script that builds one of these
    Dockerfiles WITH PODMAN has to put a cc-ca.crt in the context even when there
    is no CA. The single-node scripts stage one (empty when CC_CA_BUNDLE is
    unset); the k3s scripts build on the chromebox with podman out of a remote
    staging directory, so they touch an empty one there.
    """
    for script in BUILD_SCRIPTS:
        text = (SINGLE / script).read_text(encoding="utf-8")
        assert "cc_stage_build_context" in text
    lib = (ROOT / "deploy" / "env-lib.sh").read_text(encoding="utf-8")
    assert ': >"$staged/cc-ca.crt"' in lib, (
        "cc_stage_build_context must create an EMPTY cc-ca.crt when there is no "
        "CA, or a podman build with no CC_CA_BUNDLE dies on the glob COPY"
    )
    for script in ("build-graphiti-image.sh", "build-sandbox-image.sh",
                   "build-crawler-image.sh"):
        text = (ROOT / "deploy" / "k3s" / script).read_text(encoding="utf-8")
        assert ": >cc-ca.crt" in text, (
            f"deploy/k3s/{script}: its chromebox build is `podman build`, so the "
            "context needs a cc-ca.crt for the glob to match"
        )


def _build_dry_run(script: str, tmp_path: pathlib.Path, ca: pathlib.Path | None):
    """Run one build script in CC_BUILD_DRY_RUN mode and return its stdout.

    The state directory is whatever this checkout's `.env` says (the scripts
    source it with `set -a`, so the environment cannot pre-empt it) — the test
    reads the staged path out of the output rather than dictating it.
    """
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "CC_BUILD_DRY_RUN": "1",
    }
    if ca is not None:
        env["CC_CA_BUNDLE"] = str(ca)
    r = subprocess.run([BASH, str(SINGLE / script)], capture_output=True,
                       text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.mark.parametrize("script,dockerfile", [
    ("build-graphiti-image.sh", "Dockerfile"),
    ("build-sandbox-image.sh", "sandbox.Dockerfile"),
    ("build-crawler-image.sh", "Dockerfile"),
])
def test_the_resolved_build_command_uses_the_staged_context(script, dockerfile, tmp_path):
    """The context IS the staged directory, and -f points inside it.

    ``CC_BUILD_DRY_RUN=1`` prints the resolved command and touches nothing, which
    is the only way to pin this on a host with no podman.
    """
    # The staged path is deterministic (<state>/build/<image>), so snapshot it
    # BEFORE the dry run: on a host that carries a real install (the Windows
    # testbed, 2026-09-25) the directory already exists from `fetch`, and
    # "must not exist afterwards" was asserting about the install, not the
    # dry run. What the dry run must not do is ADD or CHANGE anything there.
    image = script.replace("build-", "cc-").replace("-image.sh", "")
    state = subprocess.run(
        [BASH, "-c", f'. "{(ROOT / "deploy" / "env-lib.sh").as_posix()}"; '
                     f'cc_state_dir "{(ROOT / ".env").as_posix()}" "{ROOT.as_posix()}"'],
        capture_output=True, text=True).stdout.strip()
    staged = pathlib.Path(state) / "build" / image if state else None

    def snapshot():
        if staged is None or not staged.exists():
            return None
        return sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in staged.iterdir())

    before = snapshot()
    out = _build_dry_run(script, tmp_path, None)
    line = next(l for l in out.splitlines() if l.startswith("DRY-RUN build:"))
    argv = line.split(": ", 1)[1].split(" ")
    assert argv[0] == "podman" and argv[1] == "build", argv
    # The LAST argument is the context, and it is the staged dir — not the
    # checkout, and not a bare Dockerfile path.
    ctx = argv[-1]
    assert f"DRY-RUN context: {ctx} " in out, out
    assert "/build/" in ctx, ctx
    assert not ctx.startswith(str(ROOT)), (
        f"the build context must live outside the checkout, got {ctx}"
    )
    assert argv[argv.index("-f") + 1] == f"{ctx}/{dockerfile}", argv
    assert "--secret" not in argv, argv
    # A dry run touches nothing: whatever was staged before is exactly what is
    # staged after, and nothing appeared where nothing was.
    assert snapshot() == before, (
        f"CC_BUILD_DRY_RUN=1 changed {ctx} — it must only print"
    )


@pytest.mark.parametrize("script", BUILD_SCRIPTS)
def test_the_ca_is_staged_only_when_there_is_one(script, tmp_path):
    """With a CA it is copied in; without one the context file is EMPTY.

    Empty rather than absent because buildah errors on a zero-match glob — and
    the Dockerfile's `-s` test reads empty as "no CA".
    """
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nnot-a-real-cert\n-----END CERTIFICATE-----\n")
    with_ca = _build_dry_run(script, tmp_path, ca)
    assert f"cc-ca.crt <- {ca}" in with_ca, with_ca
    without = _build_dry_run(script, tmp_path, None)
    assert "cc-ca.crt EMPTY" in without, without
    assert str(ca) not in without


def test_staging_replaces_the_context_and_never_keeps_a_stale_ca(tmp_path):
    """`build/<image>/` is regenerable: last run's CA cannot survive into this one."""
    lib = ROOT / "deploy" / "env-lib.sh"
    src = tmp_path / "ctx"
    (src / "patches").mkdir(parents=True)
    (src / "Dockerfile").write_text("FROM scratch\n")
    (src / "patches" / "p.patch").write_text("x\n")
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nA\n-----END CERTIFICATE-----\n")
    staged = tmp_path / "state" / "build" / "cc-graphiti"

    def run(with_ca: bool):
        r = subprocess.run(
            [BASH, "-c",
             f'. "{lib}"; cc_stage_build_context "{staged}" '
             f'"{ca if with_ca else ""}" "{src}"'],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr

    run(True)
    assert (staged / "cc-ca.crt").read_text() == ca.read_text()
    # The whole context came along, subdirectories included...
    assert (staged / "patches" / "p.patch").exists()
    assert (staged / "Dockerfile").exists()
    # ...and nothing was written into the source tree.
    assert not (src / "cc-ca.crt").exists()

    run(False)
    assert (staged / "cc-ca.crt").exists(), (
        "the file must still EXIST (buildah errors on a zero-match glob)"
    )
    assert (staged / "cc-ca.crt").read_text() == "", "a stale CA survived a re-stage"


def test_only_the_two_outbound_containers_get_the_trust_env():
    """Only LiteLLM and the speech engine dial outward, so only they need it."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "SSL_VERIFY: ${CC_LITELLM_SSL_VERIFY:-True}" in text
    assert "SSL_CERT_FILE: ${CC_CA_BUNDLE_IN_CONTAINER:-}" in text
    assert "REQUESTS_CA_BUNDLE: ${CC_CA_BUNDLE_IN_CONTAINER:-}" in text
    # Two mounts, one per outbound service, with the neutral /dev/null default
    # so the volume list needs no conditional.
    assert text.count("${CC_CA_BUNDLE_MOUNT_SRC:-/dev/null}:/etc/cc/ca.pem:ro") == 2


def test_the_machine_phase_is_in_the_order_and_the_docs():
    """`machine` runs between preflight and fetch, and takes --dry-run."""
    setup = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    m = re.search(r"for p in ((?:\w+\s+)+\w+); do", setup)
    assert m, "the all-phases loop is gone"
    phases = m.group(1).split()
    assert phases[:2] == ["machine", "fetch"], phases
    assert "phase_machine --dry-run" in setup, (
        "preflight must REPORT the machine diff rather than hand out instructions"
    )
    # The writer only ever writes drop-ins.
    lib = MACHINE_LIB.read_text(encoding="utf-8")
    assert "registries.conf.d/cc-central-command.conf" in lib
    assert "containers.conf.d/cc-proxy.conf" in lib
    for main_file in ("/etc/containers/registries.conf\"", "/etc/containers/containers.conf\""):
        assert main_file not in setup, (
            "the machine phase must never write a MAIN containers configuration file"
        )


# ── F34: the trust step, BUILT, with and without a CA ────────────────────────
# v2.43.0's implementer verified the CA lands in the image bundle by hand. This
# is that check, automated: it lifts the COPY + RUN trust step verbatim out of
# each Dockerfile, builds it on a base this host already holds, and looks for the
# certificate inside the built image. It needs a container builder, so it SKIPS
# where there is none — that is the honest trade for making it a test at all.
# The COPY plus the whole RUN block it guards — the RUN ends at its unindented
# `fi`, which is why the pattern names that rather than "the next instruction".
TRUST_STEP = re.compile(
    r"^COPY cc-ca\.cr\[t\][^\n]*\nRUN set -e;.*?\n    fi\n", re.S | re.M)
BUILD_BASE = "python:3.12-slim-bookworm"   # ships ca-certificates + openssl


def _docker() -> str | None:
    for exe in ("docker", "podman"):
        try:
            r = subprocess.run([exe, "image", "inspect", BUILD_BASE],
                               capture_output=True, text=True)
        except FileNotFoundError:
            # Not installed at all (the Windows testbed has podman only, and
            # `docker` raised WinError 2 here, 2026-09-25) — that is "not on
            # this host", which the caller turns into a skip.
            continue
        if r.returncode == 0:
            return exe
    return None


def _trust_step(df: pathlib.Path) -> str:
    m = TRUST_STEP.search(df.read_text(encoding="utf-8"))
    assert m, f"{df.name}: the COPY/RUN trust step is not where it was"
    return m.group(0)


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.parent.name)
def test_the_trust_step_builds_with_and_without_a_ca(df, tmp_path):
    """The glob COPY is a no-op with no CA, and installs the CA when there is one."""
    exe = _docker()
    if exe is None:
        pytest.skip(f"no container builder holding {BUILD_BASE} on this host")

    step = _trust_step(df)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        f"FROM {BUILD_BASE}\nARG CC_TLS_INSECURE=0\n{step}\n", encoding="utf-8")

    def build(tag: str) -> subprocess.CompletedProcess:
        return subprocess.run([exe, "build", "-t", tag, str(ctx)],
                              capture_output=True, text=True)

    # 1. No CA. Under docker/BuildKit that is a context with NO cc-ca.crt at
    #    all — what a k3s build from the repo context is, and a zero-match glob
    #    COPY is a no-op there. Under podman/buildah a zero-match glob is an
    #    ERROR (containers/podman#25229 — the very reason every build script
    #    stages an EMPTY cc-ca.crt), so there the production case is the empty
    #    file, and the `-s` guard in the step must leave the bundle alone.
    #    Asserting the BuildKit behaviour against buildah failed the Windows
    #    testbed's test phase three times over (2026-09-25).
    if exe == "podman":
        (ctx / "cc-ca.crt").write_text("", encoding="utf-8")
    r = build("cc-trust-step-noca")
    assert r.returncode == 0, (
        ("an EMPTY cc-ca.crt must build and install nothing:\n" if exe == "podman"
         else "a zero-match `COPY cc-ca.cr[t]` must be a no-op:\n") + r.stdout + r.stderr
    )
    r = subprocess.run([exe, "run", "--rm", "cc-trust-step-noca",
                        "sh", "-c", "ls /usr/local/share/ca-certificates/"],
                       capture_output=True, text=True)
    if exe == "podman":
        # The empty file is copied (the step keeps it as a marker) but the `-s`
        # guard must not have installed it into the system bundle: nothing
        # under /etc/ssl/certs mentions the marker name.
        r2 = subprocess.run([exe, "run", "--rm", "cc-trust-step-noca", "sh", "-c",
                             "ls /etc/ssl/certs/ | grep -c cc-ca || true"],
                            capture_output=True, text=True)
        assert r2.stdout.strip() == "0", "an EMPTY CA file was installed into the bundle"
    else:
        assert "cc-ca.crt" not in r.stdout, "no CA was given, yet one is installed"

    # 2. A real (self-signed) CA in the context: it must reach the SYSTEM bundle,
    #    which is the file every ENV in these Dockerfiles points at.
    key = tmp_path / "ca.key"
    pem = tmp_path / "ca.pem"
    gen = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(pem), "-days", "2", "-subj",
         "/CN=Central Command Test CA"], capture_output=True, text=True)
    if gen.returncode != 0:
        pytest.skip("no usable openssl to mint a test CA: " + gen.stderr[-200:])
    body = [l for l in pem.read_text().splitlines()
            if l and "CERTIFICATE" not in l][0]
    (ctx / "cc-ca.crt").write_text(pem.read_text(), encoding="utf-8")

    r = build("cc-trust-step-ca")
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run(
        [exe, "run", "--rm", "cc-trust-step-ca", "sh", "-c",
         "grep -c '" + body + "' /etc/ssl/certs/ca-certificates.crt"],
        capture_output=True, text=True)
    assert r.stdout.strip() not in ("", "0"), (
        f"{df.name}: the staged CA never reached /etc/ssl/certs/ca-certificates.crt "
        "— which is what PIP_CERT / NPM_CONFIG_CAFILE / NODE_EXTRA_CA_CERTS point at"
    )


# ── F32: a one-certificate bundle plus a public source is a WARN ─────────────
def _check_ca_bundle_harness(env_file: pathlib.Path) -> str:
    """Run setup.sh's CA-completeness check against one answer file.

    setup.sh ends in `main "$@"`, so it cannot be sourced — the function and its
    seam list are lifted out and run against the real env-lib helpers, the same
    way the resolver's manifest lookup is tested above.
    """
    text = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    start = text.index("PUBLIC_SOURCE_SEAMS=(")
    end = text.index("\ncheck_required_keys() {", start)
    body = text[start:end]
    script = f"""
set -uo pipefail
ENV_FILE="{env_file}"
. "{ROOT}/deploy/env-lib.sh"
. "{SINGLE}/questions-lib.sh"
get_kv() {{ cc_get_kv "$@"; }}
is_placeholder() {{ cc_is_placeholder "$@"; }}
warn() {{ printf 'WARN %s: %s\\n' "$1" "$2"; }}
pass() {{ printf 'PASS %s: %s\\n' "$1" "$2"; }}
{body}
check_ca_bundle_covers_everything
"""
    r = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


ONE_CERT = "-----BEGIN CERTIFICATE-----\nA\n-----END CERTIFICATE-----\n"


def test_a_single_certificate_bundle_warns_while_a_public_source_is_reachable(tmp_path):
    """CC_CA_BUNDLE REPLACES the trust store, so it has to be COMPLETE.

    The operator who answers with only the mirror's CA loses pypi/npm/deb with
    curl exit 60 — which reads like a broken mirror. F32, 2026-09-24.
    """
    ca = tmp_path / "corp.pem"
    ca.write_text(ONE_CERT)
    env = tmp_path / ".env"

    # One certificate, and every public seam blank: WARN, with the recipe.
    env.write_text(f"CC_CA_BUNDLE={ca}\nCC_PYPI_INDEX_URL=\n")
    out = _check_ca_bundle_harness(env)
    assert out.startswith("WARN answers-ca-bundle:"), out
    assert "REPLACES the trust store" in out
    assert "CC_PYPI_INDEX_URL" in out and "CC_NPM_REGISTRY" in out
    assert "cat corporate.pem /etc/ssl/certs/ca-certificates.crt" in out
    assert "curl.se/docs/caextract.html" in out

    # Same bundle, every public source mirrored: nothing public is dialled.
    env.write_text(
        f"CC_CA_BUNDLE={ca}\n"
        "CC_REGISTRY_DOCKERIO=registry.corp.example\n"
        "CC_REGISTRY_GHCR=registry.corp.example\n"
        "CC_REGISTRY_MCR=registry.corp.example\n"
        "CC_PYPI_INDEX_URL=https://m.corp.example/pypi/simple\n"
        "CC_PYTHON_MIRROR=https://m.corp.example/python\n"
        "CC_NPM_REGISTRY=https://m.corp.example/npm/\n"
        "CC_APT_MIRROR=https://m.corp.example/debian\n"
        "CC_HF_ENDPOINT=https://m.corp.example/hf\n")
    out = _check_ca_bundle_harness(env)
    assert out.startswith("PASS answers-ca-bundle:"), out

    # A combined bundle is a PASS whatever the seams say.
    ca.write_text(ONE_CERT * 3)
    env.write_text(f"CC_CA_BUNDLE={ca}\n")
    out = _check_ca_bundle_harness(env)
    assert out.startswith("PASS answers-ca-bundle:"), out
    assert "3 certificates" in out

    # No bundle at all: the check has nothing to say.
    env.write_text("CC_CA_BUNDLE=\n")
    assert _check_ca_bundle_harness(env) == ""


# ── F26: the loopback rule exempts registry mirrors and image pins ──────────
def _loopback_check_harness(env_file: pathlib.Path) -> str:
    """Run setup.sh's `loopback-addressing` check against one answer file.

    setup.sh ends in `main "$@"`, so the check is lifted out by anchor and run
    stand-alone, the same technique as `_check_ca_bundle_harness` above.
    """
    text = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    start = text.index("# 127.0.0.1, never localhost")
    end = text.index("# schema.sql is bind-mounted", start)
    body = text[start:end]
    script = f"""
set -uo pipefail
ENV_FILE="{env_file}"
pass() {{ printf 'PASS %s: %s\\n' "$1" "$2"; }}
warn() {{ printf 'WARN %s: %s\\n' "$1" "$2"; }}
run_check() {{
{body}
printf 'LH=[%s]\\n' "${{lh:-}}"
}}
run_check
"""
    r = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_loopback_check_exempts_registry_mirror_and_image_pin_keys(tmp_path):
    """A registry mirror published by the podman machine is addressed as
    `localhost:5000` on purpose — the one spelling that reaches it from both
    the Windows host and inside the machine (measured on the 2026-09-24
    testbed; `127.0.0.1` does not). The loopback rule is about the APP's own
    URLs, so CC_REGISTRY_*/CC_IMG_* must not trip it while an app key still
    does."""
    env = tmp_path / ".env"
    env.write_text(
        "CC_REGISTRY_DOCKERIO=localhost:5000\n"
        "CC_REGISTRY_GHCR=localhost:5000\n"
        "CC_REGISTRY_MCR=localhost:5000\n"
        "CC_IMG_POSTGRES=localhost:5000/library/postgres:16\n"
        "CC_API_HOST_URL=http://localhost:8080\n",
        encoding="utf-8",
    )
    out = _loopback_check_harness(env)
    assert "WARN loopback-addressing" in out, out
    lh_line = next(l for l in out.splitlines() if l.startswith("LH="))
    assert "CC_API_HOST_URL" in lh_line, lh_line
    for exempt in ("CC_REGISTRY_DOCKERIO", "CC_REGISTRY_GHCR", "CC_REGISTRY_MCR", "CC_IMG_POSTGRES"):
        assert exempt not in lh_line, f"{exempt} should be exempt from the loopback check: {lh_line}"

    # No app key mentions localhost: PASS, even with every registry seam set.
    env.write_text(
        "CC_REGISTRY_DOCKERIO=localhost:5000\n"
        "CC_IMG_POSTGRES=localhost:5000/library/postgres:16\n",
        encoding="utf-8",
    )
    out = _loopback_check_harness(env)
    assert "PASS loopback-addressing" in out, out
    assert "WARN loopback-addressing" not in out, out


# ── F25: a single run WARNs "tls-insecure" exactly once ─────────────────────
def test_tls_insecure_warn_once_prints_exactly_one_line_per_run():
    """A single `./setup.sh check` used to call `load_env` several times and
    exec resolve-images.sh, the build scripts' dry runs and discover-llm.sh —
    each printing its own `WARN tls-insecure` line, up to five for one fact
    (F25, 2026-09-24 Windows testbed run). `cc_tls_insecure_warn_once` is the
    one gate: exactly one WARN per run, never zero when the knob is on."""
    r = subprocess.run(
        [BASH, "-c",
         f'. "{ENV_LIB.as_posix()}"; '
         'cc_tls_insecure_warn_once x; '
         'cc_tls_insecure_warn_once y || true'],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [l for l in r.stdout.splitlines() if l.startswith("WARN tls-insecure")]
    assert len(lines) == 1, r.stdout
    assert "x" in lines[0] and "y" not in lines[0], lines[0]


def test_tls_insecure_warn_once_is_inherited_by_a_child_process():
    """The dedup has to reach across process boundaries: setup.sh execs
    resolve-images.sh, the build scripts and discover-llm.sh as SEPARATE
    processes, so the marker must be an EXPORTED variable, not a local one."""
    script = f"""
set -uo pipefail
. "{ENV_LIB.as_posix()}"
cc_tls_insecure_warn_once "parent"
bash -c '. "{ENV_LIB.as_posix()}"; cc_tls_insecure_warn_once "child"' || true
"""
    r = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [l for l in r.stdout.splitlines() if l.startswith("WARN tls-insecure")]
    assert len(lines) == 1, r.stdout
    assert "parent" in lines[0], lines[0]


def test_a_standalone_script_still_warns_once():
    """Nothing upstream has warned yet: the FIRST call still prints — this is a
    print-ONCE gate, never a suppress-always one."""
    r = subprocess.run(
        [BASH, "-c", f'. "{ENV_LIB.as_posix()}"; cc_tls_insecure_warn_once solo'],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("WARN tls-insecure") == 1, r.stdout


def test_every_tls_insecure_warn_site_routes_through_the_shared_gate():
    """A new call site that prints its own `WARN tls-insecure` line instead of
    going through `cc_tls_insecure_warn_once` reintroduces F25. Only
    env-lib.sh's own definition, and discover-llm.sh's defensive fallback for
    when env-lib.sh failed to source, may construct the line by hand."""
    allowed_bare = {ROOT / "deploy" / "env-lib.sh", SINGLE / "discover-llm.sh"}
    pattern = re.compile(r'(?:warn\s+"tls-insecure"|echo\s+"WARN tls-insecure)')
    for script in SCRIPTS:
        if script in allowed_bare:
            continue
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert not pattern.search(code), (
                f"{script.name}: prints its own tls-insecure WARN instead of routing "
                "through cc_tls_insecure_warn_once (F25) — a run that touches this "
                "script and another tls-insecure consumer would double-print"
            )


def test_the_uv_python_fallback_never_syncs_the_project():
    """`uv run` inside the checkout discovers pyproject.toml and SYNCS the
    project — a universal resolution a Windows mirror holding only Windows
    wheels cannot satisfy (2026-09-25 testbed: every `$PY` call in the llm
    section died on uvloop) — and it writes uv.lock into the tree. The
    fallback is an interpreter, so every script passes --no-project."""
    for name in ("setup.sh", "discover-llm.sh", "resolve-images.sh", "verify.sh"):
        src = (SINGLE / name).read_text(encoding="utf-8")
        for line in src.splitlines():
            if 'PY="uv run' in line:
                assert "--no-project" in line, f"{name}: {line.strip()}"


def test_the_app_phase_derives_the_proxy_url_the_app_dials():
    """`configure` writes only what it asks, so CC_LLM_BASE_URL — the app's
    address for the proxy this profile deploys — must be COMPOSED by the app
    phase from the port answer. A configure-born .env without it made every
    live model resolve raise LLMProviderNotConfigured (Windows testbed,
    2026-09-25: the demo feed was a 500)."""
    src = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    assert re.search(r'set_kv_if_unset "\$ENV_FILE" CC_LLM_BASE_URL "http://127\.0\.0\.1:\$\{CC_LITELLM_PORT\}"', src), (
        "the app phase must derive CC_LLM_BASE_URL from CC_LITELLM_PORT"
    )
    assert "for k in CC_LLM_BASE_URL CC_DEFAULT_MODEL CC_LLM_API_KEY" in src, "status must check CC_LLM_BASE_URL and CC_DEFAULT_MODEL too"
