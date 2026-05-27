"""Token providers for the Wardrowbe MCP server.

Two auth modes mirror the hacs-wardrowbe integration:

* ``DevTokenProvider`` — sends ``external_id`` + synthesised email/display_name
  for dev-mode installs (backend `_is_dev_mode()` is True).
* ``OIDCRefreshTokenProvider`` — exchanges a stored OIDC ``refresh_token`` at
  the issuer's token endpoint for a fresh ``id_token`` and forwards it to
  /auth/sync. The refresh token is obtained once externally (interactive
  browser flow); MCP keeps it warm.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class WardrowbeAuthError(Exception):
    """Authentication failed and could not be recovered."""


class TokenProvider:
    """Produces the payload expected by Wardrowbe /auth/sync."""

    async def async_get_sync_payload(self) -> dict[str, Any]:
        raise NotImplementedError


class DevTokenProvider(TokenProvider):
    """external_id + synthesised email/name for dev-mode installs."""

    def __init__(
        self,
        external_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self._external_id = external_id
        self._email = email or f"{external_id}@wardrowbe.local"
        self._display_name = display_name or external_id

    async def async_get_sync_payload(self) -> dict[str, Any]:
        return {
            "external_id": self._external_id,
            "email": self._email,
            "display_name": self._display_name,
        }


class OIDCRefreshTokenProvider(TokenProvider):
    """Refresh-token-based OIDC provider.

    The refresh token is obtained externally (once) via an interactive PKCE
    flow against the configured OIDC issuer, then passed to this provider via
    addon config. We exchange it for a fresh id_token at the issuer's token
    endpoint on each /auth/sync (cached briefly to avoid hammering the IDP).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        issuer_url: str,
        client_id: str,
        client_secret: str | None,
        refresh_token: str,
    ) -> None:
        self._session = session
        self._issuer_url = issuer_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret or None
        self._refresh_token = refresh_token
        self._token_endpoint: str | None = None

    async def _discover_token_endpoint(self) -> str:
        if self._token_endpoint:
            return self._token_endpoint
        well_known = f"{self._issuer_url}/.well-known/openid-configuration"
        async with self._session.get(
            well_known, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            cfg = await resp.json()
        endpoint = cfg.get("token_endpoint")
        if not isinstance(endpoint, str):
            raise WardrowbeAuthError(
                f"OIDC discovery for {self._issuer_url} did not return token_endpoint"
            )
        self._token_endpoint = endpoint
        return endpoint

    async def async_get_sync_payload(self) -> dict[str, Any]:
        token_endpoint = await self._discover_token_endpoint()
        data: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        async with self._session.post(
            token_endpoint,
            data=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise WardrowbeAuthError(
                    f"OIDC refresh failed at {token_endpoint}: {resp.status} {body}"
                )
        id_token = body.get("id_token")
        if not id_token:
            raise WardrowbeAuthError(
                "OIDC token endpoint did not return an id_token — make sure the "
                "client requested the `openid` scope."
            )
        # Rotate the refresh token if the IDP issued a new one (RT rotation).
        if (new_rt := body.get("refresh_token")) and new_rt != self._refresh_token:
            _LOGGER.info("OIDC refresh_token rotated")
            self._refresh_token = new_rt
        return _build_oidc_sync_payload(id_token)


def _build_oidc_sync_payload(id_token: str) -> dict[str, Any]:
    """Decode an OIDC id_token and shape it for /auth/sync.

    Signature is not verified here — the backend validates it again against
    the issuer's JWKS in `validate_oidc_id_token`. We only need to extract
    the claims to populate the request body fields.
    """
    claims = _decode_jwt_payload(id_token)
    sub = claims.get("sub")
    if not sub:
        raise WardrowbeAuthError("id_token missing required `sub` claim")
    email = claims.get("email") or ""
    display_name = (
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("nickname")
        or email
        or str(sub)
    )
    return {
        "external_id": str(sub),
        "email": email,
        "display_name": display_name,
        "id_token": id_token,
    }


def _decode_jwt_payload(id_token: str) -> dict[str, Any]:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise WardrowbeAuthError("Malformed id_token (expected 3 dot-separated parts)")
    encoded = parts[1]
    pad = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + pad)
        decoded = json.loads(raw)
    except (ValueError, binascii.Error, json.JSONDecodeError) as err:
        raise WardrowbeAuthError(f"Could not decode id_token payload: {err}") from err
    if not isinstance(decoded, dict):
        raise WardrowbeAuthError("id_token payload is not a JSON object")
    return decoded
