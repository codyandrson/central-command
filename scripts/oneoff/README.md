# Spent one-off scripts

These ran once, did their job, and are kept only as the record of *how* a
migration was performed. **Nothing here is part of a normal workflow** — the
scripts you actually run live one directory up (`m2_spike.py`, the durable
pause/resume proof, and `m5_acceptance.py`, the live acceptance run, both
documented in `CLAUDE.md`).

Moved down here 2026-07-29 so `scripts/` stops mixing living tools with
finished errands. Paths inside them are relative to the repo root and were
correct when they ran; re-running one would need that checked first.

| script | what it did | why it is spent |
|---|---|---|
| `extend_lib_jira.py` | Extended the n8n `lib-jira` provider workflow with additional operations. | **D23** moved Jira to a native client (`central_command/integrations/jira.py`). n8n keeps only the email façade, where it already solved a hard OAuth problem. |
| `reembed_graph.py` | Re-embedded the Neo4j graph onto a single embedding model after the Graphiti move. | One-time convergence. It ran, `--verify` passed, and the graph now carries exactly one model. |
| `replay_cancelled_reject_resume.py` | Replayed the resume half of one reject (prop_075d96b53965) whose RPC the 2026-08-13 WS drop cancelled mid-flight, un-sticking sess_10e406047961. | The decision half had committed; only that one session was owed its resume. The general fix is `resume_park.arm()` before every decision resume. |
| `arm_orphaned_resumes_2026_08_15.py` | Armed park records for the 12 resumes SIGKILLed mid-batch on 2026-08-15, so `resume_sweep` could drive them. | Those twelve predate the arm-before-run fix and carried no marker; every later resume writes its own. |
| `recommit_lost_private_episodes.py` | Re-delivered the private-scope graph episodes silently dropped 2026-08-01→08-15 (colon group ids rejected AFTER ack), from their EXECUTED proposal rows. | `private_group()` builds valid ids now; the surviving episode bodies were re-committed once. |
| `clear_and_requeue_inbox_2026_08_16.py` | Cleared the 27-item Decisions Inbox and requeued the emails behind it after the 2026-08-15 graph fixes. | the operator's call: those items were triaged against a graph that was destroying true facts; a fresh pass was worth more than the stale conclusions. |
