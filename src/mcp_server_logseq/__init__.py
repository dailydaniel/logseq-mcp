"""Logseq MCP server package."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from .server import config, mcp


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
    args = parser.parse_args()

    token = args.api_key or os.getenv("LOGSEQ_API_TOKEN")
    if not token:
        parser.error(
            "Logseq API token required: pass --api-key or set LOGSEQ_API_TOKEN"
        )

    config["token"] = token
    config["url"] = args.url or os.getenv("LOGSEQ_API_URL") or "http://localhost:12315"

    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
