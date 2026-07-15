"""LLM input size preflight and progressive trimming."""

from __future__ import annotations

import os

from modules.env_loader import load_env

load_env()

DEFAULT_MAX_CHARS = 90_000
DEFAULT_WARN_CHARS = 75_000

PAYLOAD_BLOCKED_HINT = (
    "本轮分析数据量过大，已超过模型输入上限。"
    "请新建对话、缩小提问范围，或只勾选少量标的后再试。"
)


def llm_input_limits() -> tuple[int, int]:
    try:
        max_chars = int(os.environ.get("LLM_MAX_INPUT_CHARS", str(DEFAULT_MAX_CHARS)))
    except ValueError:
        max_chars = DEFAULT_MAX_CHARS
    try:
        warn_chars = int(os.environ.get("LLM_WARN_INPUT_CHARS", str(DEFAULT_WARN_CHARS)))
    except ValueError:
        warn_chars = DEFAULT_WARN_CHARS
    return max(10_000, max_chars), max(5_000, warn_chars)


def estimate_payload_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def assess_payload(messages: list[dict[str, str]]) -> dict[str, int | bool | str | None]:
    chars = estimate_payload_chars(messages)
    max_chars, warn_chars = llm_input_limits()
    blocked = chars >= max_chars
    return {
        "payload_chars": chars,
        "payload_limit": max_chars,
        "payload_warn": warn_chars,
        "payload_warned": chars >= warn_chars,
        "payload_blocked": blocked,
        "payload_hint": PAYLOAD_BLOCKED_HINT if blocked else None,
    }


def trim_fetched_aggressive(data: dict) -> dict:
    """Second-pass compact when still over API limit."""
    from modules.query_planner import compact_payload_for_llm

    out = compact_payload_for_llm(data)
    sectors = out.get("sectors")
    if isinstance(sectors, dict):
        for key in ("industry", "concept"):
            block = sectors.get(key)
            if isinstance(block, dict):
                block["top_gainers"] = (block.get("top_gainers") or [])[:6]
                block["top_losers"] = (block.get("top_losers") or [])[:6]
    picks = out.get("sector_picks")
    if isinstance(picks, dict):
        picks["top_picks"] = (picks.get("top_picks") or [])[:3]
        picks["weak_boards"] = (picks.get("weak_boards") or [])[:2]
    period = out.get("sector_period_rank")
    if isinstance(period, dict):
        period["top_losers"] = (period.get("top_losers") or [])[:8]
        period["top_gainers"] = (period.get("top_gainers") or [])[:5]
    syms = out.get("symbols")
    if isinstance(syms, list):
        for sym in syms:
            if isinstance(sym, dict):
                sym["candle_bars"] = (sym.get("candle_bars") or [])[-3:]
                notes = sym.get("candle_notes")
                if isinstance(notes, list):
                    sym["candle_notes"] = notes[:2]
    market = out.get("market")
    if isinstance(market, dict):
        for drop in ("raw", "debug", "history"):
            market.pop(drop, None)
    return out
