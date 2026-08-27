# Plan — outage equivalence slice 6: read-path bounded retries

_Plan written 2026-07-31. The smallest slice, last on purpose: a live turn
is not rewindable (doctrine boundary), so all a read can do is try briefly
and then degrade honestly — this slice just makes "briefly" real._

## Shape

One helper in `runtime/tools.py` (where every read tool and `_read_failed`
already live):

```python
async def _read_with_retry(op, *, attempts=2, delay=0.5):
    """One bounded retry for a TRANSIENT read failure — a blip should not
    degrade a turn that a half-second's patience would have saved. One
    retry only: a real outage must degrade fast (the run is live and the
    operator may be watching); the UNIT-level machinery (slices 2-5) is
    what waits outages out, never a live turn."""
```

- On first failure: `classify_failure` — SEMANTIC returns the failure
  immediately (today's `_read_failed` text); TRANSIENT sleeps `delay` and
  tries once more; second failure degrades exactly as today, with the
  existing honest text (plus `after 2 attempts`).
- Applied to the read tools that call out: jira reads (3), graph read,
  litellm reads, `read_team_activity` if it calls out (verify — if it reads
  only the spine DB, leave it; the spine being down fails the whole run and
  is not a "read degradation").
- The consult sub-run and Executor writes are NOT touched — consults degrade
  at the caller (slice-1 label already rides), writes are slice 3's.

## Explicitly bounded

- `attempts=2`, `delay=0.5s`, constants — no config knob until a reason
  exists (scope-the-ask).
- Total added worst-case latency per read: ~0.5s + one call. A live turn
  stays live.

## Tests

1. Transient-then-success: one retry, result returned, no degradation text.
2. Transient-twice: degrades with the honest text; exactly 2 attempts (spy).
3. Semantic: NO retry (spy asserts 1 call), today's text.
4. The sleep is patched in tests (no real waiting).

## Deployment

Code-only; restart `cc-uvicorn`. STATUS/JOURNAL: closes the six-slice arc —
include the arc summary line. Commit + push.
