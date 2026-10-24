"""The task board's list never caps OPEN tasks (2026-09-13): the limit is
for terminal rows only, so yesterday's REVIEW task is still on the board
after a day of churn."""
import uuid

from central_command.db import repo


async def test_open_tasks_survive_the_terminal_cap():
    agent_id = f"board-test-{uuid.uuid4().hex[:6]}"
    await repo.upsert_agent(agent_id, "Board test", "", "")
    old_open = f"task_board_open_{uuid.uuid4().hex[:8]}"
    await repo.create_task(old_open, "old open", "x", agent_id)
    terminal_ids = []
    for i in range(5):
        tid = f"task_board_done_{i}_{uuid.uuid4().hex[:6]}"
        await repo.create_task(tid, f"done {i}", "x", agent_id)
        await repo.resolve_task(tid, "DONE", outcome="ok")
        terminal_ids.append(tid)
    ids = [r["id"] for r in await repo.list_tasks(limit=2, agent_id=agent_id)]
    assert old_open in ids
    assert [i for i in ids if i in terminal_ids] == terminal_ids[-2:][::-1]  # newest two only


async def test_an_old_row_reads_as_unrefused_and_operator_created():
    """A row from before the backstops has no creator (NULL reads as operator
    in the cockpit) and no refusal mark."""
    agent_id = f"board-test-{uuid.uuid4().hex[:6]}"
    await repo.upsert_agent(agent_id, "Board test", "", "")
    tid = f"task_board_old_{uuid.uuid4().hex[:8]}"
    await repo.create_task(tid, "old", "x", agent_id)
    (row,) = [r for r in await repo.list_tasks(agent_id=agent_id) if r["id"] == tid]
    assert row["created_by"] is None and row["approved_by"] is None
    assert row["parent_task_id"] is None
    assert row["refusals"] is None
