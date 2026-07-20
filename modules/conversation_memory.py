"""Conversation history compression for multi-turn chat."""

from __future__ import annotations

import logging
import re
from typing import Any

# (question, answer) pairs before the current turn
Turn = tuple[str, str]

MAX_SUMMARY_CHARS = 400
SUMMARY_INPUT_ANSWER_CHARS = 800
MEMORY_BLOCK_LIMIT = 800

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = """你是投研对话归档助手。根据多轮问答，提炼本 session 关键记忆，供后续轮次引用。
要求：
1. 300字以内，口语化中文，一段话说完
2. 保留：用户关注的标的/板块、已讨论结论、尚未解决的问题
3. 禁止 Markdown；禁止编造对话中未出现的信息
4. 只有一轮时，一句话概括即可"""

# 明示承接上文的话语标记（短句/代词追问也算）
_FOLLOWUP_MARKERS = (
    "那",
    "那么",
    "所以",
    "刚才",
    "上面",
    "上文",
    "继续",
    "还有",
    "基于此",
    "基于你",
    "你刚",
    "你说的",
    "同上",
    "接着",
    "进一步",
    "补充一下",
    "另外呢",
    "为什么会这样",
    "原因是什么",
    "这些",
    "那些",
    "这个呢",
    "那个呢",
    "呢？",
    "呢?",
)

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_TOPIC_HINTS = (
    "大盘",
    "市场",
    "指数",
    "上证",
    "深证",
    "沪深",
    "两市",
    "板块",
    "行业",
    "概念",
    "题材",
    "牛市",
    "熊市",
    "回调",
    "反弹",
    "资金",
    "北向",
    "主力",
)


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


def _is_discourse_followup(message: str) -> bool:
    msg = (message or "").strip()
    if not msg:
        return False
    if any(m in msg for m in _FOLLOWUP_MARKERS):
        return True
    # 极短追问（如「为什么？」「会回来吗」）默认视为承接上一轮
    if len(msg) <= 12 and ("？" in msg or "?" in msg or "吗" in msg or "呢" in msg):
        return True
    return False


def _extract_codes(text: str) -> set[str]:
    return set(_CODE_RE.findall(text or ""))


def _extract_topics(text: str) -> set[str]:
    t = text or ""
    return {h for h in _TOPIC_HINTS if h in t}


def _chinese_phrases(text: str, *, min_len: int = 2, max_len: int = 8) -> set[str]:
    """从文本抽出中文片段（含短窗），便于个股/板块名粗匹配。"""
    out: set[str] = set()
    for m in re.finditer(r"[\u4e00-\u9fff]{2,}", text or ""):
        s = m.group(0)
        out.add(s if len(s) <= max_len else s[:max_len])
        upper = min(max_len, len(s))
        for n in range(min_len, upper + 1):
            for i in range(0, len(s) - n + 1):
                out.add(s[i : i + n])
    return out


def _turn_entities(q: str, a: str) -> set[str]:
    blob = f"{q}\n{a}"
    return _extract_codes(blob) | _extract_topics(blob) | _chinese_phrases(q)


def _related_turn_indices(message: str, prior_turns: list[Turn]) -> list[int]:
    """选出与本轮问题实体/主题有交集的历史轮次（最多近 3 轮）。"""
    msg_codes = _extract_codes(message)
    msg_topics = _extract_topics(message)
    msg_phrases = {
        p
        for p in _chinese_phrases(message)
        if len(p) >= 3
        and p
        not in (
            "怎么样",
            "为什么",
            "怎么看",
            "能不能",
            "可以吗",
            "是什么",
            "有哪些",
            "风险点",
            "展开说",
        )
    }
    hits: list[int] = []
    for i, (q, a) in enumerate(prior_turns):
        prior_blob = f"{q}\n{_strip_time_banner(a)}"
        ents = _turn_entities(q, a)
        if msg_codes & ents:
            hits.append(i)
            continue
        if msg_topics & _extract_topics(prior_blob):
            hits.append(i)
            continue
        # 名称：本轮短语出现在历史问答中，或与历史问题短语交集
        prior_phrases = {p for p in _chinese_phrases(q) if len(p) >= 3}
        if msg_phrases & prior_phrases:
            hits.append(i)
            continue
        if any(p in prior_blob for p in msg_phrases if len(p) >= 3):
            hits.append(i)
            continue
    if len(hits) > 3:
        hits = hits[-3:]
    return hits


def assess_memory_relevance(
    message: str,
    prior_turns: list[Turn],
) -> dict[str, Any]:
    """
    判断本轮是否应合并历史：
    - 无历史 → 不合并
    - 像追问/承接 → 合并最近相关轮（无实体命中则取最近 1～2 轮）
    - 有实体/主题交集 → 只合并命中轮
    - 否则 → 不合并（新话题独立作答）
    """
    if not prior_turns:
        return {
            "related": False,
            "reason": "no_prior",
            "indices": [],
            "followup": False,
        }

    followup = _is_discourse_followup(message)
    indices = _related_turn_indices(message, prior_turns)

    if followup and not indices:
        # 承接上文但未点名实体：默认接最近一轮，必要时再带上一轮
        indices = [len(prior_turns) - 1]
        if len(prior_turns) >= 2 and len((message or "").strip()) <= 24:
            indices = [len(prior_turns) - 2, len(prior_turns) - 1]

    if indices:
        return {
            "related": True,
            "reason": "followup" if followup else "entity_overlap",
            "indices": indices,
            "followup": followup,
        }

    return {
        "related": False,
        "reason": "unrelated_new_topic",
        "indices": [],
        "followup": False,
    }


def _format_related_memory(prior_turns: list[Turn], indices: list[int]) -> str:
    parts: list[str] = []
    for i in indices:
        if i < 0 or i >= len(prior_turns):
            continue
        q, a = prior_turns[i]
        parts.append(
            f"第{i + 1}轮「{_brief(q, 48)}」→{_brief(_strip_time_banner(a), 140)}"
        )
    return "；".join(parts)


def build_memory_block(
    prior_turns: list[Turn],
    session_summary: str = "",
    *,
    current_message: str = "",
) -> tuple[str, bool]:
    """
    注入本轮可用的对话记忆。
    若提供 current_message：先做相关性判断——无关则返回空；有关则只合并关联轮次。
    未提供 current_message（兼容旧调用）：仍用整段 session_summary。
    """
    if current_message:
        decision = assess_memory_relevance(current_message, prior_turns)
        if not decision["related"]:
            return "", False
        related_text = _format_related_memory(prior_turns, list(decision["indices"]))
        if not related_text:
            return "", False
        block = (
            "【对话记忆·仅关联部分】\n"
            "已判定本轮与上文有关联；以下只含相关轮次，用于指代消解与承接，"
            "禁止写成「上次咱们说到」式接龙，也禁止把无关旧题硬扯进本轮。\n"
            f"关联背景：{related_text}"
        )
    else:
        summary = (session_summary or "").strip()
        if not summary:
            return "", False
        block = (
            "【对话记忆·背景备忘·勿写成接龙复盘】\n"
            "以下只是本会话背景，用户未必记得细节；请把本轮写成一份独立、完整、自洽的答复，"
            "不要用「上次咱们说到」之类口吻。\n"
            f"历史提炼：{summary}"
        )

    was_truncated = len(block) > MEMORY_BLOCK_LIMIT
    if was_truncated:
        block = block[:MEMORY_BLOCK_LIMIT] + "…（记忆已截断）"
    return block, was_truncated


NEW_CHAT_HINT = "本对话上下文已达上限，请点击「新建对话」开启新窗口后再继续提问。"


def _session_raw_chars(prior_turns: list[Turn], session_summary: str = "") -> int:
    return len((session_summary or "").strip())


def assess_context_limit(
    prior_turns: list[Turn],
    session_summary: str = "",
) -> dict[str, int | bool | str | None]:
    """Check whether session summary memory is too large."""
    if not prior_turns:
        return {
            "context_full": False,
            "context_chars": 0,
            "context_limit": MEMORY_BLOCK_LIMIT,
            "new_chat_hint": None,
        }

    raw_chars = _session_raw_chars(prior_turns, session_summary)
    _block, was_truncated = build_memory_block(prior_turns, session_summary=session_summary)
    context_full = was_truncated or raw_chars >= MEMORY_BLOCK_LIMIT
    return {
        "context_full": context_full,
        "context_chars": raw_chars,
        "context_limit": MEMORY_BLOCK_LIMIT,
        "new_chat_hint": NEW_CHAT_HINT if context_full else None,
    }
