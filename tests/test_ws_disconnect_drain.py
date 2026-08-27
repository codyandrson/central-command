"""A cockpit disconnect must not cancel in-flight RPCs.

The browser reaches the gateway through nerve's 1:1 ws-proxy, so every
client-side blip (laptop sleep, tailscale path change, page reload) closes
the gateway socket. Cancelling in-flight tasks on that close killed a
`proposals.reject` mid-`agent.run` (2026-08-13): the proposal was already
REJECTED but the drafter was never resumed, and the CancelledError bypassed
every `except Exception` — no log line, no session.resume_failed, the
session stuck AWAITING_HUMAN forever. Disconnect must let RPCs drain.
"""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from central_command.api import nerve_gateway


@pytest.fixture
def ws_app():
    app = FastAPI()
    app.include_router(nerve_gateway.router)
    return app


def test_disconnect_lets_inflight_rpc_drain(ws_app, monkeypatch):
    state = {"started": False, "finished": False, "cancelled": False}

    async def slow_dispatch(method, params, notify):
        state["started"] = True
        try:
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        state["finished"] = True
        return {}

    monkeypatch.setattr(nerve_gateway, "_dispatch", slow_dispatch)

    with TestClient(ws_app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connect.challenge
            ws.send_json({"type": "req", "id": "1", "method": "anything", "params": {}})
            deadline = time.monotonic() + 2
            while not state["started"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert state["started"]
        # Socket is closed with the RPC still in flight; the task must drain.
        deadline = time.monotonic() + 2
        while not (state["finished"] or state["cancelled"]) and time.monotonic() < deadline:
            time.sleep(0.01)

    assert not state["cancelled"], "disconnect cancelled an in-flight RPC"
    assert state["finished"], "in-flight RPC never completed after disconnect"
