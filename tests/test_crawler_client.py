"""The cc-crawler CLIENT SEAM only (central_command/integrations/crawler.py).

The service itself cannot run here: it needs Playwright/Chromium, which is why
it lives in a container on the chromebox. What is testable offline — and what
actually bites — is the seam: does an unreachable crawler degrade instead of
raising, and does the request carry the caps the server expects.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.integrations import crawler


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def captured(monkeypatch):
    """Patch httpx.AsyncClient so every call is served by a recorded handler."""
    box: dict = {}
    real = httpx.AsyncClient

    def make(response_factory):
        def _factory(*args, **kwargs):
            def handler(request: httpx.Request) -> httpx.Response:
                box["request"] = request
                return response_factory(request)

            kwargs["transport"] = _mock_transport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)
        return box

    return make


async def test_crawl_site_parses_the_response(captured):
    captured(
        lambda req: httpx.Response(
            200,
            json={
                "pages": [{"url": "https://x/a", "title": "A", "markdown": "# A"}],
                "truncated": True,
                "errors": [{"url": "https://x/b", "error": "timeout"}],
            },
        )
    )
    out = await crawler.crawl_site("https://x", path_prefix="/docs")
    assert out["degraded"] == ""
    assert out["truncated"] is True
    assert out["pages"][0]["markdown"] == "# A"
    assert out["errors"][0]["url"] == "https://x/b"


async def test_crawl_site_caps_max_pages_in_the_request(captured):
    box = captured(lambda req: httpx.Response(200, json={"pages": [], "truncated": False}))
    await crawler.crawl_site("https://x", max_pages=5000)
    import json

    body = json.loads(box["request"].content)
    assert body["max_pages"] == crawler.MAX_PAGES_CEILING == 200
    assert box["request"].url.path == "/crawl"


async def test_filter_mode_defaults_to_pruning_and_passes_through(captured):
    import json

    box = captured(lambda req: httpx.Response(200, json={"pages": [], "truncated": False}))
    await crawler.crawl_site("https://x")
    assert json.loads(box["request"].content)["filter"] == "pruning"

    await crawler.crawl_site("https://x", filter_mode="llm")
    assert json.loads(box["request"].content)["filter"] == "llm"

    captured(lambda req: httpx.Response(200, json={"url": "https://x"}))
    await crawler.fetch_rendered("https://x", filter_mode="llm")
    assert json.loads(box["request"].content)["filter"] == "llm"


async def test_llm_fallback_warning_reaches_the_caller(captured):
    captured(
        lambda req: httpx.Response(
            200,
            json={"pages": [], "truncated": False, "warnings": ["used pruning instead"]},
        )
    )
    out = await crawler.crawl_site("https://x", filter_mode="llm")
    assert out["degraded"] == ""  # a fallback is not a failure
    assert out["warnings"] == ["used pruning instead"]


async def test_connect_failure_degrades_and_never_raises(captured):
    def boom(req):
        raise httpx.ConnectError("connection refused", request=req)

    captured(boom)
    out = await crawler.crawl_site("https://x")
    assert out["pages"] == []
    assert "crawler unavailable" in out["degraded"]
    assert "chromebox" in out["degraded"]

    page = await crawler.fetch_rendered("https://x")
    assert page["markdown"] == ""
    assert "crawler unavailable" in page["degraded"]


async def test_fetch_rendered_surfaces_a_render_error_as_degraded(captured):
    captured(
        lambda req: httpx.Response(
            200, json={"url": "https://x", "title": "", "markdown": "", "error": "nav timeout"}
        )
    )
    page = await crawler.fetch_rendered("https://x")
    assert page["degraded"] == "nav timeout"
