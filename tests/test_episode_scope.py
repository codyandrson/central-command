"""`graph.add_episode` refuses a scope-less write.

Scope is the operator-reviewed routing decision on the proposal (D11-r1); a
default here would land an "agent-scoped" episode in the shared team graph
silently. Observed 2026-08-27: a redraft claimed EA-scoping via an entity NAME
and carried no `scope` argument at all.
"""

import pytest

from central_command.gateway.executor import ExecutorError, _graph_add_episode


async def test_add_episode_requires_explicit_scope():
    with pytest.raises(ExecutorError, match="no `scope` given"):
        await _graph_add_episode(
            {"name": "x", "episode_body": "y"}, approver="lee", proposer="ea"
        )
