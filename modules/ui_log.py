"""UI / session event log for debugging chat loss and client issues."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from modules.db import get_connection, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_ui_event(
    event: str,
    *,
    source: str = "server",
    detail: dict[str, Any] | str | None = None,
    session_id: str = "default",
) -> None:
    init_db()
    payload = detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ui_event_log (source, event, detail, session_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, event, payload, session_id, _now()),
        )
        conn.commit()


def load_ui_events(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, source, event, detail, session_id, created_at
            FROM ui_event_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item["detail"] or "{}")
        except json.JSONDecodeError:
            pass
        out.append(item)
    return out
