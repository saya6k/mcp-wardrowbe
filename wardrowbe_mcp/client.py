"""HTTP client for the Wardrowbe REST API (MCP-side port of hacs api.py).

Same /auth/sync → wardrowbe-JWT → bearer-bearing requests as the HA
integration. Pared down to the calls the MCP tools actually need.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .auth import TokenProvider, WardrowbeAuthError

_LOGGER = logging.getLogger(__name__)

_API_BASE = "/api/v1"
_DEFAULT_TIMEOUT = 30
_JWT_REFRESH_LEEWAY = timedelta(hours=1)
_DEFAULT_JWT_TTL_SECONDS = 6 * 24 * 3600


class WardrowbeApiError(Exception):
    """Generic Wardrowbe API error."""


class WardrowbeClient:
    """Async REST client for Wardrowbe."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        token_provider: TokenProvider,
        *,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._token_provider = token_provider
        self._verify_ssl = verify_ssl
        self._jwt: str | None = None
        self._jwt_expires_at: datetime | None = None
        self._sync_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    # ─── unauthenticated probes ──────────────────────────────────────────

    async def async_health(self) -> bool:
        try:
            async with self._session.get(
                self._url("/health"),
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False

    async def async_auth_config(self) -> dict[str, Any]:
        async with self._session.get(
            self._url(f"{_API_BASE}/auth/config"),
            ssl=self._verify_ssl,
            timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ─── authenticated reads ─────────────────────────────────────────────

    async def async_session_info(self) -> dict[str, Any]:
        return await self._request("GET", f"{_API_BASE}/auth/session")

    async def async_analytics(self) -> dict[str, Any]:
        data = await self._request("GET", f"{_API_BASE}/analytics")
        if isinstance(data, dict):
            _resolve_image_urls(data, self._host)
        return data

    async def async_recent_outfits(self, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", f"{_API_BASE}/outfits", params={"limit": limit}
        )
        outfits = _coerce_list(data)
        _resolve_image_urls(outfits, self._host)
        return outfits

    async def async_get_outfit(self, outfit_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"{_API_BASE}/outfits/{outfit_id}")
        if isinstance(data, dict):
            _resolve_image_urls(data, self._host)
        return data

    async def async_recent_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", f"{_API_BASE}/notifications/history", params={"limit": limit}
        )
        return _coerce_list(data)

    async def async_items_needing_wash(self, limit: int = 100) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"{_API_BASE}/items",
            params={
                "needs_wash": "true",
                "is_archived": "false",
                "page_size": limit,
            },
        )
        items = _coerce_list(data)
        _resolve_image_urls(items, self._host)
        return items

    async def async_list_items(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        category: str | None = None,
        is_archived: bool | None = None,
        needs_wash: bool | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if category is not None:
            params["category"] = category
        if is_archived is not None:
            params["is_archived"] = "true" if is_archived else "false"
        if needs_wash is not None:
            params["needs_wash"] = "true" if needs_wash else "false"
        if search:
            params["search"] = search
        data = await self._request("GET", f"{_API_BASE}/items", params=params)
        if isinstance(data, dict):
            _resolve_image_urls(data, self._host)
        elif isinstance(data, list):
            _resolve_image_urls(data, self._host)
        return data if isinstance(data, dict) else {"items": data}

    async def async_get_item(self, item_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"{_API_BASE}/items/{item_id}")
        if isinstance(data, dict):
            _resolve_image_urls(data, self._host)
        return data

    # ─── authenticated writes ────────────────────────────────────────────

    async def async_suggest_outfit(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            "POST", f"{_API_BASE}/outfits/suggest", json=payload
        )
        if isinstance(result, dict):
            _resolve_image_urls(result, self._host)
        return result

    async def async_outfit_action(self, outfit_id: str, action: str) -> dict[str, Any]:
        if action not in {"accept", "reject", "skip"}:
            raise ValueError(f"Unsupported outfit action: {action}")
        return await self._request(
            "POST", f"{_API_BASE}/outfits/{outfit_id}/{action}"
        )

    async def async_post_outfit_feedback(
        self, outfit_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_API_BASE}/outfits/{outfit_id}/feedback", json=payload
        )

    async def async_log_wear(
        self, item_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_API_BASE}/items/{item_id}/wear", json=payload
        )

    async def async_log_wash(self, item_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_API_BASE}/items/{item_id}/wash", json={}
        )

    async def async_archive_item(
        self, item_id: str, reason: str | None
    ) -> dict[str, Any]:
        body = {"reason": reason} if reason else {}
        return await self._request(
            "POST", f"{_API_BASE}/items/{item_id}/archive", json=body
        )

    async def async_restore_item(self, item_id: str) -> dict[str, Any]:
        return await self._request("POST", f"{_API_BASE}/items/{item_id}/restore")

    async def async_test_notification(self, setting_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_API_BASE}/notifications/settings/{setting_id}/test"
        )

    # ─── internals ───────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self._host}{path}"

    async def _ensure_jwt(self, *, force: bool = False) -> str:
        async with self._sync_lock:
            now = datetime.now(timezone.utc)
            if (
                not force
                and self._jwt
                and self._jwt_expires_at
                and self._jwt_expires_at - _JWT_REFRESH_LEEWAY > now
            ):
                return self._jwt
            payload = await self._token_provider.async_get_sync_payload()
            try:
                async with self._session.post(
                    self._url(f"{_API_BASE}/auth/sync"),
                    json=payload,
                    ssl=self._verify_ssl,
                    timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
                ) as resp:
                    body_text = await resp.text()
                    if resp.status >= 400:
                        raise WardrowbeAuthError(
                            f"/auth/sync failed: {resp.status} {body_text}"
                        )
                    body = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise WardrowbeApiError(f"/auth/sync transport error: {err}") from err
            jwt_token = body.get("access_token")
            if not jwt_token:
                raise WardrowbeAuthError("/auth/sync did not return access_token")
            ttl = int(body.get("expires_in", _DEFAULT_JWT_TTL_SECONDS))
            self._jwt = jwt_token
            self._jwt_expires_at = now + timedelta(seconds=ttl)
            return jwt_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        last_status: int | None = None
        last_body: str | None = None
        for attempt in (0, 1):
            jwt_token = await self._ensure_jwt(force=attempt == 1)
            try:
                async with self._session.request(
                    method,
                    self._url(path),
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {jwt_token}"},
                    ssl=self._verify_ssl,
                    timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    last_status = resp.status
                    if resp.status >= 400:
                        last_body = await resp.text()
                        break
                    if resp.status == 204:
                        return None
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise WardrowbeApiError(f"{method} {path} failed: {err}") from err
        if last_status == 401:
            raise WardrowbeAuthError(
                f"{method} {path} → 401 after re-sync: {last_body}"
            )
        raise WardrowbeApiError(f"{method} {path} → {last_status}: {last_body}")


# ─── helpers ────────────────────────────────────────────────────────────


def _coerce_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "data", "outfits", "notifications"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


_IMAGE_URL_KEYS: tuple[str, ...] = (
    "image_url",
    "thumbnail_url",
    "image_path",
    "thumbnail_path",
    "preview_url",
    "composite_image_url",
    "composite_url",
    "photo_url",
    "media_url",
)


def _resolve_image_urls(payload: Any, host: str) -> None:
    """Rewrite relative Wardrowbe image URLs to absolute in-place."""
    if isinstance(payload, list):
        for entry in payload:
            _resolve_image_urls(entry, host)
        return
    if not isinstance(payload, dict):
        return
    for key in _IMAGE_URL_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.startswith("/") and host:
            payload[key] = f"{host}{val}"
    for val in payload.values():
        if isinstance(val, (list, dict)):
            _resolve_image_urls(val, host)
