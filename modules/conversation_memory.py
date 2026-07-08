"""Conversation history compression for multi-turn chat."""

from __future__ import annotations

import logging

# (question, answer) pairs before the current turn
Turn = tuple[str, str]

MAX_VERBATIM_TURNS = 3
MAX_ANSWER_CHARS = 600
MAX_TOTAL_CHARS = 5000
MAX_SUMMARY_CHARS = 400
SUMMARY_INPUT_ANSWER_CHARS = 800

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = """你是投研对话归档助手。根据多轮问答，提炼本 session 关键记忆，供后续轮次引用。
要求：
1. 300字以内，口语化中文，一段话说完
2. 保留：用户关注的标的/板块、已讨论结论、尚未解决的问题
3. 禁止 Markdown；禁止编造对话中未出现的信息
4. 只有一轮时，一句话概括即可"""


def extract_prior_turns(messages: list[dict[str, str]]) -> list[Turn]:
    """Extract Q/A pairs from session messages, skipping system + session opener."""
    turns: list[Turn] = []
    i = 0
    while i < len(messages):
        role = messages[i].get("role")
        if role == "system":
            i += 1
            continue
        if role == "user":
            content = (messages[i].get("content") or "").strip()
            if i == 1 and ("请用对话体" in content or "用户已勾选分析标的" in content):
                i += 1
                continue
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                ans = (messages[i + 1].get("content") or "").strip()
                turns.append((content, ans))
                i += 2
                continue
        i += 1
    return turns


def _brief(text: str, limit: int = 120) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _strip_time_banner(text: str) -> str:
    idx = text.find("\n\n")
    if idx > 0 and "📅" in text[:idx]:
        return text[idx + 2 :].strip()
    return text.strip()


def _rule_summarize_old(turns: list[Turn]) -> str:
    if not turns:
        return ""
    parts = []
    for q, a in turns:
        parts.append(f"曾问「{_brief(q, 40)}」，当时要点：{_brief(_strip_time_banner(a), 80)}")
    return "；".join(parts)


def build_session_summary_rule(prior_turns: list[Turn]) -> str:
    """Rule-based fallback when LLM summary is unavailable."""
    if not prior_turns:
        return ""
    if len(prior_turns) == 1:
        q, a = prior_turns[0]
        return f"用户关注：{_brief(q, 80)}；要点：{_brief(_strip_time_banner(a), 220)}"
    older = prior_turns[:-2] if len(prior_turns) > 2 else []
    recent = prior_turns[-2:]
    parts = [f"共 {len(prior_turns)} 轮"]
    if older:
        parts.append(f"早期：{_rule_summarize_old(older)}")
    recent_text = "；".join(
        f"「{_brief(q, 36)}」→{_brief(_strip_time_banner(a), 100)}" for q, a in recent
    )
    parts.append(f"近期：{recent_text}")
    return "。".join(parts)


def _format_turns_for_summary(turns: list[Turn]) -> str:
    lines: list[str] = []
    for i, (q, a) in enumerate(turns, 1):
        body = _strip_time_banner(a)
        if len(body) > SUMMARY_INPUT_ANSWER_CHARS:
            body = body[: SUMMARY_INPUT_ANSWER_CHARS] + "…"
        lines.append(f"第{i}轮\n用户：{q}\n助手：{body}")
    return "\n\n".join(lines)


def build_session_summary_llm(
    prior_turns: list[Turn],
    *,
    previous_summary: str = "",
) -> str:
    """LLM secondary summarization for session memory."""
    from modules.llm import generate, llm_available

    if not prior_turns or not llm_available(timeout=3.0):
        return build_session_summary_rule(prior_turns)

    prev = (previous_summary or "").strip()
    try:
        if prev and len(prior_turns) >= 2:
            q, a = prior_turns[-1]
            body = _strip_time_banner(a)
            if len(body) > SUMMARY_INPUT_ANSWER_CHARS:
                body = body[:SUMMARY_INPUT_ANSWER_CHARS] + "…"
            prompt = (
                f"已有对话摘要：\n{prev}\n\n"
                "请合并以下新增一轮后，输出更新后的完整摘要：\n"
                f"用户：{q}\n助手：{body}"
            )
        else:
            prompt = f"请提炼以下对话：\n\n{_format_turns_for_summary(prior_turns)}"

        text = generate(prompt, system=SUMMARY_SYSTEM, temperature=0.2, timeout=90.0)
        text = " ".join((text or "").split())
        if not text:
            raise RuntimeError("empty summary")
        if len(text) > MAX_SUMMARY_CHARS:
            text = text[: MAX_SUMMARY_CHARS - 1] + "…"
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM session summary failed, using rule fallback: %s", exc)
        return build_session_summary_rule(prior_turns)


def build_session_summary(
    prior_turns: list[Turn],
    *,
    previous_summary: str = "",
) -> str:
    """Persisted session summary: LLM first, rule-based fallback."""
    if not prior_turns:
        return ""
    return build_session_summary_llm(prior_turns, previous_summary=previous_summary)


def build_memory_block(
    prior_turns: list[Turn],
    session_summary: str = "",
) -> tuple[str, bool]:
    """Format prior dialogue for injection; returns (block, was_truncated)."""
    summary = (session_summary or "").strip()
    if not prior_turns and not summary:
        return "", False

    verbatim_n = 2 if summary else MAX_VERBATIM_TURNS
    recent = prior_turns[-verbatim_n:] if prior_turns else []

    lines = ["【对话记忆】"]
    if summary:
        lines.append(f"本 session 历史提炼：{summary}")
    if recent:
        label = "最近往来：" if summary else "以下是本 session 内更早的往来："
        lines.append(label)
        for q, a in recent:
            body = _strip_time_banner(a)
            a_short = body if len(body) <= MAX_ANSWER_CHARS else body[:MAX_ANSWER_CHARS] + "…"
            lines.append(f"用户：{q}")
            lines.append(f"助手：{a_short}")
    elif not summary and len(prior_turns) > verbatim_n:
        old = prior_turns[:-verbatim_n]
        lines.append(f"更早对话摘要：{_rule_summarize_old(old)}")

    block = "\n".join(lines)
    was_truncated = len(block) > MAX_TOTAL_CHARS
    if was_truncated:
        block = block[:MAX_TOTAL_CHARS] + "…（记忆已截断）"
    return block, was_truncated


NEW_CHAT_HINT = "本对话上下文已达上限，请点击「新建对话」开启新窗口后再继续提问。"


def _session_raw_chars(prior_turns: list[Turn], session_summary: str = "") -> int:
    """Estimate cumulative session footprint (what must be remembered across turns)."""
    total = len((session_summary or "").strip())
    for q, a in prior_turns:
        body = _strip_time_banner(a)
        total += len(q) + min(len(body), MAX_ANSWER_CHARS)
    return total


def assess_context_limit(
    prior_turns: list[Turn],
    session_summary: str = "",
) -> dict[str, int | bool | str | None]:
    """Check whether session context has hit the memory cap."""
    if not prior_turns:
        return {
            "context_full": False,
            "context_chars": 0,
            "context_limit": MAX_TOTAL_CHARS,
            "new_chat_hint": None,
        }

    raw_chars = _session_raw_chars(prior_turns, session_summary)
    block, was_truncated = build_memory_block(prior_turns, session_summary=session_summary)
    context_full = was_truncated or raw_chars >= MAX_TOTAL_CHARS
    return {
        "context_full": context_full,
        "context_chars": raw_chars,
        "context_limit": MAX_TOTAL_CHARS,
        "new_chat_hint": NEW_CHAT_HINT if context_full else None,
    }
