"""Logseq MCP server package."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from . import server
from .config import ConfigError, default_config_path, load_config
from .server import config, mcp


def _run_streamable_http(host: str, port: int, http_token: str | None) -> None:
    """Serve over Streamable HTTP, gated by a bearer token."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    from .auth import BearerAuthMiddleware

    if not http_token:
        raise SystemExit(
            "Streamable HTTP transport requires an auth token: pass --http-token "
            "or set LOGSEQ_MCP_HTTP_TOKEN. (Refusing to expose an unauthenticated "
            "endpoint.)"
        )

    mcp.settings.host = host
    mcp.settings.port = port
    # The SDK auto-enables a localhost-only DNS-rebinding guard (it assumes a
    # local server) which 421s remote clients. We sit behind a bearer token (and
    # typically a VPN like Tailscale), so disable it for the HTTP transport.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=http_token)

    uvicorn.run(app, host=host, port=port)


def main() -> None:
    """CLI entry point for the Logseq MCP server."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "MCP server for the Logseq local HTTP API "
            "(https://docs.logseq.com/#/page/local%20http%20server)"
        )
    )
    parser.add_argument("--api-key", help="Logseq API token")
    parser.add_argument("--url", help="Logseq API base URL")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.getenv("LOGSEQ_MCP_TRANSPORT", "stdio"),
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("LOGSEQ_MCP_HOST", "127.0.0.1"),
        help="Host for streamable-http transport",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("LOGSEQ_MCP_PORT", "8000")),
        help="Port for streamable-http transport",
    )
    parser.add_argument(
        "--http-token",
        default=os.getenv("LOGSEQ_MCP_HTTP_TOKEN"),
        help="Bearer token required from clients on the streamable-http endpoint",
    )
    args = parser.parse_args()

    token = args.api_key or os.getenv("LOGSEQ_API_TOKEN")
    if not token:
        parser.error(
            "Logseq API token required: pass --api-key or set LOGSEQ_API_TOKEN"
        )

    config["token"] = token
    config["url"] = args.url or os.getenv("LOGSEQ_API_URL") or "http://localhost:12315"

    cfg_path = Path(os.getenv("LOGSEQ_MCP_CONFIG") or default_config_path())
    try:
        server.app_config = load_config(cfg_path)
    except ConfigError as exc:
        parser.error(str(exc))
    server.register_dynamic_tools()

    if args.transport == "streamable-http":
        _run_streamable_http(args.host, args.port, args.http_token)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
