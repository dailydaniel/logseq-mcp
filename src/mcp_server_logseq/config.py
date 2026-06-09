"""Configuration loading for the Logseq MCP server.

The control plane is a TOML file (see assets/logseq-mcp-design-v0.7.md); custom
queries live in external EDN files referenced from it. Secrets and connection
URL come from the environment, never the config file.

The server starts fine with no config file at all — everything falls back to
safe defaults (read-mostly, agent writes forced to `byAgent/`, status changes
off).
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import edn_format
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


@contextlib.contextmanager
def _silence_fd(fileno: int) -> Iterator[None]:
    """Mute a raw OS file descriptor (e.g. stderr) for the duration.

    Stronger than contextlib.redirect_stderr: it catches writes that bypass
    Python's sys.stderr object (as PLY's table generation does).
    """
    saved = os.dup(fileno)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, fileno)
        yield
    finally:
        os.dup2(saved, fileno)
        os.close(devnull)
        os.close(saved)


# edn_format rebuilds its PLY lexer on every parse, each time dumping debug noise
# to stderr (which the stdio transport shares). Route all EDN parse/serialize
# through a hard stderr mute so server logs stay clean.
def _edn_loads(text: str) -> Any:
    with _silence_fd(2):
        return edn_format.loads(text)


def _edn_dumps(value: Any) -> str:
    with _silence_fd(2):
        return edn_format.dumps(value)


class ConfigError(Exception):
    """Raised on any invalid configuration (bad TOML/EDN/structure)."""


# ---------------------------------------------------------------------------
# TOML section models (structural validation)
# ---------------------------------------------------------------------------


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadCfg(_Section):
    resolve_depth: int = Field(default=2, ge=0)


class WriteCfg(_Section):
    allow_agents_write_any: bool = False
    agent_write_prefix: str = "byAgent"


class SearchCfg(_Section):
    files_path: str = ""


class BlacklistCfg(_Section):
    pages: list[str] = Field(default_factory=list)


class TasksCfg(_Section):
    allow_status_change: bool = False


class AuditLogCfg(_Section):
    enabled: bool = False


class QueryCfg(_Section):
    description: str = ""
    file: Optional[str] = None
    query: Optional[str] = None
    rules: Optional[str] = None
    register_as_tool: bool = False
    resolve_block_refs: Optional[bool] = None

    @model_validator(mode="after")
    def _one_source(self) -> "QueryCfg":
        if bool(self.file) == bool(self.query):
            raise ValueError("needs exactly one of 'file' or inline 'query'")
        return self


class _RawConfig(_Section):
    read: ReadCfg = Field(default_factory=ReadCfg)
    write: WriteCfg = Field(default_factory=WriteCfg)
    search: SearchCfg = Field(default_factory=SearchCfg)
    blacklist: BlacklistCfg = Field(default_factory=BlacklistCfg)
    tasks: TasksCfg = Field(default_factory=TasksCfg)
    audit_log: Optional[AuditLogCfg] = None
    queries: dict[str, QueryCfg] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Runtime config (with compiled queries)
# ---------------------------------------------------------------------------


@dataclass
class CompiledQuery:
    """A query ready to run: split into the parts datascriptQuery/DB.q expect."""

    name: str
    description: str
    kind: str  # "datalog" | "simple"
    query: str  # datalog vector string, or simple-query DSL string
    rules: Optional[str]  # datalog rules vector string, if any
    inputs: list[Any]  # may contain server-resolved "@..." tokens
    register_as_tool: bool
    resolve_block_refs: Optional[bool]


@dataclass
class AppConfig:
    read: ReadCfg
    write: WriteCfg
    search: SearchCfg
    blacklist: BlacklistCfg
    tasks: TasksCfg
    audit_log: Optional[AuditLogCfg]
    queries: dict[str, CompiledQuery]
    config_dir: Optional[Path]


# ---------------------------------------------------------------------------
# EDN helpers
# ---------------------------------------------------------------------------

_K_QUERY = edn_format.Keyword("query")
_K_RULES = edn_format.Keyword("rules")
_K_INPUTS = edn_format.Keyword("inputs")


def _plainify(value: Any) -> Any:
    """Convert EDN-parsed structures into plain JSON-ish Python."""
    if isinstance(value, (edn_format.Keyword, edn_format.Symbol)):
        return str(value)
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if hasattr(value, "items"):
        return {_plainify(k): _plainify(v) for k, v in value.items()}
    try:
        return [_plainify(v) for v in value]
    except TypeError:
        return value


def _strip_leading_comments(text: str) -> str:
    """Drop leading blank/`;`-comment lines so we can detect the first token."""
    out: list[str] = []
    started = False
    for line in text.splitlines():
        if not started:
            stripped = line.lstrip()
            if not stripped or stripped.startswith(";"):
                continue
            started = True
        out.append(line)
    return "\n".join(out).strip()


def _normalize_edn(text: str, what: str) -> str:
    """Parse EDN and re-serialize it — validates and strips comments."""
    try:
        return _edn_dumps(_edn_loads(text))
    except Exception as exc:  # edn_format raises various parse errors
        raise ConfigError(f"{what}: invalid EDN: {exc}") from exc


def _compile_text(text: str, shared_rules: Optional[str]) -> tuple[str, str, Optional[str], list[Any]]:
    """Detect the query kind from its first token and split it into parts.

    `(` → simple DSL, `[` → datalog vector, `{` → full advanced-query map.
    """
    s = _strip_leading_comments(text)
    if not s:
        raise ValueError("empty query")
    head = s[0]
    if head == "(":
        return "simple", s, None, []
    if head == "[":
        try:
            normalized = _edn_dumps(_edn_loads(s))
        except Exception as exc:
            raise ValueError(f"invalid datalog: {exc}") from exc
        return "datalog", normalized, shared_rules, []
    if head == "{":
        parsed = _edn_loads(s)
        if not hasattr(parsed, "items"):
            raise ValueError("expected an advanced-query map {:query ...}")
        m = dict(parsed)
        if _K_QUERY not in m:
            raise ValueError("advanced-query map is missing :query")
        query = _edn_dumps(m[_K_QUERY])
        rules = _edn_dumps(m[_K_RULES]) if _K_RULES in m else shared_rules
        inputs = _plainify(m[_K_INPUTS]) if _K_INPUTS in m else []
        if not isinstance(inputs, list):
            raise ValueError(":inputs must be a vector")
        return "datalog", query, rules, inputs
    raise ValueError("query must start with '(' (simple), '[' (datalog) or '{' (map)")


def _read_rel(base_dir: Optional[Path], rel: str, what: str) -> str:
    if base_dir is None:
        raise ConfigError(f"{what}: cannot resolve '{rel}' without a config directory")
    path = (base_dir / rel).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{what}: cannot read '{path}': {exc}") from exc


def _compile_query(name: str, q: QueryCfg, base_dir: Optional[Path]) -> CompiledQuery:
    shared_rules: Optional[str] = None
    if q.rules:
        raw_rules = _read_rel(base_dir, q.rules, f"query '{name}' rules")
        shared_rules = _normalize_edn(raw_rules, f"query '{name}' rules file")

    text = _read_rel(base_dir, q.file, f"query '{name}' file") if q.file else q.query
    assert text is not None  # guaranteed by QueryCfg validator

    try:
        kind, query, rules, inputs = _compile_text(text, shared_rules)
    except ValueError as exc:
        raise ConfigError(f"query '{name}': {exc}") from exc

    return CompiledQuery(
        name=name,
        description=q.description,
        kind=kind,
        query=query,
        rules=rules,
        inputs=inputs,
        register_as_tool=q.register_as_tool,
        resolve_block_refs=q.resolve_block_refs,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def default_config_path() -> Path:
    return Path("~/.config/logseq-mcp/config.toml").expanduser()


def load_config(path: Optional[Path]) -> AppConfig:
    """Load and validate config from `path`. Missing file → all defaults."""
    if path is not None and path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        try:
            raw = _RawConfig.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid config {path}:\n{exc}") from exc
        base_dir: Optional[Path] = path.parent
    else:
        raw = _RawConfig()
        base_dir = path.parent if path is not None else None

    queries = {name: _compile_query(name, q, base_dir) for name, q in raw.queries.items()}

    return AppConfig(
        read=raw.read,
        write=raw.write,
        search=raw.search,
        blacklist=raw.blacklist,
        tasks=raw.tasks,
        audit_log=raw.audit_log,
        queries=queries,
        config_dir=base_dir,
    )
