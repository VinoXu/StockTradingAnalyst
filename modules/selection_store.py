"""Persist analysis selection (checked symbols) in local SQLite app_state."""

from __future__ import annotations

import json
import re
from typing import Any

from modules.db import get_connection, init_db

_KEY_SYMBOLS = "analysis_selected_symbols"
_KEY_EXPLICIT = "analysis_selected_explicit"
_CODE_RE = re.compile(r"^\d{6}$")


def _get_state(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
        (key, value),
    )


def _normalize_codes(codes: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes or []:
        code = str(raw or "").strip().split(".")[0]
        if not _CODE_RE.match(code) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def get_selection() -> dict[str, Any]:
    init_db()
    with get_connection() as conn:
        raw = _get_state(conn, _KEY_SYMBOLS) or "[]"
        explicit_raw = _get_state(conn, _KEY_EXPLICIT) or "0"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return {
        "symbols": _normalize_codes([str(x) for x in parsed]),
        "explicit": str(explicit_raw).strip() in ("1", "true", "True", "yes"),
    }


def save_selection(symbols: list[str] | None, *, explicit: bool = True) -> dict[str, Any]:
    init_db()
    codes = _normalize_codes(symbols)
    with get_connection() as conn:
        _set_state(conn, _KEY_SYMBOLS, json.dumps(codes, ensure_ascii=False))
        _set_state(conn, _KEY_EXPLICIT, "1" if explicit else "0")
        conn.commit()
    return {"symbols": codes, "explicit": bool(explicit)}
