"""Hidden chain-of-thought instructions (internal reasoning, not shown in UI)."""

from __future__ import annotations

import re
from typing import Any

# Injected into user blob; model must follow but must NOT echo to user.
HIDDEN_COT_CORE = """【内部推理链·必做但不输出】
作答前在内心按以下五步完成推理（禁止把步骤、思考过程或中间结论写进回复）：
① 意图：结合 workflow / query_intents 确认用户要机会、风险、板块还是个股结论
② 证据：只引用本轮检索 JSON 的真实字段；缺失标「未验证」，禁止编造
③ Skill：逐条对照本轮 Skill 判据，标注满足 / 不满足 / 待验证
④ 推断：分别整理机会路径与风险路径，各含至少 1 个证伪条件（若…则…；若…则观点作废）
⑤ 输出：仅用口语给出最终结论（环境→机会→风险→收尾态度），遵守文风与合规要求

硬性约束：
- 回复中不得出现步骤编号、思考过程、「让我分析/首先其次/综上所述」等元表述
- 不得输出【思考】【结论】【推理】等任何标记
- 用户只能看到⑤的最终口语正文；思考过程对用户完全不可见"""

_WORKFLOW_COT_HINTS: dict[str, str] = {
    "opportunity_scan": "④ 侧重挖掘 1～3 个可参与方向及参与方式；风险作证伪条件附带。",
    "risk_scan": "④ 侧重尚未兑现的高概率风险与减仓/观望条件；机会一句带过即可。",
    "sector_deep_dive": "② 优先 sectors / sector_picks / participant_flow；③ 对照趋势与形态 Skill。",
    "market_overview": "② 优先 market / 广度 / 北向；④ 给大盘延续或转折及受益主线。",
    "capital_flow": "② 优先 participant_flow 与量价；④ 判断资金延续或派发，禁止编造席位。",
    "named_symbols": "② 优先 symbols / summary / candle_bars；逐标的核对 Skill。",
    "question_deep_dive": "① 先判断用户真正要机会还是风险，再选数据字段。",
}

_SIMPLE_COT = """【内部推理·简版】
内心核对：意图 → 检索 JSON 关键字段 → 本轮 Skill → 向前推断（机会+风险+证伪）→ 只输出最终口语结论。
禁止输出任何思考过程或步骤标记。"""


def build_cot_instruction(fetched: dict[str, Any]) -> str:
    """Workflow-aware hidden COT block for the user message."""
    workflow = fetched.get("workflow") or ""
    intents = set(fetched.get("query_intents") or [])
    symbols = fetched.get("symbols") or []

    if len(intents) <= 1 and len(symbols) == 1 and intents <= {"tech"}:
        return _SIMPLE_COT

    lines = [HIDDEN_COT_CORE]
    hint = _WORKFLOW_COT_HINTS.get(workflow)
    if hint:
        lines.append(f"本轮 workflow={workflow}：{hint}")
    if intents:
        lines.append(f"已识别 intents：{'、'.join(sorted(intents))}。")
    if fetched.get("wants_sector_pick"):
        lines.append("用户要板块优选：④ 必须基于 sector_picks 形态依据，不是涨幅榜复读。")
    return "\n".join(lines)


_THINKING_BLOCK_RE = re.compile(
    r"【思考】[\s\S]*?(?=【结论】|$)|"
    r"【推理】[\s\S]*?(?=【结论】|$)|"
    r"<thinking>[\s\S]*?</thinking>",
    re.IGNORECASE,
)
_CONCLUSION_MARKER_RE = re.compile(r"【结论】\s*")


def strip_cot_leakage(text: str) -> str:
    """Remove thinking blocks if the model leaked them into content."""
    if not text:
        return text
    out = _THINKING_BLOCK_RE.sub("", text)
    out = _CONCLUSION_MARKER_RE.sub("", out)
    return out.strip()
