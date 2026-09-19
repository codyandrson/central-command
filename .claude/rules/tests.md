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
