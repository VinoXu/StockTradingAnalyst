"""Forward-looking analysis mandate injected into chat turns."""

from __future__ import annotations

from typing import Any

from modules.outlook_format import resolve_guidance_sector_label

CORE_ANALYSIS_MANDATE = """【分析使命·必遵】
你的价值不是复述行情，而是用判据做「向前看」的投研：
1. 机会：趋势能否延续、形态是否配合、量价是否支持；说清接下来可能怎么走、什么条件下成立。
2. 风险：假突破、背离、广度失真、关键支撑失守等尚未完全兑现的隐患。
3. 禁止把「今天涨了多少」当唯一答案；判断要能对应到具体方法或数据。
4. 结构配合时可讲分批跟进、回踩关注；结构不配合适观望或降权。
5. 禁止保证收益、禁止具体下单价位；立场只用偏多观察 / 偏空观察 / 观望 / 降权。"""

# 面向用户可见正文（总分逻辑，但不套生硬小标题）
USER_REPLY_STRUCTURE = """【对用户说话·必遵】
读者是普通投资者。先把总结论说清楚，再自然展开依据；像当面把事情讲明白，不要填表、不要贴标签。

禁止出现：Agent、评分卡、Team Lead、Skill 文件名、JSON、「上轮/上次论文/漂移」、
「结合历史分析」「各视角矛盾已汇总」等内部黑话；也不要用「首先其次综上所述」堆砌。

写作顺序（内在逻辑，不要用【汇总结论】【依据·技术指标】这类小标题，也不要用「一、二、三」编号）：
1. 开篇 2～4 句给汇总结论：对谁、偏多观察/偏空观察/观望/降权；短期与中期态度；跟不跟、怎么跟。
2. 接着写技术与指标依据（趋势、均线、形态、K 线、振荡等），用完整句子，一条说清现象和含义。
3. 再写资金行为是否支持（主力/北向/板块资金、量价）；没有数据就一句话说明本轮未核实，不要假装分析过。
4. 用一两句带过用到的分析框架与理论依据（如趋势量价、蜡烛形态、估值与护城河、逆向否决、产业逻辑），点到为止，不念文件名。
5. 收尾写 1～2 条风险与失效条件：出现什么信号，刚才的结论就要作废或改降权。

全文要求：纯中文、短句、口语、连贯成文；禁止 Markdown（无 **、##、表格、代码块）；
禁止输出任何【……】框架小标题；缺数据就实话实说，禁止编造。"""


def build_closing_guidance(fetched: dict[str, Any]) -> str:
    """Workflow-aware closing: opportunity-seeking vs risk-first vs balanced."""
    workflow = fetched.get("workflow") or ""
    intents = set(fetched.get("query_intents") or [])
    label = resolve_guidance_sector_label(fetched)
    wants_opportunity = (
        workflow in ("opportunity_scan", "sector_deep_dive")
        or fetched.get("wants_sector_pick")
        or "opportunity" in intents
        or any(k in " ".join(fetched.get("query_keywords") or []) for k in ("加仓", "机会", "看好", "推荐", "买入", "购买"))
    )
    wants_risk = workflow == "risk_scan" or "risk" in intents

    if wants_opportunity and not wants_risk:
        return f"""【收尾核对】开篇结论里兑现仓位含义：
结构配合：点名{label}，说明可分批关注或回踩再看，并带一条失效条件。
已大涨但结构未坏：可说主线还在、不追极端、回踩可观察。
禁止只喊机会不给失效条件；禁止结构明明配合却只劝别碰。不要用【小标题】。"""

    if wants_risk:
        return f"""【收尾核对】结尾写清与{label}相关的 1～2 个高概率风险及回避/降权含义；
机会最多在开篇结论里一句带过。不要用【小标题】。"""

    return f"""【收尾核对】开篇结论同时交代机会与收手信号；对象涉及{label}时要说具体。不要用【小标题】。"""
