"""MCP tool definitions, ported from hacs-wardrowbe/llm_api.

Tools mirror the upstream HA integration's LLM API surface 1:1 where they
apply, plus a handful of read-only helpers (``list_items``, ``get_item``,
``get_outfit``) that the HA-side tools couldn't fit into the satellite-card
envelope.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import WardrowbeApiError, WardrowbeClient

# agentskills.io-format skill bundle shipped inside the package (via the
# `wardrowbe_mcp/skill` symlink pointing at the repo's canonical
# `mcp_server/skill/`). Registered as MCP resources at startup so
# compatible clients (e.g. ha-llm-conversation-agent 1.11+) auto-install
# it without a separate config entry.
_SKILL_DIR = Path(__file__).resolve().parent / "skill"
_SKILL_SLUG = "wardrowbe-skill"

# Conservative mime-type map — anything not listed falls back to text/plain.
# We don't ship binaries in the skill bundle.
_MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".py": "text/x-python",
    ".sh": "application/x-sh",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}

_LOGGER = logging.getLogger(__name__)

_VALID_OCCASIONS = (
    "beach", "brunch", "business-casual", "casual", "date", "dinner",
    "formal", "gym", "hiking", "interview", "lounge", "office", "outdoor",
    "party", "running", "smart-casual", "sport", "sporty", "travel",
    "wedding", "weekend", "work",
)
_VALID_TIME_OF_DAY = ("morning", "afternoon", "evening", "night", "full day")
_OUTFIT_STATUSES = ("pending", "sent", "accepted", "rejected", "skipped")
_ACTIONABLE_STATUSES = {"pending", "sent"}


def build_mcp_server(client: WardrowbeClient, name: str = "wardrowbe") -> FastMCP:
    """Build a FastMCP server bound to ``client``.

    The returned server has all Wardrowbe tools registered. Caller is
    responsible for choosing the transport (SSE / Streamable HTTP / stdio)
    and running it.
    """
    mcp = FastMCP(
        name,
        instructions=(
            "Tools for the user's Wardrowbe wardrobe. Prefer `suggest_outfit` "
            "when the user asks what to wear (occasion / time_of_day / notes "
            "are optional hints). Use `get_latest_outfit` to recall the most "
            "recent suggestion; `get_recent_outfits` to list as a gallery. "
            "Use `accept_latest_outfit` / `reject_latest_outfit` / "
            "`skip_latest_outfit` to act on a pending suggestion. Use "
            "`get_wardrobe_summary` for stats, `get_items_to_wash` to see "
            "what needs washing, `log_wash` to mark something washed. "
            "Always summarise visual results in 1–2 short sentences; do not "
            "echo image URLs."
        ),
        # SDK default allows only 127.0.0.1/localhost; reverse-proxy or
        # cross-container Host headers (e.g. `local-wardrowbe`) get 421.
        # BearerAuthMiddleware already gates every request.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    # ─── unauthenticated probes ──────────────────────────────────────────

    @mcp.tool()
    async def health() -> dict[str, Any]:
        """Return {"healthy": bool} from the Wardrowbe backend's /health probe."""
        return {"healthy": await client.async_health()}

    @mcp.tool()
    async def auth_config() -> dict[str, Any]:
        """Return Wardrowbe's auth configuration (dev_mode flag, OIDC settings)."""
        return await client.async_auth_config()

    # ─── identity ────────────────────────────────────────────────────────

    @mcp.tool()
    async def session_info() -> dict[str, Any]:
        """Return the current authenticated user record (whoami)."""
        return await _wrap(client.async_session_info())

    # ─── wardrobe / analytics ────────────────────────────────────────────

    @mcp.tool()
    async def get_wardrobe_summary() -> dict[str, Any]:
        """Return wardrobe stats (item counts, outfits this week/month, acceptance %, avg rating, total wears)."""
        analytics = await _wrap(client.async_analytics())
        wardrobe = analytics.get("wardrobe") if isinstance(analytics, dict) else None
        return {"wardrobe": wardrobe or {}, "raw": analytics}

    @mcp.tool()
    async def get_most_worn_items(limit: int = 5) -> dict[str, Any]:
        """List the user's most-worn items (analytics-derived). ``limit`` 1–10."""
        limit = max(1, min(int(limit), 10))
        analytics = await _wrap(client.async_analytics())
        most_worn = analytics.get("most_worn") if isinstance(analytics, dict) else None
        if not isinstance(most_worn, list):
            most_worn = []
        return {"count": min(len(most_worn), limit), "items": most_worn[:limit]}

    # ─── items ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_items(
        page: int = 1,
        page_size: int = 50,
        category: str | None = None,
        is_archived: bool | None = None,
        needs_wash: bool | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Paginated item search. Filters: category, is_archived, needs_wash, search (name substring)."""
        return await _wrap(
            client.async_list_items(
                page=page,
                page_size=page_size,
                category=category,
                is_archived=is_archived,
                needs_wash=needs_wash,
                search=search,
            )
        )

    @mcp.tool()
    async def get_item(item_id: str) -> dict[str, Any]:
        """Fetch a single item by id."""
        return await _wrap(client.async_get_item(item_id))

    @mcp.tool()
    async def get_items_to_wash(limit: int = 8) -> dict[str, Any]:
        """List items the backend has flagged as ``needs_wash=true``. ``limit`` 1–20."""
        limit = max(1, min(int(limit), 20))
        items = await _wrap(client.async_items_needing_wash(limit=limit))
        return {"count": len(items), "items": items}

    @mcp.tool()
    async def log_wear(item_id: str, date: str | None = None) -> dict[str, Any]:
        """Log a wear for ``item_id``. ``date`` is optional YYYY-MM-DD; defaults to today."""
        payload: dict[str, Any] = {}
        if date:
            payload["date"] = date
        return await _wrap(client.async_log_wear(item_id, payload))

    @mcp.tool()
    async def log_wash(item_id: str) -> dict[str, Any]:
        """Mark ``item_id`` as washed. Resets wears_since_wash on the backend."""
        return await _wrap(client.async_log_wash(item_id))

    @mcp.tool()
    async def archive_item(item_id: str, reason: str | None = None) -> dict[str, Any]:
        """Archive ``item_id`` (hide from active wardrobe). Optional ``reason``."""
        return await _wrap(client.async_archive_item(item_id, reason))

    @mcp.tool()
    async def restore_item(item_id: str) -> dict[str, Any]:
        """Restore a previously-archived item."""
        return await _wrap(client.async_restore_item(item_id))

    # ─── outfits ─────────────────────────────────────────────────────────

    @mcp.tool()
    async def suggest_outfit(
        occasion: str | None = None,
        time_of_day: str | None = None,
        target_date: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Generate a new outfit. Optional hints:
        - occasion: one of beach/brunch/business-casual/casual/date/dinner/formal/gym/hiking/
          interview/lounge/office/outdoor/party/running/smart-casual/sport/sporty/travel/
          wedding/weekend/work
        - time_of_day: morning/afternoon/evening/night/full day
        - target_date: YYYY-MM-DD
        - notes: free-form context (e.g., "warm layers", "no white")
        """
        if occasion is not None and occasion not in _VALID_OCCASIONS:
            raise ValueError(f"occasion must be one of {_VALID_OCCASIONS}")
        if time_of_day is not None and time_of_day not in _VALID_TIME_OF_DAY:
            raise ValueError(f"time_of_day must be one of {_VALID_TIME_OF_DAY}")
        payload: dict[str, Any] = {}
        for k, v in (
            ("occasion", occasion),
            ("time_of_day", time_of_day),
            ("target_date", target_date),
            ("notes", notes),
        ):
            if v is not None:
                payload[k] = v
        return await _wrap(client.async_suggest_outfit(payload))

    @mcp.tool()
    async def get_latest_outfit() -> dict[str, Any]:
        """Return the most recent outfit (any status)."""
        outfits = await _wrap(client.async_recent_outfits(limit=1))
        return outfits[0] if outfits else {}

    @mcp.tool()
    async def get_outfit(outfit_id: str) -> dict[str, Any]:
        """Fetch a single outfit by id (full item list)."""
        return await _wrap(client.async_get_outfit(outfit_id))

    @mcp.tool()
    async def get_recent_outfits(
        limit: int = 6,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List recent outfits. ``limit`` 1–20. Optional ``status`` filter:
        pending/sent/accepted/rejected/skipped.
        """
        limit = max(1, min(int(limit), 20))
        if status is not None and status not in _OUTFIT_STATUSES:
            raise ValueError(f"status must be one of {_OUTFIT_STATUSES}")
        outfits = await _wrap(client.async_recent_outfits(limit=limit))
        if status is not None:
            outfits = [o for o in outfits if o.get("status") == status]
        return {"count": len(outfits), "outfits": outfits}

    async def _act_on_latest(action: str) -> dict[str, Any]:
        outfits = await _wrap(client.async_recent_outfits(limit=20))
        target = next(
            (
                o for o in outfits
                if o.get("status") in _ACTIONABLE_STATUSES and o.get("id") is not None
            ),
            None,
        )
        if target is None:
            return {"error": "No actionable (pending/sent) outfit found."}
        outfit_id = str(target["id"])
        await _wrap(client.async_outfit_action(outfit_id, action))
        return {"action": action, "outfit_id": outfit_id, "outfit": target}

    @mcp.tool()
    async def accept_latest_outfit() -> dict[str, Any]:
        """Accept the most recent pending/sent outfit suggestion."""
        return await _act_on_latest("accept")

    @mcp.tool()
    async def reject_latest_outfit() -> dict[str, Any]:
        """Reject the most recent pending/sent outfit suggestion."""
        return await _act_on_latest("reject")

    @mcp.tool()
    async def skip_latest_outfit() -> dict[str, Any]:
        """Skip the most recent pending/sent outfit suggestion."""
        return await _act_on_latest("skip")

    @mcp.tool()
    async def submit_outfit_feedback(
        outfit_id: str,
        rating: int | None = None,
        wore: bool | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Submit feedback on an outfit. ``rating`` 1–5, ``wore`` boolean, ``notes`` free-form."""
        if rating is not None and not 1 <= int(rating) <= 5:
            raise ValueError("rating must be 1–5")
        payload = {
            k: v for k, v in (
                ("rating", rating), ("wore", wore), ("notes", notes),
            ) if v is not None
        }
        return await _wrap(client.async_post_outfit_feedback(outfit_id, payload))

    # ─── notifications ───────────────────────────────────────────────────

    @mcp.tool()
    async def recent_notifications(limit: int = 20) -> dict[str, Any]:
        """List recent push/Mattermost notifications. ``limit`` 1–100."""
        limit = max(1, min(int(limit), 100))
        notifications = await _wrap(client.async_recent_notifications(limit=limit))
        return {"count": len(notifications), "notifications": notifications}

    @mcp.tool()
    async def test_notification(setting_id: str) -> dict[str, Any]:
        """Fire a test notification through the given notification setting."""
        return await _wrap(client.async_test_notification(setting_id))

    _register_skill_resources(mcp)
    return mcp


def _register_skill_resources(mcp: FastMCP) -> None:
    """Expose the bundled agentskills.io skill as MCP resources.

    Convention: URI ending in `/SKILL.md` (text/markdown) is the skill
    manifest; all sibling resources sharing the URI prefix
    `skill://<slug>/` form the bundle. Compatible MCP clients use this
    to auto-install the skill without a separate package URL or manual
    drop into a skills directory.

    File contents are captured at startup (the skill ships inside the
    immutable container image, so there's nothing to refresh). Missing
    skill dir is logged WARNING and silently no-op — tools still work.
    """
    if not _SKILL_DIR.is_dir():
        _LOGGER.warning(
            "Skill bundle directory missing at %s; MCP resources not registered. "
            "Tools still work; clients that auto-install skills will skip.",
            _SKILL_DIR,
        )
        return
    count = 0
    for path in sorted(_SKILL_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(_SKILL_DIR).as_posix()
        uri = f"skill://{_SKILL_SLUG}/{rel}"
        mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "text/plain")
        text = path.read_text(encoding="utf-8")

        def _make_reader(captured_text: str):
            def _read() -> str:
                return captured_text
            return _read

        mcp.resource(uri, name=path.name, mime_type=mime)(_make_reader(text))
        count += 1
    _LOGGER.info(
        "Registered %d skill resource(s) under skill://%s/ (manifest + siblings)",
        count, _SKILL_SLUG,
    )


async def _wrap(coro):
    """Convert WardrowbeApiError into a clean MCP-visible exception."""
    try:
        return await coro
    except WardrowbeApiError as err:
        raise RuntimeError(str(err)) from err
