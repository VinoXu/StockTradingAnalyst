"""Debug LLM context size for a chat session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.chat_history import get_session_summary, load_session_raw_turns
from modules.chat_service import _build_llm_messages, _compose_user_message
from modules.conversation_memory import build_memory_block
from modules.llm import build_chat_system_prompt
from modules.query_planner import compact_payload_for_llm, fetch_data_for_plan, format_fetch_block, plan_query
from modules.skill_loader import load_all_skills
from modules.skill_mapper import select_skills_for_plan, skills_summary


def analyze_session(session_id: int, *, turn_index: int | None = None) -> None:
    turns = load_session_raw_turns(session_id)
    print(f"Session {session_id}: {len(turns)} turns in DB")
    for i, (q, a) in enumerate(turns, 1):
        print(f"  Q{i} ({len(q)} chars): {q[:120]}")

    skills = load_all_skills()
    print(f"\nAll skills total: {sum(len(v) for v in skills.values()):,} chars ({len(skills)} files)")

    summary = get_session_summary(session_id)
    idx = (turn_index or len(turns)) - 1
    if idx < 0 or idx >= len(turns):
        print("No turn to simulate")
        return

    q, _a = turns[idx]
    plan = plan_query(q, [])
    skill_names = select_skills_for_plan(plan, scope="open")
    selected_chars = sum(len(skills.get(n, "")) for n in skill_names)
    print(f"\nSelected skills ({len(skill_names)}): {skills_summary(skill_names)}")
    print(f"Selected skill chars: {selected_chars:,} (saved {sum(len(v) for v in skills.values()) - selected_chars:,})")

    fetched = fetch_data_for_plan(plan, None)
    compact = compact_payload_for_llm(fetched)
    raw_json_len = len(json.dumps(fetched, ensure_ascii=False, default=str))
    print(f"Fetch block (compact): {len(format_fetch_block(fetched)):,} chars")
    print(f"Fetch payload (raw dict): {raw_json_len:,} chars")

    prior = turns[:idx]
    mem, _ = build_memory_block(prior, session_summary=summary if idx else "")
    messages = _build_llm_messages(
        effective=[],
        plan=plan,
        memory_block=mem,
        fetched=fetched,
        message=q,
    )
    total = sum(len(m["content"]) for m in messages)
    print(f"\n=== LLM payload (turn {idx + 1}): {total:,} chars (limit ~98304) ===")
    for i, m in enumerate(messages):
        print(f"  [{i}] {m['role']:10} {len(m['content']):>8,} chars")


if __name__ == "__main__":
    sid = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    turn = int(sys.argv[2]) if len(sys.argv) > 2 else None
    analyze_session(sid, turn_index=turn)
