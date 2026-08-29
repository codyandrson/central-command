# Tier-3 Discussion Stall Escalation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A task blocked behind a clarification discussion no longer sits there silently — the sweep flags it at you (S1) or nudges the agent (S2), and concluding a discussion visibly closes it.

**Architecture:** One pure classifier + one heartbeat action for detection; one shared close seam so every close pushes the frame the cockpit listens for; two small cockpit changes.

**Spec:** `docs/superpowers/specs/2026-07-27-discussion-stall-escalation-design.md` — read it first.

## Global Constraints

- Work in `/home/codyslab/central-command` on the Pi, on `master`. Run tests with `source .venv/bin/activate`.
- **Assert on rows your test created, never global counts** — the dev DB is shared.
- **Pin `demo_mode=True`** in any test that reaches code calling `resolve_model()`, or the offline suite calls Claude for real.
- `runtime/` may never import `gateway/`. (`runtime/questions.py` already reaches `api.orchestration` late — that is allowed and not the banned edge.)
- Emit an event **before** the thing it authorises; a record that authorises nothing may follow its write.
- Schema changes are additive `alter table ... add column if not exists`, and must seed nothing.
- **`schema.sql` is auto-loaded on a FRESH database only — nothing applies it at runtime.** Task 1's three columns have already been applied by hand to the live `cc-postgres`, so DB-backed tests and live writes will find them. If you add another column, apply it the same way in the same task or the next task fails with an undefined-column error that looks like a code bug.
- **Pass timezone-AWARE datetimes** to the classifier (`datetime.now(timezone.utc)`, never `datetime.utcnow()`). asyncpg returns aware datetimes for `timestamptz`; mixing naive and aware raises `TypeError`.
- Don't commit `.env`. Push to `origin master` when the task's commit lands.

## File Structure

| File | Change |
|---|---|
| `central_command/db/schema.sql` | Add 3 nullable columns |
| `central_command/runtime/discussions.py` | **New** — the pure classifier |
| `central_command/db/repo.py` | Query for open discussions; stamp setters; `closed_reason` |
| `central_command/heartbeat/actions.py` | New `discussion.sweep` action + registry entry |
| `central_command/runtime/converse.py` | Shared close seam; `why_not_sendable` lock rule |
| `central_command/runtime/questions.py` | `conclude_discussion` closes via the seam |
| `central_command/config.py` | `discussion_stall_hours: int = 24` |
| `web/src/features/chat/ChatPanel.tsx` | Terminal divider |
| `web/src/features/sessions/SessionList.tsx` | Amber "blocks \<task\>" chip |
| `tests/test_discussion_stall.py` | **New** — classifier + sweep + close tests |

---

### Task 1: Schema and the pure classifier

**Files:** Create `central_command/runtime/discussions.py`, `tests/test_discussion_stall.py`; modify `central_command/db/schema.sql`, `central_command/config.py`.

**Interfaces:**
- Produces: `classify_discussion_stall(*, item: dict, session: dict, last_turn_by: str, now: datetime, stall_hours: int) -> str | None` returning `None | "S1" | "S2"`. Tasks 2 depends on this exact signature.
- Produces: `settings.discussion_stall_hours` (int, default 24).

- [ ] **Step 1: Add the columns to `schema.sql`**

Append near the end of the file, following the existing `alter table ... if not exists` style:

```sql
-- Tier-3 stall escalation (2026-07-27). Current state, not history: each stamp
-- says "this lane was already flagged during THIS quiet window". The sweep
-- re-flags when the stamp is older than the session's updated_at, so a
-- discussion that goes quiet, moves, then goes quiet again is not silently
-- ignored forever.
alter table operator_item add column if not exists stalled_at timestamptz;
alter table operator_item add column if not exists nudged_at  timestamptz;
-- Why a conversation closed: 'operator' (closed from the cockpit) or
-- 'concluded' (the agent ended its own clarification lane). Drives the lock
-- rule in why_not_sendable, the terminal divider text, and the event payload.
alter table session add column if not exists closed_reason text;
```

- [ ] **Step 2: Add the config knob**

In `central_command/config.py`, beside the other Phase-4 settings:

```python
    # Tier-3: how long a clarification discussion may sit quiet before the
    # sweep flags it (S1, at the operator) or nudges the agent (S2).
    discussion_stall_hours: int = 24
```

- [ ] **Step 3: Write the failing classifier tests**

Create `tests/test_discussion_stall.py`:

```python
"""The tier-3 stall classifier is pure, so it is tested without a database."""
from datetime import datetime, timedelta, timezone

from central_command.runtime.discussions import classify_discussion_stall

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _case(*, quiet_hours=48, last_turn_by="agent", stalled_at=None, nudged_at=None):
    moved = NOW - timedelta(hours=quiet_hours)
    return {
        "item": {"id": "item_x", "status": "OPEN", "stalled_at": stalled_at,
                 "nudged_at": nudged_at},
        "session": {"id": "sess_x", "updated_at": moved},
        "last_turn_by": last_turn_by,
        "now": NOW,
        "stall_hours": 24,
    }


def test_not_quiet_long_enough_is_not_a_stall():
    assert classify_discussion_stall(**_case(quiet_hours=2)) is None


def test_agent_spoke_last_is_s1_waiting_on_the_operator():
    assert classify_discussion_stall(**_case(last_turn_by="agent")) == "S1"


def test_operator_spoke_last_is_s2_waiting_on_the_agent():
    assert classify_discussion_stall(**_case(last_turn_by="operator")) == "S2"


def test_already_flagged_in_this_quiet_window_is_not_reflagged():
    """Idempotence: the sweep runs every tick and must flag once per window."""
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="agent", stalled_at=moved + timedelta(minutes=1))
    ) is None


def test_flagged_before_the_session_last_moved_is_flagged_again():
    """Quiet -> movement -> quiet again is a NEW stall, not one already handled."""
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="agent", stalled_at=moved - timedelta(days=3))
    ) == "S1"


def test_s2_idempotence_uses_the_nudge_stamp_not_the_stall_stamp():
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="operator", nudged_at=moved + timedelta(minutes=1))
    ) is None
    assert classify_discussion_stall(
        **_case(last_turn_by="operator", stalled_at=moved + timedelta(minutes=1))
    ) == "S2"


def test_an_answered_item_is_never_a_stall():
    case = _case(last_turn_by="agent")
    case["item"]["status"] = "ANSWERED"
    assert classify_discussion_stall(**case) is None
```

- [ ] **Step 4: Run them and watch them fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py -v`
Expected: collection error — `No module named 'central_command.runtime.discussions'`.

- [ ] **Step 5: Write the classifier**

Create `central_command/runtime/discussions.py`. Module docstring must explain *why the test is who spoke last* (an agent that answers "I still need X" after a nudge is correctly waiting on the operator, and a "has the operator ever spoken" test would nudge it forever). Implement exactly the signature in **Interfaces**, keeping it pure — no imports from `db`, no I/O.

Logic: return `None` unless `item["status"] == "OPEN"`; return `None` unless the session has been quiet for at least `stall_hours`; pick `S1` when `last_turn_by == "agent"` else `S2`; then return `None` if the relevant stamp (`stalled_at` for S1, `nudged_at` for S2) is non-null **and** not older than `session["updated_at"]`.

- [ ] **Step 6: Run the tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add central_command/runtime/discussions.py central_command/db/schema.sql central_command/config.py tests/test_discussion_stall.py
git commit -m "Classify a stalled clarification discussion by who spoke last

Not by 'has the operator ever replied': after a nudge an agent may answer that
it still needs something, which puts the ball back in the operator's court. The
weaker test would nudge that agent forever."
git push origin master
```

---

### Task 2: The `discussion.sweep` heartbeat action

**Files:** modify `central_command/db/repo.py`, `central_command/heartbeat/actions.py`, `central_command/db/schema.sql` (seed row), `tests/test_discussion_stall.py`.

**Interfaces:**
- Consumes: `classify_discussion_stall` from Task 1.
- Produces: `ACTIONS["discussion.sweep"]`; repo helpers `list_open_discussions()`, `mark_discussion_stalled(item_id)`, `mark_discussion_nudged(item_id)`.

- [ ] **Step 1: Add the repo helpers**

In `central_command/db/repo.py`, following the existing async/pool style:

- `list_open_discussions() -> list[dict]` — join `operator_item` to `session` on `discussion_session_id`, where `operator_item.status = 'OPEN'` and `discussion_session_id is not null`. Return item fields plus `session_updated_at`, `session_status`, and the session `run_state` (needed to read the last turn).
- `mark_discussion_stalled(item_id)` / `mark_discussion_nudged(item_id)` — set the respective stamp to `now()`.

- [ ] **Step 2: Write the failing sweep tests**

Append to `tests/test_discussion_stall.py`. These need a real Postgres; skip without one exactly the way `tests/test_ledger.py` does, and **scope every assertion to rows the test created**.

Cover: (a) a fresh discussion is not swept; (b) an S1 discussion emits exactly one `discussion.stalled` event carrying `task_id`, and running the sweep twice still emits one; (c) an S2 discussion calls the nudge path once; (d) **a sweep that finds nothing emits no event at all** — the quiet rule.

For (c), monkeypatch the nudge so no agent actually runs.

- [ ] **Step 3: Run them and watch them fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py -v`
Expected: failures on the missing `_discussion_sweep` / registry entry.

- [ ] **Step 4: Implement the action**

In `central_command/heartbeat/actions.py` add `async def _discussion_sweep(schedule_id, params) -> dict`:

- Read `settings.discussion_stall_hours`.
- For each row from `list_open_discussions()`, determine `last_turn_by` from the session run state (`durable.simplify_messages`: the last turn whose `kind == "user-prompt"` is the operator's, anything later is the agent's) and call the classifier.
- **S1:** emit `discussion.stalled` with `ref_id=item["id"]` and payload `{agent_id, task_id, discussion_session_id, waiting_hours}`, then `mark_discussion_stalled`.
- **S2:** call the nudge (below), then `mark_discussion_nudged`.
- Return `{"checked": n, "stalled": [...], "nudged": [...]}`.

The nudge is one ordinary turn into the discussion session via `converse.conversation_turn`, with the wording from the spec naming the blocked task. It must be a normal turn so it lands in the transcript.

Register it:

```python
        ActionSpec(
            kind="discussion.sweep",
            description="Flag clarification discussions gone quiet; nudge the agent when it owes a conclusion.",
            params={},
            required=(),
            levers=("runtime.converse.conversation_turn",),
            run=_discussion_sweep,
            # Quiet ONLY when it found nothing: a sweep with no stalled lane
            # changed nothing and each flag emits its own discussion.stalled.
            # A nudge starts an agent turn, so it is always material.
            material=lambda r: bool(r.get("stalled") or r.get("nudged")),
        ),
```

`tests/test_heartbeat.py` parity-tests which actions may be quiet — update that allowlist in the same commit, since `discussion.sweep` is now quiet-eligible.

- [ ] **Step 5: Seed the schedule, DISABLED**

Add a seed row to `schema.sql` beside the existing founding schedules, `enabled = false` like every other, running hourly. Never born enabled.

- [ ] **Step 6: Run the tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py tests/test_heartbeat.py -v`
Expected: all pass.

- [ ] **Step 7: Commit and push**

```bash
git add -A
git commit -m "discussion.sweep: flag a quiet clarification lane, nudge the agent that owes a conclusion

Quiet only when it finds nothing; a nudge starts an agent turn, so it is always
material. Seeded disabled like every heartbeat schedule."
git push origin master
```

---

### Task 3: One close seam, and concluded discussions are read-only

This fixes a live bug: `conclude_discussion` marks the session `DONE` without emitting `conversation.ended`, so the cockpit keeps a stale open row.

**Files:** modify `central_command/runtime/converse.py`, `central_command/runtime/questions.py`, `central_command/db/repo.py`, `tests/test_discussion_stall.py`.

**Interfaces:**
- Produces: `converse.close_session(session_id, *, agent_id, reason, actor) -> None` — writes `closed_reason`, marks `DONE`, emits `conversation.ended`. Used by both close paths.
- Produces: a new `why_not_sendable` code, `discussion_concluded`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discussion_stall.py`:

- `test_concluding_a_discussion_emits_conversation_ended` — the regression test for the bug; it must fail on today's code.
- `test_conclude_records_closed_reason_concluded`.
- `test_a_concluded_discussion_is_read_only` — `why_not_sendable({... "status": "DONE", "closed_reason": "concluded", "mode": "conversation"})` returns code `discussion_concluded`.
- `test_an_ordinary_closed_conversation_is_still_writable` — same but `closed_reason` `operator`/`None` returns `None`. **This guards the existing deliberate behaviour** ("a closed conversation can be reopened by simply writing to it") — only concluded discussions lock.

- [ ] **Step 2: Run them and watch them fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py -v -k "conclude or read_only or writable"`
Expected: failures — no `conversation.ended` emitted, no `closed_reason`, no lock rule.

- [ ] **Step 3: Add the shared close seam**

In `converse.py`, add `close_session(...)` doing: `repo.mark_session_closed(session_id, reason)` (new repo helper writing `closed_reason` **and** status `DONE`), then `events.emit("conversation.ended", ref_id=session_id, payload={"agent_id": ..., "reason": reason}, actor=actor)`.

Rewrite `end_conversation` to keep **all** its existing guards and then delegate to `close_session(..., reason="operator", actor="operator")`. Do not weaken a guard.

- [ ] **Step 4: Route `conclude_discussion` through it**

In `questions.py`, replace `await repo.mark_session_done(discussion_session_id)` with `close_session(..., reason="concluded", actor=f"agent:{agent_id}")`.

Add a comment saying why the guards deliberately do not apply: the agent is closing its own lane mid-turn, and `end_conversation`'s `turn_in_progress` guard exists to stop the *operator* racing a turn.

**Also fix this file's module docstring in the same commit.** It still claims tier 3 "is designed and NOT built. It is deliberately absent from the `ask_operator` tool" — false since 2026-07-25 (`park_question` handles `needs_discussion`, `conclude_discussion` exists, `packs.py` grants the tool). Leaving it is precisely the failure the paragraph itself warns about.

- [ ] **Step 5: Add the lock rule**

In `why_not_sendable`, **before** the existing `status not in ("AWAITING_OPERATOR", "DONE")` check, add the concluded case returning `("discussion_concluded", "discussion <id> concluded and its task resumed — start a new conversation with the agent instead")`. Leave the existing DONE-is-writable comment and behaviour intact for every other closed conversation.

- [ ] **Step 6: Run the tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_discussion_stall.py tests/test_converse.py -v`
Expected: all pass. If `tests/test_converse.py` does not exist, run the full suite instead.

- [ ] **Step 7: Commit and push**

```bash
# Stage ONLY your files -- another session is working in this tree.
git add central_command/runtime/converse.py central_command/runtime/questions.py central_command/db/repo.py tests/test_discussion_stall.py
git commit -m "One close seam: every close pushes the frame the cockpit listens for

conclude_discussion marked the session DONE and emitted nothing, so the panel
kept a stale open row -- the documented 'push the frames or the UI lies'
failure, in a close path added after that rule was written. It cannot call
end_conversation (that guard rejects a mid-turn close, and concluding IS
mid-turn), so both paths now share close_session.

A concluded discussion is read-only; ordinary closed conversations stay
writable, which is deliberate and now guarded."
git push origin master
```

---

### Task 4: The cockpit tells the truth

**Files:** modify `web/src/features/chat/ChatPanel.tsx`, `web/src/features/sessions/SessionList.tsx`.

**Interfaces:** consumes `closed_reason` on the session payload and the `discussion.stalled` event.

- [ ] **Step 1: Terminal divider in the chat window**

When the open session is terminal, render a divider at the end of the transcript — `Discussion concluded — task resumed` for `closed_reason === 'concluded'`, `Conversation closed` otherwise. Match existing panel styling; do not invent a new design language.

**The composer is already handled — do not redo it.** This plan originally claimed it needed no work "because it reads `why_not_sendable`". That was FALSE: `nerve_gateway._COMPOSER_DISABLING` is an explicit ALLOWLIST of codes, so a code missing from it renders an enabled box that `_chat_send` then 409s — the very composer/send drift `_send_target` was written to end. Task 3 added `discussion_concluded` to that tuple with a guard test. Verify it, don't rebuild it.

- [ ] **Step 2: Amber chip on a stalled discussion row**

In `SessionList.tsx`, a session row that is a stalled discussion shows an amber chip reading `blocks <task title> · waiting <n>d`. Follow the existing "name the task you block" pattern used for inbox questions.

**Respect the no-displacement rule** (memory: new content auto-loads but never moves what the operator is viewing) — the chip must not reflow or reorder rows.

- [ ] **Step 3: Typecheck and test**

Run: `cd web && npx tsc --noEmit && npm test -- --run 2>&1 | tail -20`
Expected: tsc clean. The web suite has a **documented 22-failure baseline** — compare against it and report the number; do not treat pre-existing failures as yours, and do not "fix" them.

- [ ] **Step 4: Build and commit**

```bash
cd web && npm run build && cd ..
# Stage ONLY your files -- another session is working in this tree.
git add web/src/features/chat/ChatPanel.tsx web/src/features/sessions/SessionList.tsx
git commit -m "The cockpit shows a discussion ending, and one going quiet

A terminal divider so a concluded chat does not leave you guessing, and an amber
'blocks <task>' chip so a lane that has been quiet for days reads differently
from one quiet for five minutes."
git push origin master
```

---

### Task 5: Verify end to end and record it

- [ ] **Step 1: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q 2>&1 | tail -5`
Expected: the documented baseline (431 before this work) plus the new tests, 0 failures. Report actual numbers.

- [ ] **Step 2: Deploy and smoke**

```bash
sudo systemctl restart cc-uvicorn && sleep 8
systemctl is-active cc-uvicorn
curl -s --max-time 10 -o /dev/null -w 'api:%{http_code}\n' http://localhost:8080/api/dispatch
bash deploy/pi/verify.sh 2>&1 | tail -3
```

- [ ] **Step 3: Confirm the schedule is present and DISABLED**

Query `heartbeat_schedule` for `discussion.sweep` and confirm `enabled = false`. It must not have started by surprise.

- [ ] **Step 4: STATUS.md and push**

Add a short section describing what shipped and mark NEXT SESSION item 13 done. Note the one thing left to the operator: enabling the schedule, which is a deliberate operator action.

```bash
git add docs/STATUS.md && git commit -m "Record tier-3 stall escalation" && git push origin master
git status -sb
```

## Self-Review

**Spec coverage:** S1 detect → Task 2; S2 nudge → Task 2; who-spoke-last classifier → Task 1; idempotence stamps → Tasks 1+2; quiet rule → Task 2; seed disabled → Tasks 2+5; close seam + `conversation.ended` → Task 3; `closed_reason` → Tasks 1+3; lock → Task 3; divider → Task 4; amber chip → Task 4; config knob → Task 1. No gaps.

**Placeholders:** none — every step names its file, command and expected result.

**Type consistency:** `classify_discussion_stall(item, session, last_turn_by, now, stall_hours)` is spelled identically in Task 1's tests, its implementation, and Task 2's caller. `closed_reason` values are exactly `"operator"` and `"concluded"` in schema, seam, lock rule and UI.
