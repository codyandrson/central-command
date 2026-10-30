---
paths:
  - "tests/**"
---

# Test-writing bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **Tests must pin `test_model=True` if they hit code that calls
  `resolve_model()` itself** (e.g. `routes.feed_email`) — with a real key in
  `.env` the "offline" suite will otherwise call the model for real and spend
  money. Pin it, or fake the model: a test that builds a live model and makes a
  request is stopped at the spend point. The session-scoped `no_live_model`
  guard in `tests/conftest.py` replaces `OpenAIChatModel.request`,
  `request_stream` and `_completions_create` with a refusal that says how to opt
  in, so a worktree's copy of the live key cannot be billed by an unpinned test
  (building a live model stays allowed; only the request is refused).
  `CC_LIVE_MODEL_TESTS=1` is the opt-in, read at call time, for a run that means
  to spend. Guarded by `tests/test_no_live_model.py`.
- **Any test whose approval executes uses `fake_world`**
  (`tests/fake_world.py`); there is no executor mode to pin. The session-scoped
  `no_live_world` guard in `tests/conftest.py` holds every write-capable
  integration at nothing and makes both subprocess seams refuse, because a
  worktree's `.env` is a copy of the live one and carries real credentials: an
  approved test proposal must fail loudly as unconfigured, never reach a real
  Jira, mail, calendar, LiteLLM or MCP write.
- **The queue is settled lazily; a test that plants rows over its own
  connection opts in to the eager settle.** The autouse `isolated_queue`
  fixture used to park foreign `UNPROCESSED` rows at the start of every test;
  it now does so on the first pool request to the test database (a one-shot
  hook on `repo._get_pool`), which is why the suite is faster. A test that opens
  its own `asyncpg.connect` and plants rows BEFORE its first pool request must
  carry `@eager_settle` (`pytest.mark.eager_settle`), or the late settle parks
  its rows too. The test database also runs with `synchronous_commit = off`.
  The failures of this change show only in the sequential `pytest -q` run, which
  stays the gate. Guarded by `tests/test_conftest_database.py`.
- **A real embedding is refused unless it is faked.** `no_live_graph_writes`
  replaces `neo4j_writer._embedding_client` with a refusal, so a test that
  reaches `request_embedding` must fake `request_embedding` (or a class standing
  in for `httpx.AsyncClient`); `CC_LIVE_GRAPH_TESTS=1` opts in. The curation
  writer posts to `<llm_base_url>/v1/embeddings` with the scoped key, and the
  worktree's `.env` carries a real one. Guarded by
  `tests/test_curation_embed_key.py`.
- **Assert on rows your test created, never on global counts.** The dev DB
  may be shared; `tests/conftest.py` parks foreign `UNPROCESSED` rows so
  queue-order tests are deterministic, but count assertions still need
  scoping.
- **The gate is `pytest -q`, and the per-test ceiling comes from
  `pyproject.toml`** (`timeout = 120`) — never from a remembered
  `--timeout=120` flag. The suite runs live knowledge-graph READS on purpose
  (a fake proves nothing about the SSE envelope), each one embeds through
  LiteLLM to an external backend, and with that backend off they do not fail:
  they HANG (2026-09-24: nine tests, and the testbed's "11-hour suite").
- **A live graph read is marked `@pytest.mark.graph_live`.** `conftest.py`
  probes the read path ONCE per session with a 10 s budget and skips every
  marked test when it does not answer. So `graph_live` skips in the summary
  mean **the graph backend was down** — not that those tests passed. A new test
  that reaches `graphiti.search_facts`/`get_episodes`
  for real needs the marker, or it becomes the next hang.
- **A test that runs the real installer is marked `@drives_installer` and
  calls `run_driver`** (v2.58.2; `tests/installer_source.py`). pytest-timeout
  on Windows has only the `thread` method, which ends the WHOLE session with
  `os._exit` and no summary when one test passes the suite's 120 s ceiling —
  the 2026-10-02 laptop run died at 79 % on a `setup.sh`-driving test. The
  marker gives those tests their own ceiling (`DRIVER_TEST_TIMEOUT`),
  `run_driver` kills the driver's process tree and fails the ONE test when an
  invocation exceeds `DRIVER_RUN_TIMEOUT`, and a conftest hook makes
  `run_driver` refuse an unmarked test. Stubs on `PATH` go through
  `with_stub_path`: Git for Windows' `bash.exe` launcher prepends
  `/mingw64/bin:/usr/bin` to the PATH it is given, so a stub `curl` beside
  the real `curl.exe` was shadowed; stub scripts and stub data are written
  LF (`write_lf`), because `write_text` writes CRLF on Windows and `read -r`
  then carries the CR into the comparison.
- **On Windows the stub directory goes FIRST, the harness supplies its own
  `python3`, and nothing with a space is run unquoted** (v2.58.2, measured on
  the laptop). Inside the driver PATH read `/mingw64/bin:/usr/bin:…:<stub>`:
  the stub was on PATH but behind Git's prefix, so `with_stub_path` must put
  it first, not merely ensure it is present. The only `python3` there is the
  Microsoft Store alias (exit 49), so `$PY` fell back to `uv run …` — which
  the harness stubs — and every `$PY -c` printed nothing: `python_shim` gives
  the temp tree a `python3` that runs the test's interpreter. A test that
  sets `PY` to an interpreter PATH breaks where that path has a space (the
  product runs `$PY` unquoted on purpose — it may be several words); use a
  wrapper function. A minimal environment for a bash started from Python
  needs `SYSTEMROOT`/`WINDIR`, or sockets fail with "Permission denied". The
  MSYS runtime re-globs arguments from a native parent (`${X:-y}` arrived as
  `$X:-y`): pass such text through the environment.
