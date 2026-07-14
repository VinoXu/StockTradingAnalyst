"""Lightweight report QA against fetched evidence."""

from __future__ import annotations

from typing import Any

_REQUIRED_SYMBOL_FIELDS = (
    "research_reports",
    "fundamentals",
)


def qa_symbol_payload(fetched: dict[str, Any]) -> dict[str, Any]:
    """Check symbol deep-research payload completeness before LLM synthesis."""
    issues: list[str] = []
    notes: list[str] = []

    rr = fetched.get("research_reports") or {}
    if not rr.get("available"):
        issues.append("研报数据不可用，四大师估值解读须标灰色地带")
    else:
        for row in rr.get("symbols") or []:
            if row.get("confidence") == "C":
                notes.append(f"{row.get('symbol')} 研报置信度 C")

    fund = fetched.get("fundamentals") or {}
    if not fund.get("available"):
        issues.append("结构化财报不可用，巴菲特视角须降权")
    else:
        for row in fund.get("symbols") or []:
            rigor = row.get("rigor") or {}
            if rigor.get("confidence_downgrade"):
                notes.append(f"{row.get('symbol')} 财务验算存在告警")

    symbols = fetched.get("symbols") or []
    if not symbols:
        issues.append("缺少技术分析 symbols 包")

    ok = len(issues) == 0
    return {
        "ok": ok,
        "issues": issues,
        "notes": notes,
        "force_gray_tone": bool(issues),
    }


def qa_sector_payload(fetched: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    if fetched.get("research_reports"):
        issues.append("板块链路不应含研报字段")
    if fetched.get("fundamentals"):
        issues.append("板块链路不应含财报字段")
    picks = fetched.get("sector_picks") or {}
    if not picks.get("available"):
        issues.append("sector_picks 不可用，板块深研须说明数据缺失")
    return {"ok": len(issues) == 0, "issues": issues, "notes": [], "force_gray_tone": bool(issues)}
