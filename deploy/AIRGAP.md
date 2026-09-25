# Air-gapped and mirrored installs

The map of every external source Central Command touches, the `.env` key that
redirects each one, and the loop you run to prove them all before installing
anything. It is not a mirroring project.

## Why this document exists

The work-site installs did not fail on reachability: the site has mirrors for
container images, PyPI, npm and Debian, and images pull. They failed on two
things. **A specific image tag was missing from the mirror** and a nearby one
had to be used. And **finding where that tag — or a CA, a proxy, an index — was
defined** meant chasing compose files, Dockerfiles, podman settings and several
`.env` files, and editing code to change a value.

So: there is exactly ONE file you edit (the repo-root `.env`), one command that
ASKS for what it does not answer yet (`configure`), one command that PROVES
every input while changing nothing (`check`), and nine phases that change
something afterwards. Every seam in the table below is a key in that file.
Nothing here asks you to edit a script, a Dockerfile, `images.txt`, or a
settings file inside the podman machine — a phase does each of those from `.env`.

## The two knobs

Trust is exactly **two** keys, because per-tool granularity is what drifted
(v2.43.0, design record
`docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md` D4):

* **`CC_CA_BUNDLE`** — a PEM the whole deployment should trust.
* **`CC_TLS_INSECURE=1`** — TLS verification off, everywhere the fan-out reaches.

### `CC_CA_BUNDLE` REPLACES the trust store — it must be COMPLETE

Every consumer in the table below takes the bundle as *the* CA file (curl's
`cacert`, `PIP_CERT`, npm's `cafile`, `NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO`,
and the copy installed inside a build). Nothing appends it to the public roots.
So a bundle holding only the corporate root works for the corporate mirror and
breaks every PUBLIC host the install still contacts — pypi.org,
registry.npmjs.org, deb.debian.org — with **curl exit 60**, which reads like a
broken mirror rather than an incomplete bundle.

The bundle must contain **every CA this install meets**: the corporate root, plus
the public roots whenever any mirror seam is left blank. Combine them:

```bash
# Linux
cat corporate.pem /etc/ssl/certs/ca-certificates.crt > bundle.pem
```

On **Windows** there is no system PEM to concatenate: take a copy of curl's own
`cacert.pem` from <https://curl.se/docs/caextract.html> and append the corporate
root to it. (`curl --ca-native`, which would use the Windows store instead, is
deliberately not used here: the same bundle has to serve uv, pip, npm, node, git
and the image builds, none of which read the Windows store.)

A site where EVERY mirror seam names a mirror is right to carry one certificate —
nothing public is dialled. `./setup.sh check` tells the two cases apart: it WARNs
when the bundle holds exactly one certificate while a public source seam is still
blank, and names the seams.

Prefer the CA: it keeps the supply chain verifiable. `CC_TLS_INSECURE=1` is a
supported answer for a site that relies on isolation instead — a decision the
operator is entitled to make, and one this document will not relitigate. It is
never silent and never a PASS: every command that sees it prints one
`WARN tls-insecure:` line naming the consumers *that command* drives.

| Consumer | `CC_CA_BUNDLE` | `CC_TLS_INSECURE=1` |
|---|---|---|
| curl (host) | `CURL_CA_BUNDLE` **and** a `cacert` line in the generated `<state>/curl/.curlrc` — a Schannel curl (Windows) ignores the variable and honours only the option | `insecure` in the same `.curlrc` (`CURL_HOME` points there) |
| uv | `SSL_CERT_FILE` (`UV_NATIVE_TLS` is deprecated → `UV_SYSTEM_CERTS`) | `UV_INSECURE_HOST=<index hosts>` |
| pip | `PIP_CERT` | `PIP_TRUSTED_HOST=<index hosts>` |
| npm | `NPM_CONFIG_CAFILE` | `NPM_CONFIG_STRICT_SSL=false` |
| node | `NODE_EXTRA_CA_CERTS` | `NODE_TLS_REJECT_UNAUTHORIZED=0` |
| git | `GIT_SSL_CAINFO` | `GIT_SSL_NO_VERIFY=1` |
| podman pulls | installed into the podman machine's trust store by `./setup.sh machine` (plus `podman machine set --import-native-ca` where podman ≥ 6.0 has the flag) | `insecure = true` per `[[registry]]` in the machine's `registries.conf` drop-in, also written by `./setup.sh machine` |
| the three image builds | copied into a STAGED build context as `cc-ca.crt` (`<state>/build/<image>/`); the Dockerfiles install it into the image trust store. **Not** a `--secret` — that is broken on a Windows podman machine, see below | `--tls-verify=false` + `--build-arg CC_TLS_INSECURE=1` |
| apt inside a build | the same staged CA | `Acquire::https::Verify-Peer "false"` in `/etc/apt/apt.conf.d/99cc-insecure` |
| pip / npm inside a build | `PIP_CERT` / `NPM_CONFIG_CAFILE`, set by the build's trust step | `PIP_TRUSTED_HOST` (from the index host), `NPM_CONFIG_STRICT_SSL=false` |
| LiteLLM (the LLM endpoint) | `SSL_CERT_FILE`, from the CA mounted read-only at `/etc/cc/ca.pem` | `SSL_VERIFY=False` |
| the speech engine (Hugging Face) | `REQUESTS_CA_BUNDLE`, same mount | **none exists** — `huggingface_hub` has no insecure switch. Use the CA, an HF mirror (`CC_HF_ENDPOINT`), pre-placed snapshots, or `CC_ENABLE_SPEECH=0` |

One function does the host side — `deploy/env-lib.sh`'s `cc_export_tls_env`,
called by every command in the profile — so a consumer cannot lose its variable
in one script and keep it in another
(`tests/test_single_airgap_seams.py` walks the table). The CA reaches a BUILD as
a `--secret`, never a build-arg (build-args show in `podman history`). The two
values compose reads (`CC_LITELLM_SSL_VERIFY`, `CC_CA_BUNDLE_IN_CONTAINER`) are
EXPORTED by `setup.sh` and never written into `.env`: they are composed from the
two keys above, and one fact has one key.

## Every external source, and its seam

All seams live in the **repo-root `.env`** — one answer file for the app and the
deployment since v2.42.0 (see `.env.example`'s "Deployment — single-node
profile" section). Blank means the public source.
`deploy/single/env.example`, `deploy/single/.env`, `web/.env` (on this profile)
and `deploy/discovery.conf` are retired; an existing install's are merged into
`.env` and moved aside on the next run.

| Source | Used by | Seam | Notes |
|---|---|---|---|
| docker.io | postgres, neo4j, redis, n8n, the graphiti and sandbox base images | `CC_REGISTRY_DOCKERIO` | a HOST prefix (`registry.corp.example`), never a URL |
| ghcr.io | LiteLLM, the speech engine (`speaches`) | `CC_REGISTRY_GHCR` | same shape |
| mcr.microsoft.com | the crawler base (Microsoft's Playwright image: browsers + OS libs baked in) | `CC_REGISTRY_MCR` | replaces Debian + Microsoft's browser CDN for that build |
| one image at a path this mirror renames, or a tag you verified yourself | any image, including the three build bases | `CC_IMG_<NAME>` | THE seam a host variable cannot express. An operator pin WINS: the resolver verifies that exact ref exists (manifest HEAD by tag, or by digest for a `…@sha256:…` ref, parsed from YOUR ref), `WARN`s that it honoured a pin, records it as `pinned`, and never rewrites it. A pin the registry does not have is a `FAIL` naming the key — a pin is checked, not trusted. Unset it to resolve against `images.txt` again |
| Debian archive | `apt-get` inside the graphiti and sandbox builds | `CC_APT_MIRROR`, `CC_APT_SECURITY_MIRROR` | build-args; the deb822 sources file is REWRITTEN from `/etc/os-release` (slim images ship no `sources.list`; security is a separate path on every mirror) |
| PyPI | the app's venv (uv), graphiti's `uv pip`, the crawler's `pip` | `CC_PYPI_INDEX_URL` | fanned out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*`, and `UV_INDEX_URL` is deprecated |
| python-build-standalone | uv, only when the host has no CPython 3.12 | `CC_PYTHON_MIRROR` | `UV_PYTHON_INSTALL_MIRROR`; a `file://` directory works |
| registry.npmjs.org | the cockpit build (`npm ci`), `sandbox-runtime` inside the sandbox build | `CC_NPM_REGISTRY` | `NPM_CONFIG_REGISTRY`; the lockfile's `resolved` URLs point at npmjs and npm rewrites them to the configured registry (its `replace-registry-host` default) — a lock regenerated AGAINST a mirror would not be rewritten back |
| huggingface.co | the speech engine's models (Kokoro TTS + faster-whisper STT), fetched by `cc-speech` when the `llm` phase installs them | `CC_HF_ENDPOINT`, or pre-place the hub snapshots in the `speech-models` volume | `HF_HUB_CACHE` is the volume; a present snapshot is not re-fetched. `CC_ENABLE_SPEECH=0` removes the source entirely (point `cc-tts`/`cc-stt` at your own engines) |
| huggingface.co | Whisper STT for the cockpit's *local* engine (k3s Node server only) | `WHISPER_MODELS_BASE_URL`, or pre-place `ggml-*.bin` in `config.whisperModelDir` | `whisper-local.ts` checks the local file first; unused once `cc-stt` is registered |
| the upstream LLM endpoint | LiteLLM — and `check`, directly from the host | `CC_LLM_UPSTREAM_BASE_URL`, `CC_LLM_UPSTREAM_API_KEY`, `CC_LLM_UPSTREAM_MODEL_<ALIAS>` | v2.44.0. One base, one key, one upstream model id per alias (the alias upper-cased, every non-alphanumeric `_`). `cc_required_aliases` in `deploy/env-lib.sh` decides which aliases this deployment needs — the four core ones always, `cc-tts`/`cc-stt` only with `CC_ENABLE_SPEECH=1`. **All three families are OPTIONAL** (v2.45.1): blank is the normal case and means the catalog is entered in the LiteLLM UI at the `llm` phase's deliberate pause; see below |
| a private/corporate CA (TLS interception, a self-signed mirror) | everything: host acquisition, the three builds, podman pulls, LiteLLM, the speech engine | `CC_CA_BUNDLE` | one key, fanned out — the table above |
| a mandatory egress proxy | every host-side acquisition, and (inside a podman machine) pulls and builds | `CC_PROXY` | fanned out to `http(s)_proxy` in both cases, `no_proxy` pinned to loopback; `./setup.sh machine` writes the machine's `containers.conf` `[engine] env` drop-in |
| — (verification off) | everything the CA row covers, except the speech engine | `CC_TLS_INSECURE=1` | the other supported answer to interception |
| — (credentials) | `deploy/discover.sh`'s probes | `CC_NETRC=1` | `--netrc`, so credentials stay in `~/.netrc` and never in `.env` |

Every key above is also a ROW in `deploy/single/questions.tsv`, which is what
lets `configure` ask for it and `check` validate it. Adding a seam means adding
that row (`tests/test_single_questions_schema.py` fails otherwise).

## Two host prerequisites the air gap cannot download for you

Both are HOST tools, so no `.env` seam fixes them — they have to be installed
before the install, and `check` now says so instead of finding out later.

* **podman-compose ≥ 1.6.0** (released 2026-06-03; `docker compose` has no
  floor). Below it the profile does not work: `up --wait` — how the deploy
  phases wait on `compose.yaml`'s healthchecks — arrived in 1.6.0, and so did
  the config-hash change that made a second `up -d` idempotent, without which
  every re-run fails with `container name ... is already in use`. The work
  site ran 1.5.0 (2026-09-25). `check`'s `compose-version` line is the gate;
  the fix is `uv tool install podman-compose==1.6.0` or
  `pip install podman-compose==1.6.0` from the PyPI mirror
  (`CC_PYPI_INDEX_URL`).
* **CPython 3.12 on the host**, or `CC_PYTHON_MIRROR`. The venv is built with
  `uv venv --python 3.12`, and with neither of those uv DOWNLOADS an
  interpreter from python-build-standalone on github.com. With `CC_AIRGAP=1`
  that download cannot happen, so `check`'s `python-3.12` line is a **FAIL**
  there rather than a warning (a host with only Python 3.14 is the case that
  produced it).

## The loop — `configure` → `check` → triage → `check` → `all`

```
./setup.sh configure   ->  ./setup.sh check  ->  triage: edit .env  ->  ./setup.sh check
                                                                    ...until green
                                                              ->  ./setup.sh   (the install)
```

**`./setup.sh configure`** (v2.45.0, design record D6) asks every question in
`deploy/single/questions.tsv` that `.env` does not answer yet — plain
`read -rp` prompts, grouped (`identity`, `features`, `network`, `mirrors`,
`llm`, `paths`; `--all` adds the ports and re-asks everything with the current
value as the default). It is the ONE command that creates `.env` from
`.env.example`. It prints the diff it will write (`set` / `keep`, secrets by
name only), writes nothing but `.env`, then runs `make-secrets.sh` so the
generated credentials exist too, and ends by pointing at `check`.

It **fails closed** (rustup's rule): with no terminal, or with
`--non-interactive`, it prompts for nothing. It lists every unanswered REQUIRED
key as a `USERACTION` and exits 3 — an installer that cannot ask does not guess.
That is also what makes `.env` a PRESEED file: fill it in on a connected
machine, carry it across with the release zip, and `configure` reports `keep`
for every row and asks nothing. (Under Git Bash, a non-TTY stdin is usually the
`winpty` symptom; the summary line says so.)

**`./setup.sh check`** (v2.44.0, D5) runs every check that can be made
**without changing anything** and prints one table, ending in
`CHECK: <n> pass, <n> warn, <n> fail, <n> action` and the state-dir path.
`./setup.sh check --list` names its eight sections: `answers`, `host`,
`machine`, `images`, `indexes`, `llm`, `compose`, `models`.

Two properties make the loop worth running:

* **it changes nothing but `.env`** — and inside `.env`, only `CC_STATE_DIR` and
  `CC_EMBED_DIM` (measured from the upstream embedder, written only when unset).
  Triage means editing `.env`, never a script, a Dockerfile or `images.txt`.
* **it is the GATE.** The full run starts with it and refuses to continue past a
  `FAIL` or a `USERACTION`. A WARN-only check continues with
  `--accept-warnings`, or an interactive `y`; with no terminal and no flag it
  stops and names the flag rather than deciding for you. An `answers-*`
  USERACTION means a key nobody has answered — that is `configure`'s job, and
  the message says so.

`deploy/discover.sh` still comes first on a network nobody has mapped: it probes
every commonly needed external resource and classifies each by failure MODE
(DNS vs refused vs timeout vs TLS interception vs auth), because the mode is the
diagnosis — a timeout is a default-deny firewall, a certificate failure that
clears with `-k` is an intercepting proxy, a 407 is proxy credentials. Its
report and evidence go to `$CC_STATE_DIR/discovery/` (outside the checkout:
they name internal hosts), and each finding's USERACTION names the `.env` key
that answers it — the same keys as the table above, plus
`CC_DISCO_MIRROR_<KEY>` for a resource with no seam of its own.
`./deploy/single/setup.sh diagnose` prints the state dir's path.

## What `check` cannot prove

Its ceiling is printed with the summary, and it is honest: **check proves
inputs, not builds.**

* A local image BUILD can still fail inside the build — the apt/pip/npm work
  happens there, and no dry check reaches it.
* The LiteLLM *alias* probes belong to the `llm` phase; `check` probes the
  UPSTREAM directly from the host instead.
* `CC_EMBED_DIM` is measured, never declared. `check` measures it against the
  upstream embedder and writes it only when unset; the `llm` phase measures it
  again through the proxy alias — the path production takes — and FAILS on a
  mismatch rather than overwriting it.
* The podman machine's own process environment (see below).

The target is "no CONFIGURATION failure at setup", not "no failure of any kind".

## The LLM: entered in the LiteLLM UI, and optionally declared in `.env`

**The catalog lives in LiteLLM's database, not in `.env`, and entering it in the
proxy's own UI is the primary method** — the same methodology the k3s profile
uses. The operator's reason, in one sentence: LiteLLM expresses provider nuance
(credentials, per-provider parameters, routing, fallbacks) that a flat answer
file cannot, and one method across both profiles beats two that drift.

So the `llm` phase creating `PLACEHOLDER` skeletons and PAUSING at exit 3 for
that entry is **a deliberate exception to "a full run does not stop"**, not a
defect. `check` says so up front with a PASS line (`llm: catalog will be entered
in the LiteLLM UI — setup pauses at the llm phase (exit 3) until the aliases
answer`) rather than a USERACTION: there is nothing to fix, only a pause to
expect. Fill the rows in at `http://127.0.0.1:4000/ui` (username `admin`,
password = `CC_LLM_PROXY_ADMIN_KEY`), then re-run — setup is idempotent and
fast-forwards.

**The `.env` declaration is an optional shortcut past that pause**, for the
simple case of one OpenAI-compatible endpoint serving every model (the three key
families are in the table above; `configure` offers them and NONE of them is
required). Its real value is not skipping a prompt but moving the failure
earlier: on an air-gapped install, half-deployed with the UI as the only
instrument is the worst place to discover that the endpoint, the key or a model
id is wrong. With the keys set, `check`'s `llm` section
probes that endpoint from the host with curl before any container exists:
the model list (a 404 there is a WARN — some gateways do not implement it, and
membership is then simply unchecked), one chat completion per distinct chat
model id, one `json_schema` round trip for the `graphiti-llm` model, and one
embedding call whose length IS `CC_EMBED_DIM`.

`register-models.py` stays CREATE-ONLY: with the keys unset it creates today's
`PLACEHOLDER` skeletons (which is what the k3s profile relies on), a skeleton is
UPDATED once `.env` declares the upstream, and a row the operator filled in is
never written to — it WINS over `.env`, and the script says so. A `127.0.0.1`
upstream is rewritten to `host.containers.internal` for the row, with a WARN:
the row is dialled by a container.

## The podman machine is a second host

On Windows and macOS podman runs inside a VM, and **that VM is a second host**:
`CC_CA_BUNDLE`, `CC_REGISTRY_*` and `CC_PROXY` do not reach a pull or a build
unless the machine itself carries them, and the Windows-side `registries.conf`
is parsed but **not honoured** for a machine-backed connection (podman#16532).

`./setup.sh machine` — between `check` and `fetch` — applies this over
`podman machine ssh`, idempotently, printing the diff before each write:

* the CA into the machine's trust store
  (`/etc/pki/ca-trust/source/anchors/cc-ca.pem` + `update-ca-trust`; the machine
  image is Fedora CoreOS, and a Debian/Ubuntu image falls back to
  `/usr/local/share/ca-certificates/` + `update-ca-certificates`), plus
  `podman machine set --import-native-ca=true` where this podman has that flag
  (podman ≥ 6.0) — it imports the **Windows** trust store, which is usually
  where an enterprise CA already is;
* `/etc/containers/registries.conf.d/cc-central-command.conf` — a **drop-in**,
  never the main file — with a `[[registry]]`/`[[registry.mirror]]` pair per
  configured `CC_REGISTRY_*`, and `insecure = true` (including for the host of
  every `CC_IMG_*` pin) when `CC_TLS_INSECURE=1`;
* `/etc/containers/containers.conf.d/cc-proxy.conf` — `[engine] env`, the
  engine's own environment, which is what pulls and builds travel through. The
  proxy VALUE is never printed; only the key name.

It then VERIFIES the CA with a live probe from inside the machine (a `curl` at
the registry; exit 60 is a certificate failure, a FAIL naming the code) rather
than reading a settings file — Podman Desktop's CA propagation is a known rough
edge (podman-desktop#3821). `./setup.sh machine --dry-run` reports current state
and the diff and writes nothing; that is what `check`'s `machine` section runs.
On bare Linux the whole phase is a no-op.

**What still needs a machine restart, and is therefore yours:** the machine's
own process environment is taken from the HOST environment at
`podman machine start`, so a proxy there stays a USERACTION
(`podman machine stop && HTTPS_PROXY=… podman machine start`). Writing systemd
drop-ins inside the machine is not doc-verified and is an open item below.

**Windows (Git Bash + a podman machine) — measured 2026-09-18, CORRECTED
2026-09-24.** Git for Windows' curl is schannel-only, with two consequences:

* it **IGNORES `CURL_CA_BUNDLE`** — but it does honour `--cacert`. Measured on
  2026-09-24 against a private CA with curl 8.21.0 (Schannel):
  `CURL_CA_BUNDLE=<pem>` → `000` (certificate failure), `--cacert <pem>` → `200`,
  with `curl -v` reporting `schannel: added 1 certificate(s) from CA file`. The
  earlier note here said the CA therefore had to be in the Windows Root store;
  **that was wrong.** It only has to reach curl as an OPTION, so `CC_CA_BUNDLE`
  is written as a `cacert` line into the setup-owned `.curlrc`. Putting the CA in
  the Root store still works and is usually there by policy anyway (as
  Administrator, `certutil -addstore Root <ca.cer>`; the `ca-windows-store` check
  names it), but it is no longer REQUIRED;
* it checks revocation, which an intercepting proxy cannot answer
  (`CRYPT_E_REVOCATION_OFFLINE`), so the same `.curlrc` carries
  `ssl-revoke-best-effort` on Windows unconditionally.

That `.curlrc` lives in the state directory, is found via `CURL_HOME`, and is
REWRITTEN every run — so a knob turned back off leaves no stale line. uv, npm
and node take the bundle from the variables as on Linux.

## Partial availability — the minimal move per missing source

The sources are independent: fix ONLY the seam your environment cannot serve and
leave every other seam alone. `deploy/discover.sh`'s report says which sources
are reachable and which mirrors can stand in.

| Missing source | Minimal move |
|---|---|
| Debian apt | apt is consumed ONLY inside the three local image builds — point `CC_APT_MIRROR`/`CC_APT_SECURITY_MIRROR` at the mirror discovery found. Nothing else in the profile touches apt. |
| NodeSource (apt-based Node) | Node ≥ 22 is a HOST prerequisite (it *runs* the cockpit). Pre-stage the official self-contained tarball — `node-v22.x-linux-<arch>.tar.xz` from nodejs.org or its mirror, untarred onto PATH — no apt involved. |
| registry.npmjs.org | `CC_NPM_REGISTRY` at the npm mirror (the lockfile's `resolved` URLs are rewritten automatically). |
| PyPI | `CC_PYPI_INDEX_URL` at the PyPI mirror; the pip installs inside the graphiti/crawler builds ride the same seam as build-args. |
| A container registry | `CC_REGISTRY_DOCKERIO`/`_GHCR`/`_MCR` at the registry mirror. A mirror that renames PATHS, or one tag that is simply absent: `CC_IMG_<NAME>`. |
| python-build-standalone | Install CPython 3.12 on the host, or point `CC_PYTHON_MIRROR` at a `file://` directory holding the archive. Under `CC_AIRGAP=1` neither being true is a `check` FAIL, not a warning — the download is known to be impossible. |
| huggingface.co | Speech: `CC_HF_ENDPOINT` at an HF mirror, or pre-place the two hub snapshots in the `speech-models` volume, or `CC_ENABLE_SPEECH=0` and register `cc-tts`/`cc-stt` at engines you already have. Cockpit-local Whisper (k3s only): pre-place `ggml-*.bin` in `config.whisperModelDir`, or set `WHISPER_MODELS_BASE_URL`. |

## Versions: a constraint, a lock, a resolution

`deploy/single/images.txt` carries THREE tiers per image (2026-09-03): a
**constraint** (the supported tag series), the tested **locked tag** and its
**digest**. `fetch` runs `resolve-images.sh`, which asks the mirror's
`/v2/<path>/tags/list` what it actually has:

* the locked tag → PASS, and its digest is verified (a mismatch FAILS: drifted
  or poisoned). A rolling series or channel tag (`16`, `7-alpine`,
  `main-stable`) carries `-` instead of a digest and is pinned by the tag alone;
* otherwise the newest tag satisfying the constraint, with a WARN naming the
  substitution and no digest check — deliberate: the digest pin defended against
  public-registry tag poisoning, a threat a mirrored air gap does not carry;
* otherwise a FAIL naming the constraint and what the mirror has, so you can pin.

A mirror serving no tags-list API still works: resolution falls back to a blind
pull of the locked tag, with a WARN. Resolved refs are written to `.env` as
`CC_IMG_*` (which `compose.yaml` reads) and recorded in
`$CC_STATE_DIR/installed.manifest` — the provenance, and the rollback record.
**A WARN-level substitution plus a green `./setup.sh verify` is a supported
install:** capability is proven by probes, not by version strings.

The three locally BUILT images resolve their base through the same manifest
(v2.43.0): the resolver writes `CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP`,
`CC_IMG_PYTHON` and `CC_IMG_PLAYWRIGHT_PYTHON`, each `build-*-image.sh` passes
its one as a `--build-arg`, and each Dockerfile's `ARG` default must equal its
`images.txt` row (a test fails the suite if they drift). `requirements.lock` is
the frozen Python resolution and `web/package-lock.json` the cockpit's: a mirror
that "gets updated regularly" changes nothing until a release bumps a pin —
that is the point.

## Open items (copied from the design record)

* The LiteLLM and speech containers get the CA and the insecure flag but NOT
  `CC_PROXY`: the machine drop-in sets the ENGINE's proxy (pulls and builds), and
  `containers.conf`'s `[engine] env` is documented as not reaching containers. An
  upstream reachable only through an egress proxy needs `HTTP(S)_PROXY`/`NO_PROXY`
  added to those two services — to be decided on the testbed, where an
  empty-string proxy variable's effect on httpx can be observed rather than
  assumed.
* The podman version in the site's Podman Desktop build decides whether
  `--import-native-ca` or the `certs.d` write is the primary CA path.
* Whether machine edits survive a Podman Desktop upgrade is unverified; `check`
  re-verifies each run either way.

## The k3s profile

The two-node deployment keeps its own seams: `deploy/k3s/registries.yaml.example`
(containerd mirrors; add `--disable-default-registry-endpoint` so a miss fails
loud), `deploy/airgap.env.example` (pip/uv), and `web/.npmrc.example` (npm). It
also still needs the k3s install script vendored and the NodeSource apt repo
mirrored. The Dockerfiles' build-args apply to its builds too (pass them to
`podman build` by hand; the k3s build scripts pass nothing and get the public
defaults). `configure`, `check` and the question schema are the single-node
profile's; the k3s driver has its own six phases.
