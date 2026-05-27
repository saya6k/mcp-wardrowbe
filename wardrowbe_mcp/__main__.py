"""Entry point: build client + MCP server, expose over HTTP or stdio.

Transports:
* ``--transport http`` (default) — Starlette app on one port serving
  ``/mcp`` (Streamable HTTP) + ``/sse`` + ``/messages/``. Bearer auth via
  ``--api-key`` gates every MCP route.
* ``--transport stdio`` — FastMCP stdio loop for bridges that spawn MCP
  servers as child processes (e.g. ``sparfenyuk/mcp-proxy``,
  ``HASS-MCPProxy``). No HTTP server, no Bearer auth — the parent owns
  the trust boundary. JSON-RPC frames go on stdout; keep all logging on
  stderr (``logging.basicConfig`` default).

Auth modes (apply to both transports — both still talk to the wardrowbe
backend over HTTP):
* ``--auth dev`` — dev_login sync with ``--external-id`` (default: wardrowbe-mcp).
  Requires the wardrowbe backend to be in dev mode (DEBUG=true + default
  SECRET_KEY).
* ``--auth oidc`` — refresh-token flow against the configured OIDC issuer.
  Requires --oidc-issuer-url, --oidc-client-id, --oidc-refresh-token.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys

import aiohttp
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .auth import DevTokenProvider, OIDCRefreshTokenProvider, TokenProvider
from .client import WardrowbeClient
from .server import build_mcp_server

_LOGGER = logging.getLogger("wardrowbe_mcp")
_PUBLIC_PATHS = frozenset({"/", "/health"})


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wardrowbe-mcp")
    p.add_argument(
        "--transport",
        choices=("http", "stdio"),
        default=os.environ.get("MCP_TRANSPORT", "http"),
        help="Transport: http (Starlette + SSE/Streamable HTTP) or stdio "
        "(for proxies like sparfenyuk/mcp-proxy and HASS-MCPProxy). "
        "stdio mode ignores --host, --port, --api-key.",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("MCP_BIND_HOST", "0.0.0.0"),
        help="Address to bind (default: 0.0.0.0). http transport only.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_BIND_PORT", "8080")),
        help="Port to bind (default: 8080)",
    )
    p.add_argument(
        "--wardrowbe-url",
        default=os.environ.get("WARDROWBE_URL", "http://127.0.0.1:8000"),
        help="Wardrowbe backend base URL",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("MCP_API_KEY"),
        help="Required bearer token for incoming MCP connections",
    )
    p.add_argument(
        "--auth",
        choices=("dev", "oidc"),
        default=os.environ.get("MCP_AUTH_MODE", "dev"),
        help="Backend auth mode",
    )
    p.add_argument(
        "--external-id",
        default=os.environ.get("MCP_EXTERNAL_ID", "wardrowbe-mcp"),
        help="Dev-mode external_id sent to /auth/sync",
    )
    p.add_argument("--oidc-issuer-url", default=os.environ.get("MCP_OIDC_ISSUER_URL"))
    p.add_argument("--oidc-client-id", default=os.environ.get("MCP_OIDC_CLIENT_ID"))
    p.add_argument(
        "--oidc-client-secret", default=os.environ.get("MCP_OIDC_CLIENT_SECRET")
    )
    p.add_argument(
        "--oidc-refresh-token", default=os.environ.get("MCP_OIDC_REFRESH_TOKEN")
    )
    p.add_argument(
        "--log-level",
        default=os.environ.get("MCP_LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return p


def _build_token_provider(
    args: argparse.Namespace, session: aiohttp.ClientSession
) -> TokenProvider:
    if args.auth == "dev":
        return DevTokenProvider(args.external_id)
    if args.auth == "oidc":
        missing = [
            n for n in ("oidc_issuer_url", "oidc_client_id", "oidc_refresh_token")
            if not getattr(args, n)
        ]
        if missing:
            raise SystemExit(
                f"--auth oidc requires {', '.join('--' + m.replace('_', '-') for m in missing)}"
            )
        return OIDCRefreshTokenProvider(
            session,
            issuer_url=args.oidc_issuer_url,
            client_id=args.oidc_client_id,
            client_secret=args.oidc_client_secret,
            refresh_token=args.oidc_refresh_token,
        )
    raise SystemExit(f"Unknown auth mode: {args.auth}")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject MCP requests without a valid Bearer token (except public probes)."""

    def __init__(self, app, *, expected_token: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {expected_token}"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth != self._expected:
            return JSONResponse(
                {"error": "unauthorized", "detail": "missing or invalid bearer token"},
                status_code=401,
            )
        return await call_next(request)


async def _info(_request: Request) -> JSONResponse:
    """Anonymous health/info endpoint (handy for HA's watchdog and smoke tests)."""
    return JSONResponse(
        {
            "service": "wardrowbe-mcp",
            "version": __version__,
            "status": "ok",
            "endpoints": {
                "streamable_http": "/mcp",
                "sse": "/sse",
                "sse_messages": "/messages/",
            },
            "auth": "Bearer required on all MCP endpoints; this probe is anonymous.",
        }
    )


async def _serve_stdio(args: argparse.Namespace) -> None:
    """Run as a stdio MCP server.

    Used by bridges that spawn MCP servers as subprocesses and read
    JSON-RPC framing from their stdout (sparfenyuk/mcp-proxy, the
    HASS-MCPProxy add-on, etc.). The parent process is the trust
    boundary, so Bearer auth doesn't apply here — backend auth
    (--auth dev|oidc) still does, since that's how we talk to wardrowbe.
    """
    async with aiohttp.ClientSession() as session:
        token_provider = _build_token_provider(args, session)
        client = WardrowbeClient(session, args.wardrowbe_url, token_provider)
        mcp = build_mcp_server(client)
        await mcp.run_stdio_async()


async def _serve_http(args: argparse.Namespace) -> None:
    """Construct everything inside a running event loop, then serve via uvicorn.

    ``aiohttp.ClientSession()`` calls ``asyncio.get_running_loop()`` at
    construction (aiohttp ≥ 3.10), so it must be created while a loop is
    already running. ``uvicorn.run(app)`` starts its own loop too late —
    by then ``_build_app()`` has already failed. So we own the loop here
    via ``asyncio.run`` and hand the live ``Server`` to it via
    ``Server.serve()``.

    The Python MCP SDK's ``sse_app()`` returns a Starlette app whose
    internal routes are at ``/sse`` (GET, EventSource stream) and
    ``/messages/`` (POST, back-channel). ``streamable_http_app()`` exposes
    ``/mcp`` (POST). Don't wrap either in ``Mount("/sse", ...)`` /
    ``Mount("/mcp", ...)`` — the URLs become ``/sse/sse`` / ``/sse/messages/``
    / ``/mcp/mcp``, which works but mangles every client config snippet.
    Instead, splice sub-app routes into one parent Starlette at root.
    """
    async with aiohttp.ClientSession() as session:
        token_provider = _build_token_provider(args, session)
        client = WardrowbeClient(session, args.wardrowbe_url, token_provider)
        mcp = build_mcp_server(client)

        sse_app = mcp.sse_app()
        http_app = mcp.streamable_http_app()

        @contextlib.asynccontextmanager
        async def _lifespan(app: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                for sub in (http_app, sse_app):
                    lifespan_ctx = getattr(sub.router, "lifespan_context", None)
                    if lifespan_ctx is not None:
                        await stack.enter_async_context(lifespan_ctx(sub))
                yield

        middleware = []
        if args.api_key:
            middleware.append(
                Middleware(BearerAuthMiddleware, expected_token=args.api_key)
            )
        else:
            _LOGGER.warning(
                "MCP API key is empty — every caller will be accepted. "
                "Set --api-key."
            )

        app = Starlette(
            routes=[
                Route("/", _info, methods=["GET"]),
                Route("/health", _info, methods=["GET"]),
                *sse_app.routes,
                *http_app.routes,
            ],
            middleware=middleware,
            lifespan=_lifespan,
        )

        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.transport == "stdio":
        _LOGGER.info(
            "Starting wardrowbe-mcp on stdio (auth=%s, wardrowbe=%s)",
            args.auth, args.wardrowbe_url,
        )
        serve = _serve_stdio
    else:
        _LOGGER.info(
            "Starting wardrowbe-mcp on %s:%d (auth=%s, wardrowbe=%s)",
            args.host, args.port, args.auth, args.wardrowbe_url,
        )
        serve = _serve_http
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
