"""Parse and format short/medium-term outlook from LLM replies."""

from __future__ import annotations

import re
from typing import Any

OUTLOOK_SECTION_TITLE = "【观点结论】"

# Direction workflows that must split short vs medium horizon.
_DUAL_HORIZON_WORKFLOWS = frozenset(
    {
        "market_overview",
        "named_symbols",
        "symbol_research",
        "sector_deep_dive",
        "sector_research",
        "opportunity_scan",
        "risk_scan",
        "capital_flow",
        "portfolio_review",
        "question_deep_dive",
        "situation_advice",
        "ta_screen",
    }
)

OUTLOOK_INSTRUCTION = """
【短线/中期分层·必遵】凡涉及方向、大盘、板块或个股态度，必须分开写，禁止混成一句盖掉两层。

开篇先输出唯一允许的框架块（供界面卡片，其它【小标题】仍禁止）：
【观点结论】
短期（1～3个交易日）：必须写直白方向，优先用「更可能延续上涨」「更可能回吐」「更可能震荡整理」之一（可加半句理由）
中期（1～2周）：偏多观察 / 偏空观察 / 观望 之一

随后用连贯口语展开（不要再套其它【小标题】）：
1. 短线要敢下方向判断：明确说更可能涨、回吐还是震荡；再写参与含义与 1 条失效条件。禁止短线也只用「偏多观察」搪塞。
2. 中期：峰谷结构、均线、道氏/广度；结构未改时可以说「中期仍偏空/偏多」，即使短线看涨。
3. 短线与中期可以相反，但必须一句话说清为何（例如：中期下降结构未破，短线更可能延续反弹）。
中期立场用偏多观察 / 偏空观察 / 观望 / 降权；禁止保证收益、禁止「必涨/必跌」与具体下单价位。
"""

GUIDANCE_CLOSING_INSTRUCTION = """
（已由 analysis_mandate 按 workflow 动态生成，此处保留兼容。）
"""


def needs_dual_horizon(fetched: dict[str, Any]) -> bool:
    """Whether this turn should force short vs medium split in the reply."""
    workflow = fetched.get("workflow") or ""
    if workflow in _DUAL_HORIZON_WORKFLOWS:
        return True
    if fetched.get("portfolio_focus") or fetched.get("symbols"):
        return True
    intents = set(fetched.get("query_intents") or [])
    if intents & {"tech", "market", "sector", "opportunity", "risk", "price"}:
        return True
    return False

_BIAS_BULLISH = (
    "偏多",
    "看涨",
    "看多",
    "上行",
    "延续上涨",
    "继续上涨",
    "延续反弹",
    "继续反弹",
    "偏涨",
)
_BIAS_BEARISH = (
    "偏空",
    "看跌",
    "看空",
    "下行",
    "回吐",
    "回落",
    "延续下跌",
    "继续下跌",
    "偏跌",
)
_BIAS_NEUTRAL = ("观望", "中性", "震荡", "盘整", "持平", "震荡整理")

_SHORT_DIRECTION_PHRASES = (
    "更可能延续上涨",
    "更可能继续上涨",
    "更可能延续反弹",
    "更可能继续反弹",
    "更可能回吐",
    "更可能回落",
    "更可能延续下跌",
    "更可能继续下跌",
    "更可能震荡整理",
    "更可能震荡",
)

_SHORT_RE = re.compile(r"^短期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+)$")
_MED_RE = re.compile(r"^中期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+)$")
# Inline / humanize-collapsed: 「【观点结论】；短期…；中期…」or same line
_SHORT_INLINE_RE = re.compile(
    r"短期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+?)(?=\s*[；;。]?\s*中期[（(]|$)"
)
_MED_INLINE_RE = re.compile(
    r"中期[（(]?[^）):：\n]{0,24}[）)]?\s*[:：]\s*(.+)$"
)


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


def _clean_short_bias_text(text: str) -> str:
    """Keep aggressive short-term direction phrases for UI chips."""
    t = (text or "").strip().rstrip("。；;")
    if not t:
        return "更可能震荡整理"
    for phrase in _SHORT_DIRECTION_PHRASES:
        if phrase in t:
            # Keep phrase + optional brief reason, capped for chip display
            return t[:36]
    # Soft stance → map to direct short phrasing for display consistency
    if any(w in t for w in ("偏多", "看涨", "看多", "上行")):
        return "更可能延续上涨"
    if any(w in t for w in ("偏空", "看跌", "看空", "下行", "回吐", "回落")):
        return "更可能回吐"
    if any(w in t for w in ("观望", "震荡", "盘整", "中性")):
        return "更可能震荡整理"
    return t[:36]


def _clean_bias_text(text: str) -> str:
    """Medium-term stance labels stay compact."""
    t = (text or "").strip().rstrip("。；;")
    for word in ("偏多观察", "偏空观察", "观望"):
        if word in t:
            return word
    for word in ("偏多", "偏空"):
        if word in t:
            return word + "观察"
    return t[:24] if t else "观望"


def expand_collapsed_outlook(text: str) -> str:
    """Undo humanize/model collapsing of outlook into one semicolon line."""
    if not text or "短期" not in text or "中期" not in text:
        return text
    t = text.replace("\r\n", "\n")
    # 【观点结论】；短期… → title on its own line
    t = re.sub(rf"({re.escape(OUTLOOK_SECTION_TITLE)})\s*[；;]?\s*", r"\1\n", t)
    # …；短期（ → newline before 短期 (when not already at line start)
    t = re.sub(r"[；;]\s*(短期[（(])", r"\n\1", t)
    # …。中期（ or ；中期（ → newline before 中期
    t = re.sub(r"[；;。]\s*(中期[（(])", r"\n\1", t)
    # Same-line without separator: 短期…中期
    t = re.sub(
        r"(短期[（(][^）)]{0,24}[）)]?\s*[:：][^\n]*?)(\s+)(中期[（(])",
        r"\1\n\3",
        t,
    )
    return t


def _split_outlook_lines(chunk: str) -> list[str]:
    """Split a chunk into logical outlook lines (handles collapsed single line)."""
    expanded = expand_collapsed_outlook(chunk)
    lines = [ln.strip().lstrip("；;").strip() for ln in expanded.split("\n") if ln.strip()]
    # If still one line containing both horizons, force-split by regex
    if len(lines) == 1 and "短期" in lines[0] and "中期" in lines[0]:
        one = lines[0]
        m_short = _SHORT_INLINE_RE.search(one)
        m_med = _MED_INLINE_RE.search(one)
        out: list[str] = []
        if m_short:
            prefix = one[: m_short.start()].strip(" ；;")
            if prefix and prefix != OUTLOOK_SECTION_TITLE:
                out.append(prefix)
            out.append(f"短期（1～3个交易日）：{m_short.group(1).strip().rstrip('。；;')}")
        if m_med:
            out.append(f"中期（1～2周）：{m_med.group(1).strip().rstrip('。；;')}")
        if out:
            return out
    return lines


def _outlook_item(label: str, short_text: str, med_text: str) -> dict[str, str] | None:
    if not short_text and not med_text:
        return None
    return {
        "label": label or "综合",
        "short_text": _clean_short_bias_text(short_text),
        "short_bias": _classify_bias(short_text),
        "medium_text": _clean_bias_text(med_text) or "观望",
        "medium_bias": _classify_bias(med_text),
    }


def _parse_outlook_block(lines: list[str]) -> dict[str, str] | None:
    label = "综合"
    short_text = ""
    med_text = ""
    for line in lines:
        line = (line or "").strip().lstrip("；;").strip()
        if not line or line == OUTLOOK_SECTION_TITLE:
            continue
        if "短期" in line and "中期" in line:
            ms = _SHORT_INLINE_RE.search(line)
            mm = _MED_INLINE_RE.search(line)
            if ms:
                short_text = ms.group(1).strip().rstrip("。；;")
            if mm:
                med_text = mm.group(1).strip().rstrip("。；;")
            prefix = line[: ms.start()].strip(" ；;") if ms else ""
            if prefix and prefix != OUTLOOK_SECTION_TITLE:
                label = prefix
            continue
        m_short = _SHORT_RE.match(line)
        m_med = _MED_RE.match(line)
        if m_short:
            short_text = m_short.group(1).strip()
        elif m_med:
            med_text = m_med.group(1).strip()
        elif not short_text and not med_text:
            label = line
    return _outlook_item(label, short_text, med_text)


def _extract_outlook_from_lines(lines: list[str]) -> dict[str, str] | None:
    return _parse_outlook_block(lines)


def parse_outlook(text: str) -> tuple[list[dict[str, str]], str]:
    """Extract outlook cards from reply body; return (items, body_without_outlook_block)."""
    if not text:
        return [], text

    text = expand_collapsed_outlook(text)

    if OUTLOOK_SECTION_TITLE in text:
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
                lines = _split_outlook_lines(chunk)
                parsed = _parse_outlook_block(lines) or _extract_outlook_from_lines(lines)
                if parsed:
                    items.append(parsed)
                    # 指令要求唯一【观点结论】块；后续重复块（常为 humanize 残留）忽略
                    continue
            if items:
                # Already captured the outlook card; remaining chunks are body
                body_parts.append(chunk)
                continue
            body_parts.append(chunk)

        if items:
            items = items[:1]
            after = "\n\n".join(body_parts).strip()
            cleaned = "\n\n".join(p for p in (before, after) if p).strip()
            return items, cleaned or text

    # Fallback: model wrote 短期/中期 lines without the title block
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    head = lines[:12]
    if any("短期" in ln for ln in head) and any("中期" in ln for ln in head):
        # If head lines are still collapsed, expand each
        expanded_head: list[str] = []
        for ln in head:
            expanded_head.extend(_split_outlook_lines(ln) if ("短期" in ln and "中期" in ln) else [ln])
        parsed = _extract_outlook_from_lines(expanded_head)
        if parsed:
            kept = [
                ln
                for ln in lines
                if not _SHORT_RE.match(ln) and not _MED_RE.match(ln)
            ]
            # Drop a lone label line that was consumed as outlook label
            if kept and kept[0] == parsed.get("label") and parsed.get("label") != "综合":
                kept = kept[1:]
            # Drop leftover collapsed title line
            kept = [ln for ln in kept if not ln.startswith(OUTLOOK_SECTION_TITLE)]
            return [parsed], "\n".join(kept).strip() or text

    return [], text


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
