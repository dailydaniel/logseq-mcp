"""Query execution: datascript/simple queries and server-resolved inputs.

Logseq's HTTP `datascriptQuery` EDN-reads every string input, so values must be
sent as EDN text: a Python str "DONE" must arrive as `"DONE"` (quoted) to match a
string attribute. Rules are passed as an EDN string too. (Verified empirically.)
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from .client import LogseqClient
from .config import CompiledQuery, _edn_dumps


class QueryError(Exception):
    pass


def resolve_input_token(token: Any) -> Any:
    """Replace server-resolved `@...` tokens with concrete values."""
    if not isinstance(token, str) or not token.startswith("@"):
        return token
    today = datetime.date.today()
    if token == "@today-journal-day":
        return int(today.strftime("%Y%m%d"))
    if token.startswith("@today-minus:"):
        try:
            n = int(token.split(":", 1)[1])
        except ValueError as exc:
            raise QueryError(f"bad token {token!r}") from exc
        return int((today - datetime.timedelta(days=n)).strftime("%Y%m%d"))
    if token == "@now-iso":
        return datetime.datetime.now().isoformat(timespec="seconds")
    raise QueryError(f"unknown input token: {token}")


def _encode_inputs(inputs: list[Any]) -> list[str]:
    """Resolve @tokens then EDN-encode each input for datascriptQuery."""
    return [_edn_dumps(resolve_input_token(v)) for v in inputs]


async def run_datascript(
    client: LogseqClient,
    query: str,
    inputs: Optional[list[Any]] = None,
    rules: Optional[str] = None,
) -> list[Any]:
    args: list[Any] = [query]
    if rules:
        args.append(rules)  # already EDN text
    args.extend(_encode_inputs(inputs or []))
    result = await client.call("logseq.DB.datascriptQuery", args)
    return result or []


async def run_simple(client: LogseqClient, dsl: str) -> list[Any]:
    result = await client.call("logseq.DB.q", [dsl])
    return result or []


async def run_compiled(
    client: LogseqClient,
    cq: CompiledQuery,
    override_inputs: Optional[list[Any]] = None,
) -> list[Any]:
    """Execute a configured query (datalog or simple)."""
    if cq.kind == "simple":
        return await run_simple(client, cq.query)
    inputs = override_inputs if override_inputs is not None else cq.inputs
    return await run_datascript(client, cq.query, inputs, cq.rules)


def flatten_pull_rows(rows: list[Any]) -> list[dict]:
    """datascriptQuery returns rows like [[{block}], ...]; pull the block dicts."""
    out: list[dict] = []
    for row in rows:
        item = row[0] if isinstance(row, list) and row else row
        if isinstance(item, dict):
            out.append(item)
    return out
