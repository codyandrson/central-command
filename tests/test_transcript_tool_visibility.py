"""Phase 1 tool-call visibility: a tool result must carry its call's identity.

CLAUDE.md's rule for this seam: "the cockpit hand-declares its own interfaces
over untyped RPC payloads, so a field the gateway never sends is invisible to
tsc AND to every frontend test." A frontend test that builds its own payload
proves nothing about what `chat.history` actually emits — this test drives
`_transcript_to_chat_messages` on a synthetic `run_state` (the same shape
`durable.full_messages` reads) and asserts the wire dict a `tool-return` part
produces carries `toolCallId`/`toolName`, so the frontend can pair it back to
its call.
"""

from __future__ import annotations

from central_command.api.nerve_gateway import _transcript_to_chat_messages


def _run_state_with_tool_call_and_return() -> dict:
    return {
        "messages": [
            {
                "kind": "response",
                "timestamp": "2026-08-17T09:00:00Z",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_call_id": "call_123",
                        "tool_name": "jira_search",
                        "args": {"jql": "project = T1"},
                    },
                ],
            },
            {
                "kind": "request",
                "parts": [
                    {
                        "part_kind": "tool-return",
                        "tool_call_id": "call_123",
                        "tool_name": "jira_search",
                        "content": "3 issues found",
                        "timestamp": "2026-08-17T09:00:01Z",
                    },
                ],
            },
        ],
    }


def test_a_tool_return_carries_its_call_id_and_name():
    out = _transcript_to_chat_messages(_run_state_with_tool_call_and_return())

    tool_call_msg = next(m for m in out if m["role"] == "assistant")
    call_block = tool_call_msg["content"][0]
    assert call_block["type"] == "tool_use"
    assert call_block["id"] == "call_123"
    assert call_block["name"] == "jira_search"

    tool_result_msg = next(m for m in out if m["role"] == "toolResult")
    assert tool_result_msg["toolCallId"] == "call_123"
    assert tool_result_msg["toolName"] == "jira_search"
    assert tool_result_msg["content"] == "3 issues found"


def test_a_retry_prompt_also_carries_call_id_and_name():
    run_state = {
        "messages": [
            {
                "kind": "request",
                "parts": [
                    {
                        "part_kind": "retry-prompt",
                        "tool_call_id": "call_456",
                        "tool_name": "propose_dismiss",
                        "content": "validation error: missing field",
                        "timestamp": "2026-08-17T09:05:00Z",
                    },
                ],
            },
        ],
    }
    out = _transcript_to_chat_messages(run_state)
    assert out[0]["toolCallId"] == "call_456"
    assert out[0]["toolName"] == "propose_dismiss"
