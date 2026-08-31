# Vendored dependency docs

Offline reference for every Central Command dependency, for use by Claude Code, by
Central Command's own agents, and by the operator — on the air-gapped deployment target,
which has **no internet access, ever**, before or after going live. The only
thing that crosses onto that machine is a single pulled file (a repo
archive/zip), so anything committed here travels for free with the rest of
the repo. If it isn't in this tree, it will never be available there.

## Layout

Each dependency gets its own directory: `docs/vendor/<name>/`, containing
whatever the upstream project's own `docs/` folder (or README/LICENSE, if
that's all there is) held at the pinned ref, plus a `MANIFEST.yaml`:

```yaml
name: pydantic-ai
source_repo: https://github.com/pydantic/pydantic-ai
source_ref: v2.18.4
fetched_url: https://codeload.github.com/pydantic/pydantic-ai/tar.gz/v2.18.4
fetched_date: 2026-08-25
subpaths: [docs README.md LICENSE*]
```

This is raw upstream doc source (mkdocs/docusaurus markdown, whatever format
the project uses), not a summary — pulled once on a machine with internet,
never regenerated automatically.

## Regenerating / adding a package

Use `scripts/vendor_docs_fetch.sh <org/repo> <ref> <local-name> [subpath...]`.
`ref` must be a real tag/branch/commit — verify it exists (GitHub API, the
project's releases page) before calling the script; never guess a URL.
Re-run it whenever a dependency's pinned version changes (see `pyproject.toml`
and `web/package.json` / `web/package-lock.json` for what's pinned, and
`deploy/k3s/*.yaml` for pinned container images like n8n/Neo4j/LiteLLM).

## What's covered

~543 MB across 53 directories (2026-08-25). See each package's own
`MANIFEST.yaml` for its exact pinned ref and source URL. Skipped on purpose:
thin stdlib-adjacent wrappers with no real docs beyond their docstrings
(`pydantic-settings`, `python-dotenv`, `croniter`, `pyyaml`, `pynacl`,
`cryptography`) — training-data knowledge plus `--help`/docstrings covers
these; re-add if one of them ever bites us the way Graphiti/LiteLLM have.

**Python runtime:** pydantic-ai, graphiti-core, litellm, neo4j-python-driver,
neo4j-cypher-manual, neo4j-operations-manual, fastapi, uvicorn, pydantic,
httpx, asyncpg, trafilatura, markdownify, markitdown, pypdfium2.

**n8n:** n8n, plus the separate docs repo (`n8n-io/n8n-docs`, vendored as
`n8n/`).

**Web — core:** react, vite, typescript, tailwindcss.

**Web — UI/utility:** hono, hono-website, hono-node-server,
hono-zod-validator, zod, radix-ui-primitives, radix-ui-website, dompurify,
cva, clsx, class-variance-authority.

**Web — viz/editor:** cytoscape, recharts, codemirror (11 packages +
website), highlight.js, lightweight-charts.

**Web — misc:** dnd-kit, react-markdown, remark-gfm, node-pty, whisper-node,
json5, diff-sequences, tw-animate-css.

**Windows/Exchange (for the work-environment Exchange adapter build):**
powershell, exchange-powershell, ews-exchange-dev, msgraph.

**Infra & platform:** k3s, victorialogs, fluent-bit, claude-code,
anthropic-platform, atlassian-dc-rest.

**Model evaluation data:** `model-library/` — fetched by
`scripts/model_library_fetch.sh` rather than `vendor_docs_fetch.sh` (pricing
and benchmark datasets, not doc trees); it self-documents via its own
`README.md`/`MANIFEST.yaml`.

**Known approximations (see each MANIFEST.yaml for detail) — the doc-source
repo doesn't tag releases in lockstep with the library's own version, so
these were vendored from that repo's default/main branch rather than an
exact-matching ref:** litellm (docs moved to the separate
`BerriAI/litellm-docs`, untagged), n8n-docs (untagged), react.dev,
TypeScript-Website, tailwindcss.com, hono-website, radix-ui-website, k3s
(k3s-io/docs is not version-branched per k3s release — vendored from
`main`; our deployed k3s is v1.34), fluent-bit (fluent-bit-docs is untagged
per release — vendored from `master`; we run fluent-bit 5.1.1). This is
expected for most "marketing site" doc repos — they publish continuously
rather than cutting versioned doc releases — and is still far better than
nothing air-gapped.

**claude-code and anthropic-platform are live-site `llms-full.txt` snapshots,
not git repos** — fetched directly by URL (`code.claude.com/docs/llms-full.txt`,
`platform.claude.com/llms-full.txt`), no version pin, re-fetch periodically.

**atlassian-dc-rest was wget-mirrored, not tarball-fetched** — the Jira and
Confluence REST references are static HTML doc trees with no git repo, so
`scripts/vendor_docs_fetch.sh` doesn't apply; each was pulled with
`wget --mirror --no-parent --convert-links --adjust-extension
--page-requisites`. Pinned to the closest published doc versions since no
exact match exists for our live instances: Jira docs 9.17.0 vs live Jira DC
10.3.23, Confluence docs 8.5.0 vs live Confluence 9.2.22 (no 10.x/9.2.x doc
trees are published — confirmed 404). The DC REST surface is stable across
those gaps but treat it as an approximation.

**graphiti-core is a special case, not a gap anymore:** its real docs were
never a git repo at all — they're a live Mintlify site at help.getzep.com
with no version history to pin against. `docs/vendor/graphiti-core/` holds
the pinned-version README + LICENSE from `getzep/graphiti@v0.28.2` AND a
full 322-page crawl of help.getzep.com (fetched via each page's `.md`
variant, a documented Mintlify convention — see `MANIFEST.yaml`'s
`help_site_crawl` block), covering the temporal-knowledge-graph concepts,
architecture, and API reference that the bare repo doesn't have. It
necessarily covers Zep-the-hosted-product broadly rather than exclusively
graphiti-core-the-library; kept unfiltered per the no-gaps policy.
