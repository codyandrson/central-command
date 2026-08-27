"""usage_snapshot() wire-shape test.

Measured live on the real proxy (2026-08-21): /user/daily/activity paginates
the underlying raw rows (per key×model×date), not whole days, so one DATE can
straddle two pages, and the response `metadata` totals disagree with summed
rows. usage_snapshot() must page-walk and merge by date across pages, never
trust metadata totals for anything but "keep going / stop".
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from central_command.config import settings
from central_command.integrations import litellm as litellm_client


def _model_bucket(model, spend, prompt, completion, requests):
    return {
        model: {
            "metrics": {
                "spend": spend,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "api_requests": requests,
            }
        }
    }


class _FakeResp:
    def __init__(self, body):
        self._body = body
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._body


async def test_usage_snapshot_walks_all_pages_and_merges_a_date_split_across_them(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    monkeypatch.setattr(settings, "llm_proxy_ui_url", "http://ui.test")

    pages = {
        1: {
            "results": [
                {
                    "date": "2026-08-20",
                    "breakdown": {"models": _model_bucket("cc-default", 1.0, 100, 50, 5)},
                },
            ],
            "metadata": {"total_pages": 2, "has_more": True},
        },
        2: {
            "results": [
                # Same date as page 1 — must be MERGED, never overwritten.
                {
                    "date": "2026-08-20",
                    "breakdown": {"models": _model_bucket("cc-default", 2.0, 200, 100, 10)},
                },
                {
                    "date": "2026-08-21",
                    "breakdown": {"models": _model_bucket("cc-other", 0.5, 10, 5, 1)},
                },
            ],
            "metadata": {"total_pages": 2, "has_more": False},
        },
    }

    async def fake_call(method, path, json_body=None):
        if path == "/global/spend":
            return _FakeResp({"spend": 42.0, "max_budget": 100.0})
        assert path.startswith("/user/daily/activity")
        page = int(parse_qs(urlparse(path).query)["page"][0])
        return _FakeResp(pages[page])

    monkeypatch.setattr(litellm_client, "_call", fake_call)

    out = await litellm_client.usage_snapshot(days=30)

    # /global/spend passes through unchanged.
    assert out["spend"] == 42.0
    assert out["max_budget"] == 100.0
    assert out["window_days"] == 30
    assert out["proxy_ui_url"] == "http://ui.test"

    daily = {d["date"]: d for d in out["daily"]}
    assert set(daily) == {"2026-08-20", "2026-08-21"}
    merged = daily["2026-08-20"]
    assert merged["spend"] == pytest.approx(3.0)
    assert merged["input"] == 300
    assert merged["output"] == 150
    assert merged["requests"] == 15

    assert out["window_spend"] == pytest.approx(3.5)

    by_model = {m["model"]: m for m in out["models"]}
    assert by_model["cc-default"]["cost"] == pytest.approx(3.0)
    assert by_model["cc-default"]["requests"] == 15
    assert by_model["cc-other"]["cost"] == pytest.approx(0.5)


async def test_usage_snapshot_stops_at_a_single_page_when_the_proxy_does_not_paginate(monkeypatch):
    """No has_more/total_pages at all (older proxy behaviour) must not loop forever."""
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    monkeypatch.setattr(settings, "llm_proxy_ui_url", "")
    calls = []

    async def fake_call(method, path, json_body=None):
        if path == "/global/spend":
            return _FakeResp({"spend": 0.0, "max_budget": None})
        calls.append(path)
        return _FakeResp(
            {
                "results": [
                    {
                        "date": "2026-08-20",
                        "breakdown": {"models": _model_bucket("cc-default", 1.0, 1, 1, 1)},
                    }
                ],
                "metadata": {},
            }
        )

    monkeypatch.setattr(litellm_client, "_call", fake_call)

    out = await litellm_client.usage_snapshot(days=7)

    assert len(calls) == 1
    assert out["proxy_ui_url"] is None
    assert out["window_spend"] == pytest.approx(1.0)
