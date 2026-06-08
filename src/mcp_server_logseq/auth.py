"""Bearer-token auth for the Streamable HTTP transport.

A minimal ASGI middleware that requires `Authorization: Bearer <token>` on every
HTTP request to the MCP endpoint. This guards the network-exposed transport so a
phone or other remote client must present a shared secret.

This is deliberately simple (a single shared secret), not full OAuth 2.1. It is
safe only over an already-encrypted channel (Tailscale/WireGuard/VPN/HTTPS) — do
not expose plain HTTP to an untrusted network.
"""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Reject HTTP requests whose bearer token does not match the secret."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")

        # Constant-time comparison to avoid leaking the token via timing.
        if not hmac.compare_digest(provided, self._expected):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
