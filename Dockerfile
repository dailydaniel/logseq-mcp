# Logseq MCP server — Streamable HTTP, for running on a (headless) Mac/Linux host.
#
# The container talks to a Logseq HTTP API running on the *host*:
#   - Docker Desktop (macOS/Windows): http://host.docker.internal:12315  (default)
#   - Linux: pass --add-host=host.docker.internal:host-gateway, or set
#     LOGSEQ_API_URL to the host IP.
#
# Required env at runtime:
#   LOGSEQ_API_TOKEN       Logseq authorization token (server -> Logseq)
#   LOGSEQ_MCP_HTTP_TOKEN  bearer token clients must send (phone -> this server)
#
# Example:
#   docker build -t logseq-mcp .
#   docker run --rm -p 8000:8000 \
#     -e LOGSEQ_API_TOKEN=xxx -e LOGSEQ_MCP_HTTP_TOKEN=yyy logseq-mcp

FROM python:3.12-slim

# ripgrep powers the file-backed search backend.
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs (from the official distroless image).
COPY --from=ghcr.io/astral-sh/uv:0.5.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached unless lockfile/manifest change).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Streamable HTTP defaults; the host Logseq is reachable via host.docker.internal.
ENV LOGSEQ_API_URL=http://host.docker.internal:12315 \
    LOGSEQ_MCP_TRANSPORT=streamable-http \
    LOGSEQ_MCP_HOST=0.0.0.0 \
    LOGSEQ_MCP_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "mcp-server-logseq"]
