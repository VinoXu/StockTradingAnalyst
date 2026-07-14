"""Enrich deep-research payload: evidence, QA, synthesis hints (no LLM)."""

from __future__ import annotations

from typing import Any

from modules.agent_specs import build_agent_evidence
from modules.query_planner import QueryPlan
from modules.report_qa import qa_sector_payload, qa_symbol_payload

_PARALLEL_WORKFLOWS = frozenset(
    {"news_pulse", "dyp_ask", "portfolio_review", "ta_screen", "symbol_research", "sector_research"}
)


def _synthesis_instruction(mode: str, qa: dict[str, Any]) -> str:
    gray = "整体语气须偏灰色地带、降权，并说明缺什么数据。" if qa.get("force_gray_tone") else ""
    if mode == "news_pulse":
        return "【异动归因】见 news_pulse 与 Agent 评分卡。"
    if mode == "dyp_ask":
        return (
            "【段永平式问答·dyp-ask】用简单语言回答生意本质与长期价值；"
            "少术语、多重「10 年后还在吗」；禁止装深研长篇。"
        )
    if mode == "portfolio_review":
        return (
            "【组合复盘·portfolio-review】基于 holdings 权重与各标的趋势；"
            "写集中度、板块暴露、再平衡语气（偏多观察/观望/降权），禁止下单指令。"
        )
    if mode == "ta_screen":
        return (
            "【TA 快筛】按六关 checklist + 去劣红线；明确通过/不通过/灰色；"
            "10 分钟内决定是否值得进入深研；写淘汰理由。"
        )
    if mode == "symbol_research":
        return (
            "【多 Agent 深研·个股】已注入 Nison + Murphy + 四大师证据包、研报共识、"
            "fundamentals 与财报精读判据（earnings-review）、六关快筛。"
            "须分别体现：①蜡烛图 ②墨菲趋势量价 ③研报评级 ④财报/估值 "
            "⑤四大师矛盾点 ⑥是否通过快筛。"
            "强制输出：偏多观察/观望/降权 + 参与方式 + ≥2 条证伪条件。"
            f"{gray}"
        )
    if mode == "sector_research":
        return (
            "【多 Agent 深研·板块】已注入 Nison + Murphy + 芒格 + 李录证据包与快筛/去劣；"
            "不调研报、不拉个股财报。"
            "须写清：优选板块、淘汰板块理由、广度与过热校验、证伪条件。"
            f"{gray}"
        )
    return ""


def enrich_research_payload(fetched: dict[str, Any], plan: QueryPlan) -> dict[str, Any]:
    """Attach agent evidence, QA, and Team Lead hints; mutates and returns fetched."""
    mode = plan.research_mode or ""
    workflow = plan.workflow or ""
    key = workflow if workflow in _PARALLEL_WORKFLOWS else mode
    if not key:
        return fetched

    out = dict(fetched)
    if mode:
        out["research_mode"] = mode

    out["agent_evidence"] = build_agent_evidence(out, plan)

    if mode == "symbol_research":
        out["report_qa"] = qa_symbol_payload(out)
    elif mode == "sector_research":
        out["report_qa"] = qa_sector_payload(out)
    elif workflow in ("news_pulse", "dyp_ask", "portfolio_review", "ta_screen"):
        out["report_qa"] = {"ok": True, "issues": [], "notes": [], "force_gray_tone": False}

    hint = _synthesis_instruction(key, out.get("report_qa") or {})
    if workflow == "news_pulse":
        hint = (
            "【异动归因·news-pulse】10 分钟内搞清发生了什么；"
            "区分价值事件/情绪波动/真因不明；禁止把所有新闻罗列当结论。"
        )
    if hint:
        out["research_synthesis_hint"] = hint
    return out
