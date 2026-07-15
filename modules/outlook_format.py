"""Parse and format short/medium-term outlook from LLM replies."""

from __future__ import annotations

import re
from typing import Any

OUTLOOK_SECTION_TITLE = "【观点结论】"

OUTLOOK_INSTRUCTION = """
涉及具体个股/ETF 时：开篇用白话写清短期（约 1～3 个交易日）与中期（约 1～2 周）的态度，
各选偏多观察 / 偏空观察 / 观望之一。先给结论，再自然展开依据；不要套【小标题】框架。
大盘或纯板块问题同样先结论后依据，可用连贯段落，不必生硬分栏。
"""

GUIDANCE_CLOSING_INSTRUCTION = """
（已由 analysis_mandate 按 workflow 动态生成，此处保留兼容。）
"""

_BIAS_BULLISH = ("偏多", "看涨", "看多", "上行")
_BIAS_BEARISH = ("偏空", "看跌", "看空", "下行")
_BIAS_NEUTRAL = ("观望", "中性", "震荡", "盘整", "持平")

_SHORT_RE = re.compile(r"^短期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+)$")
_MED_RE = re.compile(r"^中期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+)$")


def _classify_bias(text: str) -> str:
    t = (text or "").strip()
    for w in _BIAS_BULLISH:
        if w in t:
            return "bullish"
    for w in _BIAS_BEARISH:
        if w in t:
            return "bearish"
    for w in _BIAS_NEUTRAL:
        if w in t:
            return "neutral"
    return "neutral"


def _clean_bias_text(text: str) -> str:
    t = (text or "").strip().rstrip("。；;")
    for word in ("偏多观察", "偏空观察", "观望"):
        if word in t:
            return word
    for word in ("偏多", "偏空"):
        if word in t:
            return word + "观察"
    return t[:24] if t else "观望"


def _parse_outlook_block(lines: list[str]) -> dict[str, str] | None:
    label = "综合"
    short_text = ""
    med_text = ""
    for line in lines:
        m_short = _SHORT_RE.match(line)
        m_med = _MED_RE.match(line)
        if m_short:
            short_text = m_short.group(1).strip()
        elif m_med:
            med_text = m_med.group(1).strip()
        elif not short_text and not med_text:
            label = line
    if not short_text and not med_text:
        return None
    return {
        "label": label,
        "short_text": _clean_bias_text(short_text) or "观望",
        "short_bias": _classify_bias(short_text),
        "medium_text": _clean_bias_text(med_text) or "观望",
        "medium_bias": _classify_bias(med_text),
    }


def parse_outlook(text: str) -> tuple[list[dict[str, str]], str]:
    """Extract outlook cards from reply body; return (items, body_without_outlook_block)."""
    if not text or OUTLOOK_SECTION_TITLE not in text:
        return [], text

    start = text.find(OUTLOOK_SECTION_TITLE)
    before = text[:start].rstrip()
    rest = text[start + len(OUTLOOK_SECTION_TITLE) :].strip()

    parts = re.split(r"\n\s*\n", rest)
    items: list[dict[str, str]] = []
    body_parts: list[str] = []

    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        if "短期" in chunk and "中期" in chunk:
            lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
            parsed = _parse_outlook_block(lines)
            if parsed:
                items.append(parsed)
                continue
        body_parts.append(chunk)

    if not items:
        return [], text

    after = "\n\n".join(body_parts).strip()
    cleaned = "\n\n".join(p for p in (before, after) if p).strip()
    return items, cleaned or text


def resolve_guidance_sector_label(fetched: dict[str, Any]) -> str:
    """Pick display label for closing guidance (replace 科技 placeholder)."""
    matched = [m for m in (fetched.get("matched_sectors") or []) if m not in ("板块", "行业", "主线", "龙头", "题材", "概念")]
    if matched:
        return matched[0]

    names: list[str] = []
    for s in fetched.get("symbols") or []:
        if not s.get("available"):
            continue
        name = (s.get("name") or "").strip()
        code = (s.get("symbol") or "").split(".")[0]
        if name and name != code:
            names.append(name)
    if names:
        if len(names) == 1:
            return names[0]
        return "、".join(names[:2]) + "等标的"

    workflow = fetched.get("workflow") or ""
    if workflow in ("sector_deep_dive", "opportunity_scan", "risk_scan"):
        return "相关主线板块"
    if workflow == "market_overview":
        return "市场主线"
    return "本轮分析板块"


def build_guidance_instruction(fetched: dict[str, Any]) -> str:
    from modules.analysis_mandate import build_closing_guidance

    return build_closing_guidance(fetched)
