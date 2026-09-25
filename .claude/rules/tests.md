---
paths:
  - "tests/**"
---

# Test-writing bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **Tests must pin `demo_mode=True` if they hit code that calls
  `resolve_model()` itself** (e.g. `routes.feed_email`) — with a real key in
  `.env` the "offline" suite will otherwise call the model for real and spend
  money.
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
  that reaches `graphiti.search_facts`/`search_private_facts`/`get_episodes`
  for real needs the marker, or it becomes the next hang.
