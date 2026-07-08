"""Persist chat sessions and multi-turn messages (30-day retention)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from modules.conversation_memory import build_session_summary
from modules.db import get_connection, init_db

RETENTION_DAYS = 30
ACTIVE_SESSION_KEY = "active_chat_session_id"
DEFAULT_TITLE = "新对话"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_state(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
        (key, value),
    )


def _fmt_display(iso: str) -> str:
    try:
        text = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16] if len(iso) >= 16 else iso


def _split_assistant_reply(answer: str) -> tuple[str | None, str]:
    if not answer:
        return None, answer or ""
    idx = answer.find("\n\n")
    if idx > 0 and "📅" in answer[:idx]:
        return answer[:idx], answer[idx + 2 :]
    return None, answer


def _title_from_question(question: str) -> str:
    q = " ".join(question.split())
    if len(q) <= 48:
        return q or DEFAULT_TITLE
    return q[:47] + "…"


def purge_old() -> int:
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM chat_messages WHERE created_at < ?", (cutoff,))
        conn.execute(
            """
            DELETE FROM chat_sessions
            WHERE updated_at < ?
            AND id NOT IN (
                SELECT DISTINCT session_id FROM chat_messages WHERE session_id IS NOT NULL
            )
            """,
            (cutoff,),
        )
        active = _get_state(conn, ACTIVE_SESSION_KEY)
        if active:
            exists = conn.execute(
                "SELECT id FROM chat_sessions WHERE id = ?", (int(active),)
            ).fetchone()
            if not exists:
                _set_state(conn, ACTIVE_SESSION_KEY, "")
        conn.commit()
        return cur.rowcount


def clear_all() -> None:
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_sessions")
        _set_state(conn, ACTIVE_SESSION_KEY, "")
        conn.commit()


def get_active_session_id() -> int | None:
    init_db()
    with get_connection() as conn:
        raw = _get_state(conn, ACTIVE_SESSION_KEY)
        if not raw:
            return None
        try:
            sid = int(raw)
        except ValueError:
            return None
        row = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
        return row["id"] if row else None


def set_active_session(session_id: int) -> None:
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise ValueError("session not found")
        _set_state(conn, ACTIVE_SESSION_KEY, str(session_id))
        conn.commit()


def create_session(title: str = DEFAULT_TITLE) -> int:
    init_db()
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (title, summary, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, "", now, now),
        )
        sid = int(cur.lastrowid)
        _set_state(conn, ACTIVE_SESSION_KEY, str(sid))
        conn.commit()
    return sid


def ensure_active_session() -> int:
    active = get_active_session_id()
    if active is not None:
        return active
    return create_session()


def get_session_summary(session_id: int) -> str:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT summary FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return ""
    return (row["summary"] or "").strip()


def load_session_raw_turns(session_id: int) -> list[tuple[str, str]]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM chat_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    turns: list[tuple[str, str]] = []
    i = 0
    while i < len(rows) - 1:
        a, b = rows[i], rows[i + 1]
        if a["role"] == "user" and b["role"] == "assistant":
            turns.append((a["content"], b["content"]))
            i += 2
        else:
            i += 1
    return turns


def load_session_ui_turns(session_id: int) -> list[dict]:
    from modules.outlook_format import parse_outlook

    turns: list[dict] = []
    for question, answer in load_session_raw_turns(session_id):
        time_banner, raw_body = _split_assistant_reply(answer)
        outlook, body = parse_outlook(raw_body)
        turns.append({"role": "user", "content": question})
        turns.append(
            {
                "role": "assistant",
                "content": body,
                "timeBanner": time_banner,
                "fullReply": answer,
                "charts": [],
                "outlook": outlook,
            }
        )
    return turns


def update_session_summary(session_id: int) -> None:
    turns = load_session_raw_turns(session_id)
    previous = get_session_summary(session_id)
    summary = build_session_summary(turns, previous_summary=previous)
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, now, session_id),
        )
        conn.commit()


def append_turn(session_id: int, user_msg: str, assistant_msg: str) -> None:
    """Atomically append one Q/A pair to a session."""
    init_db()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "user", user_msg, now),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "assistant", assistant_msg, now),
        )
        row = conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and row["title"] in (DEFAULT_TITLE, "历史对话"):
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE id = ?",
                (_title_from_question(user_msg), session_id),
            )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        conn.commit()
    update_session_summary(session_id)


def load_sessions() -> list[dict[str, str | int]]:
    init_db()
    purge_old()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.title,
                s.summary,
                s.created_at,
                s.updated_at,
                (
                    SELECT COUNT(*) FROM chat_messages m
                    WHERE m.session_id = s.id AND m.role = 'user'
                ) AS turn_count
            FROM chat_sessions s
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
    items: list[dict[str, str | int]] = []
    for row in rows:
        summary = (row["summary"] or "").strip()
        preview = summary[:80] + ("…" if len(summary) > 80 else "") if summary else row["title"]
        items.append(
            {
                "id": str(row["id"]),
                "title": row["title"],
                "preview": preview,
                "summary": summary,
                "turn_count": int(row["turn_count"] or 0),
                "created_at": _fmt_display(row["created_at"]),
                "updated_at": _fmt_display(row["updated_at"]),
            }
        )
    return items


def delete_session(session_id: int) -> bool:
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        active = _get_state(conn, ACTIVE_SESSION_KEY)
        if active == str(session_id):
            _set_state(conn, ACTIVE_SESSION_KEY, "")
        conn.commit()
    return True


def load_messages() -> list[dict[str, str]]:
    """All messages in active session (legacy helper)."""
    sid = ensure_active_session()
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM chat_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (sid,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]
