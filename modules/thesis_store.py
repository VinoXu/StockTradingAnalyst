"""Investment thesis persistence and drift tracking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from modules.data_fetcher import _normalize_symbol
from modules.db import get_connection, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_thesis_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS investment_thesis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            session_id INTEGER,
            stance TEXT,
            thesis_json TEXT NOT NULL,
            reply_excerpt TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thesis_symbol_created ON investment_thesis(symbol, created_at DESC)"
    )


def save_thesis(
    symbol: str,
    *,
    session_id: int | None,
    stance: str,
    thesis: dict[str, Any],
    reply_excerpt: str = "",
) -> int:
    init_db()
    code = _normalize_symbol(symbol).split(".")[0]
    with get_connection() as conn:
        migrate_thesis_table(conn)
        cur = conn.execute(
            """
            INSERT INTO investment_thesis (symbol, session_id, stance, thesis_json, reply_excerpt, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, session_id, stance, json.dumps(thesis, ensure_ascii=False), reply_excerpt[:2000], _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_latest_thesis(symbol: str) -> dict[str, Any] | None:
    init_db()
    code = _normalize_symbol(symbol).split(".")[0]
    with get_connection() as conn:
        migrate_thesis_table(conn)
        row = conn.execute(
            """
            SELECT id, symbol, session_id, stance, thesis_json, reply_excerpt, created_at
            FROM investment_thesis WHERE symbol = ? ORDER BY created_at DESC LIMIT 1
            """,
            (code,),
        ).fetchone()
    if not row:
        return None
    thesis = {}
    try:
        thesis = json.loads(row["thesis_json"] or "{}")
    except json.JSONDecodeError:
        thesis = {}
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "session_id": row["session_id"],
        "stance": row["stance"],
        "thesis": thesis,
        "reply_excerpt": row["reply_excerpt"],
        "created_at": row["created_at"],
    }


def build_thesis_snapshot(
    *,
    agent_cards: list[dict[str, Any]],
    fetched: dict[str, Any],
) -> dict[str, Any]:
    return {
        "research_mode": fetched.get("research_mode"),
        "workflow": fetched.get("workflow"),
        "agent_cards": agent_cards,
        "highlights": [
            (row.get("highlights") or {})
            for row in (fetched.get("fundamentals") or {}).get("symbols") or []
        ],
        "research_consensus": [
            {
                "symbol": r.get("symbol"),
                "consensus_note": r.get("consensus_note"),
                "confidence": r.get("confidence"),
            }
            for r in (fetched.get("research_reports") or {}).get("symbols") or []
        ],
    }


def compute_thesis_drift(
    previous: dict[str, Any] | None,
    current_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    if not previous:
        return {"available": False, "reason": "无历史论文"}

    prev_cards = (previous.get("thesis") or {}).get("agent_cards") or []
    prev_map = {c.get("agent"): c for c in prev_cards if c.get("agent")}
    cur_map = {c.get("agent"): c for c in current_cards if c.get("agent")}

    changes: list[dict[str, Any]] = []
    for agent, cur in cur_map.items():
        old = prev_map.get(agent) or {}
        old_stance = old.get("stance")
        new_stance = cur.get("stance")
        old_score = old.get("score")
        new_score = cur.get("score")
        if old_stance != new_stance or old_score != new_score:
            changes.append(
                {
                    "agent": agent,
                    "stance_from": old_stance,
                    "stance_to": new_stance,
                    "score_from": old_score,
                    "score_to": new_score,
                    "kind": "stance_or_score",
                }
            )

    drift_level = "none"
    if len(changes) >= 3:
        drift_level = "high"
    elif changes:
        drift_level = "moderate"

    return {
        "available": True,
        "previous_at": previous.get("created_at"),
        "previous_stance": previous.get("stance"),
        "changes": changes,
        "drift_level": drift_level,
        "summary": _drift_summary(changes, previous.get("stance")),
    }


def _drift_summary(changes: list[dict[str, Any]], prev_stance: str | None) -> str:
    if not changes:
        return "与上次论文相比，各 Agent 立场与评分未见显著变化"
    agents = "、".join(c["agent"] for c in changes[:4])
    return f"相对上次论文（{prev_stance or '未知'}），{agents} 等视角发生漂移，须核对是事实变还是措辞变"


def load_thesis_context_for_symbols(symbols: list[str], current_cards: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for sym in symbols[:3]:
        code = _normalize_symbol(sym).split(".")[0]
        prev = get_latest_thesis(code)
        rows.append(
            {
                "symbol": code,
                "previous": prev,
                "drift": compute_thesis_drift(prev, current_cards) if prev else {"available": False},
            }
        )
    return {"available": any(r.get("previous") for r in rows), "symbols": rows}
