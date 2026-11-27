"""Request shapes the tools send, checked against wardrowbe 1.14.0's field names.

The backend ignores unknown request fields, so a wrong name fails silently;
these tests pin the names it actually reads.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from mcp.server.fastmcp.exceptions import ToolError

from wardrowbe_mcp.client import WardrowbeClient
from wardrowbe_mcp.server import build_mcp_server


class _Provider:
    async def async_get_sync_payload(self) -> dict[str, Any]:
        return {"external_id": "test", "display_name": "test"}


def _call(name: str, args: dict[str, Any], outfits: list[dict[str, Any]] | None = None):
    """Run one tool against a stub backend; return (tool result, recorded requests)."""
    seen: list[tuple[str, str, dict[str, str], Any]] = []

    async def handler(req: web.Request) -> web.Response:
        body = await req.text()
        if req.path.endswith("/auth/sync"):
            return web.json_response({"access_token": "jwt"})
        seen.append((req.method, req.path, dict(req.query), json.loads(body) if body else None))
        if req.path == "/api/v1/outfits":
            return web.json_response({"outfits": outfits or []})
        return web.json_response({})

    async def run():
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                client = WardrowbeClient(session, f"http://127.0.0.1:{port}", _Provider())
                _, result = await build_mcp_server(client).call_tool(name, args)
                return result
        finally:
            await runner.cleanup()

    return asyncio.run(run()), seen


def test_health_uses_api_prefix():
    result, seen = _call("health", {})
    assert result == {"healthy": True}
    assert seen[0][1] == "/api/v1/health"


def test_log_wear_sends_worn_at():
    _, seen = _call("log_wear", {"item_id": "i1", "date": "2026-11-20"})
    assert seen == [("POST", "/api/v1/items/i1/wear", {}, {"worn_at": "2026-11-20"})]


def test_list_items_category_sends_type():
    _, seen = _call("list_items", {"category": "shirt"})
    assert seen[0][2]["type"] == "shirt"
    assert "category" not in seen[0][2]


def test_feedback_sends_worn_and_comment():
    _, seen = _call(
        "submit_outfit_feedback",
        {"outfit_id": "o1", "rating": 4, "wore": True, "notes": "nice"},
    )
    assert seen[0][3] == {"rating": 4, "worn": True, "comment": "nice"}


def test_recent_outfits_sends_page_size_and_status():
    _, seen = _call("get_recent_outfits", {"limit": 6, "status": "viewed"})
    assert seen[0][2] == {"page_size": "6", "status": "viewed"}


def test_suggest_outfit_payload():
    _, seen = _call(
        "suggest_outfit",
        {
            "occasion": "casual",
            "weather_override": {"temperature": 5, "humidity": 80},
            "include_items": ["i1"],
            "exclude_items": ["i2"],
        },
    )
    assert seen[0][3] == {
        "occasion": "casual",
        "weather_override": {"temperature": 5.0, "humidity": 80},
        "include_items": ["i1"],
        "exclude_items": ["i2"],
    }


def test_suggest_outfit_rejects_weather_without_temperature():
    with pytest.raises(ToolError):
        _call("suggest_outfit", {"weather_override": {"humidity": 80}})


def test_accept_latest_acts_on_viewed_outfit():
    result, seen = _call(
        "accept_latest_outfit", {}, outfits=[{"id": "o1", "status": "viewed"}]
    )
    assert result["outfit_id"] == "o1"
    assert seen[-1][:2] == ("POST", "/api/v1/outfits/o1/accept")
