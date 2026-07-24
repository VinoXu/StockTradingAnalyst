"""Parallel multi-agent LLM reviews + Team Lead message builder."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from modules.agent_specs import agent_roster_for_plan, evidence_for_agent, team_lead_skills_for_plan
from modules.llm import build_chat_system_prompt, chat, llm_available
from modules.query_planner import QueryPlan
from modules.skill_loader import load_skill

_AGENT_JSON_SCHEMA = (
    '输出仅一行 JSON，不要 Markdown：'
    '{"agent":"名称","score":1-5,"stance":"偏多观察|观望|降权",'
    '"bullets":["…","…"],"falsify":["若…则作废"],"confidence":"A|B|C"}。'
    'bullets 必须是可写进最终结论的依据句（含关键数字或明确「未核实」），'
    '禁止只写「数据不全/无法判断」；有 market/fundamentals 字段时必须各至少用到其中 1 条。'
)

_MAX_KEEP_CARDS = 4
_THIN_SHEET_CHAR_CAP = 1200


def _parallel_enabled() -> bool:
    return os.environ.get("RESEARCH_PARALLEL_AGENTS", "1").strip().lower() not in ("0", "false", "no")


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def _run_single_agent(
    agent: str,
    skill_name: str,
    *,
    fetched: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    evidence = evidence_for_agent(agent, fetched)
    try:
        skill_text = load_skill(skill_name)
    except FileNotFoundError:
        skill_text = f"Agent {agent}"
    system = (
        f"你是 {agent} 视角 Agent。只依据证据 JSON，禁止编造。\n"
        f"# Skill\n{skill_text}\n\n{_AGENT_JSON_SCHEMA}"
    )
    user = (
        f"【用户问题】{message}\n"
        f"【证据】\n```json\n{json.dumps(evidence, ensure_ascii=False, default=str)[:12000]}\n```"
    )
    try:
        raw = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            timeout=180.0,
        )
        parsed = _extract_json(raw)
        if not parsed:
            parsed = {"agent": agent, "stance": "观望", "score": 3, "bullets": [raw[:200]], "confidence": "C"}
        parsed.setdefault("agent", agent)
        parsed.setdefault("skill", skill_name)
        return parsed
    except Exception as exc:  # noqa: BLE001
        return {
            "agent": agent,
            "skill": skill_name,
            "stance": "观望",
            "score": 3,
            "bullets": [f"Agent 调用失败：{exc}"],
            "falsify": [],
            "confidence": "C",
            "error": str(exc),
        }


def run_parallel_agents(
    fetched: dict[str, Any],
    plan: QueryPlan,
    message: str,
    *,
    max_workers: int = 6,
) -> list[dict[str, Any]]:
    if not _parallel_enabled() or not llm_available():
        return []
    specs = agent_roster_for_plan(plan)
    if not specs:
        return []

    cards: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as pool:
        futures = {
            pool.submit(_run_single_agent, agent, skill, fetched=fetched, message=message): agent
            for agent, skill in specs
        }
        for fut in as_completed(futures):
            cards.append(fut.result())
    order = [a for a, _ in specs]
    cards.sort(key=lambda x: order.index(x.get("agent") or "") if x.get("agent") in order else 99)
    return cards


def _card_score(card: dict[str, Any]) -> float:
    try:
        return float(card.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def rank_and_filter_agent_cards(
    cards: list[dict[str, Any]],
    *,
    max_keep: int = _MAX_KEEP_CARDS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rank by score; keep high-confidence / conflicting stances; cap at max_keep."""
    if not cards:
        return [], {
            "avg_score": None,
            "stance_counts": {},
            "kept_agents": [],
            "dropped_agents": [],
            "total": 0,
        }

    failed = [c for c in cards if c.get("error")]
    ok = [c for c in cards if not c.get("error")]

    if not ok:
        kept = failed[:1]
        dropped = [c.get("agent") for c in failed[1:]]
        board = {
            "avg_score": None,
            "stance_counts": {},
            "kept_agents": [c.get("agent") for c in kept],
            "dropped_agents": dropped,
            "total": len(cards),
            "all_failed": True,
        }
        return kept, board

    stance_counts: dict[str, int] = {}
    for c in ok:
        st = str(c.get("stance") or "观望")
        stance_counts[st] = stance_counts.get(st, 0) + 1

    must: set[str] = set()
    for c in ok:
        conf = str(c.get("confidence") or "C").upper()
        if conf in ("A", "B"):
            agent = c.get("agent")
            if agent:
                must.add(str(agent))

    if len(stance_counts) > 1:
        for stance in stance_counts:
            candidates = [c for c in ok if (c.get("stance") or "观望") == stance]
            if candidates:
                best = max(candidates, key=_card_score)
                agent = best.get("agent")
                if agent:
                    must.add(str(agent))

    ranked = sorted(ok, key=_card_score, reverse=True)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()

    for c in ranked:
        agent = str(c.get("agent") or "")
        if agent in must and agent not in seen:
            kept.append(c)
            seen.add(agent)

    for c in ranked:
        if len(kept) >= max_keep:
            break
        agent = str(c.get("agent") or "")
        if agent and agent not in seen:
            kept.append(c)
            seen.add(agent)

    if len(kept) > max_keep:
        kept = sorted(kept, key=_card_score, reverse=True)[:max_keep]
        seen = {str(c.get("agent") or "") for c in kept}

    dropped = [str(c.get("agent") or "") for c in ok if str(c.get("agent") or "") not in seen]
    dropped.extend(str(c.get("agent") or "") for c in failed if str(c.get("agent") or "") not in seen)

    avg = sum(_card_score(c) for c in ok) / len(ok)
    board = {
        "avg_score": round(avg, 2),
        "stance_counts": stance_counts,
        "kept_agents": [c.get("agent") for c in kept],
        "dropped_agents": dropped,
        "total": len(cards),
    }
    return kept, board


def build_thin_fact_sheet(fetched: dict[str, Any]) -> str:
    """Conclusion-only headlines for Team Lead; never raw process packs."""
    lines: list[str] = ["【极瘦结论摘要】"]

    market = fetched.get("market")
    if isinstance(market, dict) and market:
        bits: list[str] = []
        breadth = market.get("breadth") or {}
        if breadth.get("available"):
            bits.append(
                f"涨跌家数 涨{breadth.get('rising_count')}/跌{breadth.get('falling_count')}"
                f"（{breadth.get('trade_date') or '—'}）"
            )
        else:
            bits.append(f"涨跌家数：本轮未核实（{breadth.get('error') or '无数据'}）")
        for key, label in (("index_live", "上证"), ("index_live_sz", "深证")):
            live = market.get(key) or {}
            if live.get("available") and live.get("price") is not None:
                chg = live.get("change_pct")
                chg_txt = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else str(chg or "—")
                amt = live.get("amount")
                amt_txt = f"，成交额{amt / 1e8:.0f}亿" if isinstance(amt, (int, float)) else ""
                bits.append(
                    f"{label}{live.get('price')}（{chg_txt}{amt_txt}，{live.get('as_of_label') or '盘中'}）"
                )
            else:
                bits.append(f"{label}盘中价：本轮未核实（{live.get('error') or '无数据'}）")
        turnover = market.get("two_market_turnover") or {}
        if turnover.get("available") and turnover.get("amount_yi_text"):
            partial = "（部分）" if turnover.get("partial") else ""
            bits.append(f"两市成交额{partial}{turnover.get('amount_yi_text')}")
        else:
            bits.append(f"两市成交额：本轮未核实（{turnover.get('error') or '无数据'}）")
        dow = market.get("dow") or {}
        if dow.get("available"):
            bits.append(f"双指数结构：{dow.get('state_cn') or dow.get('state')}")
            for note in (dow.get("notes") or [])[:2]:
                if note:
                    bits.append(str(note)[:60])
        else:
            bits.append(
                f"双指数历史日K：本轮未核实（{dow.get('error') or dow.get('state_cn') or '缺失'}）"
            )
        lines.append("- 大盘: " + "；".join(bits))

    pf = fetched.get("participant_flow") or {}
    if isinstance(pf, dict) and pf:
        pf_bits: list[str] = []
        nb = pf.get("northbound") or {}
        if nb.get("available"):
            net = nb.get("total_net_buy")
            if isinstance(net, (int, float)):
                pf_bits.append(f"北向合计净买 {net:,.0f}（{nb.get('trade_date') or '—'}）")
            else:
                pf_bits.append(f"北向：接口已返回但净买为空（{nb.get('trade_date') or '—'}）")
            if nb.get("status_note"):
                pf_bits.append(str(nb.get("status_note"))[:80])
        else:
            pf_bits.append(f"北向：本轮未核实（{nb.get('error') or '无数据'}）")
        fs = pf.get("fund_structure") or {}
        if fs.get("available"):
            tops = (fs.get("top_inflow") or [])[:3]
            names = [str(r.get("name") or "") for r in tops if isinstance(r, dict) and r.get("name")]
            if names:
                pf_bits.append("行业主力净流入靠前: " + "、".join(names))
            else:
                pf_bits.append("资金细分（板块主力净流入）：已返回")
        elif fs:
            pf_bits.append(f"资金细分：本轮未核实（{fs.get('error') or '无数据'}）")
        if pf_bits:
            lines.append("- 资金: " + "；".join(pf_bits))

    # 优先：语义驱动的区间累计收益榜；否则退回「最新交易日」跌幅
    sector_bits: list[str] = []
    period = fetched.get("sector_period_rank") or {}
    if isinstance(period, dict) and period.get("available"):
        days = period.get("trading_days") or "?"
        window = period.get("window") or ""
        losers = (period.get("top_losers") or [])[:10]
        parts = []
        for row in losers:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or "?"
            pct = row.get("period_return_pct")
            pct_txt = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else str(pct or "—")
            parts.append(f"{name}{pct_txt}")
        if parts:
            sector_bits.append(
                f"近{days}个交易日跌幅榜"
                + (f"（{window}）" if window else "")
                + ": "
                + "、".join(parts)
            )
            sector_bits.append("口径=板块指数区间累计收益（已按语义计算，非单日涨跌幅）")
    else:
        picks = fetched.get("sector_picks") or {}
        weak = (picks.get("weak_boards") or [])[:8] if isinstance(picks, dict) else []
        if weak:
            parts = []
            for row in weak:
                if not isinstance(row, dict):
                    continue
                name = row.get("name") or "?"
                pct = row.get("change_pct")
                pct_txt = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else str(pct or "—")
                parts.append(f"{name}{pct_txt}")
            if parts:
                sector_bits.append("今日跌幅靠前: " + "、".join(parts))
        else:
            sectors = fetched.get("sectors") or {}
            merged_losers: list[dict[str, Any]] = []
            if isinstance(sectors, dict):
                for key in ("industry", "concept"):
                    block = sectors.get(key) or {}
                    if not isinstance(block, dict) or not block.get("available"):
                        continue
                    for row in (block.get("top_losers") or [])[:5]:
                        if isinstance(row, dict):
                            merged_losers.append(row)
                merged_losers.sort(key=lambda r: float(r.get("change_pct") or 0))
                parts = []
                for row in merged_losers[:8]:
                    name = row.get("name") or "?"
                    pct = row.get("change_pct")
                    pct_txt = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else str(pct or "—")
                    parts.append(f"{name}{pct_txt}")
                if parts:
                    sector_bits.append("今日跌幅靠前: " + "、".join(parts))
        if sector_bits:
            sector_bits.append("口径=最新交易日涨跌幅（本轮未触发区间累计计算）")
    if sector_bits:
        lines.append("- 板块: " + "；".join(sector_bits))

    for s in (fetched.get("symbols") or [])[:5]:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("symbol") or "?"
        code = (s.get("symbol") or "").split(".")[0] or "?"
        if not s.get("available", True):
            err = (s.get("error") or "本轮未核实到可用行情").strip()
            lines.append(f"- {name}({code}): 数据不可用 — {err}")
            lines.append(
                "  （对用户须原样说明上述失败原因；禁止改口成「建议确认是否为A股」等猜测）"
            )
            continue
        bits = [f"regime={s.get('trend_regime') or '—'}", f"osc={s.get('osc_bias') or '—'}"]
        if s.get("capital_flow_note"):
            bits.append(f"资金={str(s.get('capital_flow_note'))[:40]}")
        pats = s.get("candle_patterns_cn") or []
        if pats:
            bits.append("形态=" + ",".join(str(p) for p in pats[:2]))
        lines.append(f"- {name}: {'；'.join(bits)}")

    for row in ((fetched.get("research_reports") or {}).get("symbols") or [])[:3]:
        if not isinstance(row, dict):
            continue
        note = (row.get("consensus_note") or "").strip()
        if note:
            lines.append(f"- 研报共识({row.get('symbol')}): {note[:120]}")

    for row in ((fetched.get("fundamentals") or {}).get("symbols") or [])[:3]:
        if not isinstance(row, dict):
            continue
        warns = ((row.get("rigor") or {}).get("warnings") or [])[:2]
        hl = row.get("highlights")
        if not isinstance(hl, dict):
            hl = ((row.get("ths_abstract") or {}).get("latest") or {}) if row.get("available") else {}
        hl_bits: list[str] = []
        if isinstance(hl, dict):
            label_map = (
                ("roe", "ROE"),
                ("debt_ratio", "资产负债率"),
                ("ocf_per_share", "每股经营现金流"),
                ("gross_margin", "毛利率"),
                ("revenue_yoy", "营收同比"),
                ("pe_ttm", "PE_TTM"),
            )
            for key, label in label_map:
                if hl.get(key) is not None:
                    hl_bits.append(f"{label}={hl.get(key)}")
        elif isinstance(hl, list):
            hl_bits = [str(x)[:40] for x in hl[:2]]
        parts = []
        if hl_bits:
            parts.append(",".join(hl_bits)[:120])
        if warns:
            parts.append("警示:" + ";".join(str(w)[:40] for w in warns))
        if parts:
            lines.append(f"- 财务要点({row.get('symbol')}): {'；'.join(parts)}")
        elif row.get("available") is False:
            lines.append(f"- 财务要点({row.get('symbol')}): 本轮未拉到可用财务摘要")

    for note in (fetched.get("thesis_drift_notes") or [])[:2]:
        if note:
            lines.append(f"- 论文漂移: {str(note)[:100]}")

    if len(lines) <= 1:
        return ""
    text = "\n".join(lines)
    if len(text) > _THIN_SHEET_CHAR_CAP:
        return text[: _THIN_SHEET_CHAR_CAP - 1] + "…"
    return text


def format_scoreboard(board: dict[str, Any]) -> str:
    if not board or not board.get("total"):
        return ""
    stances = board.get("stance_counts") or {}
    stance_txt = "、".join(f"{k}×{v}" for k, v in stances.items()) or "—"
    lines = [
        "【Agent 评分看板】",
        f"均分={board.get('avg_score')}；立场分布={stance_txt}",
        f"保留={','.join(str(a) for a in (board.get('kept_agents') or [])) or '—'}",
        f"淘汰={','.join(str(a) for a in (board.get('dropped_agents') or [])) or '无'}",
    ]
    return "\n".join(lines)


def build_team_lead_skill_names(plan: QueryPlan) -> tuple[str, ...]:
    return team_lead_skills_for_plan(plan)


def build_team_lead_user_blob(
    *,
    message: str,
    fetched: dict[str, Any],
    agent_cards: list[dict[str, Any]],
    memory_block: str = "",
    extra_parts: list[str] | None = None,
    scoreboard: dict[str, Any] | None = None,
    thin_sheet: str | None = None,
) -> str:
    """Team Lead sees filtered cards + scoreboard + thin facts — never raw process packs."""
    parts: list[str] = []
    if memory_block:
        parts.append(memory_block)
    if extra_parts:
        parts.extend(extra_parts)

    board_txt = format_scoreboard(scoreboard or {})
    if board_txt:
        parts.append(board_txt)

    if agent_cards:
        parts.append(
            "【筛选后 Agent 评分卡】\n```json\n"
            + json.dumps(agent_cards, ensure_ascii=False, indent=2, default=str)
            + "\n```"
        )

    sheet = thin_sheet if thin_sheet is not None else build_thin_fact_sheet(fetched)
    if sheet:
        parts.append(sheet)

    drift_notes = fetched.get("thesis_drift_notes") or []
    if drift_notes and "论文漂移" not in (sheet or ""):
        parts.append("【论文漂移摘要】\n" + "\n".join(f"- {n}" for n in drift_notes[:3] if n))

    parts.append(
        f"【用户问题】\n{message}\n\n"
        "你是最终执笔人：用户看不到 Agent 过程。把评分看板与评分卡消化成连贯口语答复——"
        "紧扣本轮问题：问什么答什么，给专业深度而非空泛概述；"
        "涉及方向时开篇先出【观点结论】：短期写「更可能延续上涨/回吐/震荡」直白句，中期写偏多/偏空/观望；再分短线/中期展开建议；"
        "其它内容不要套【小标题】、不要像填表。禁止必涨必跌与下单价。"
        "极瘦摘要里若已有涨跌家数、指数结构、两市成交额、北向、财务数字，必须当作已核实依据写进结论，"
        "禁止再说「关键大盘/财务数据没抓全」「成交额/北向本轮接口未返回」之类空话；"
        "只有摘要明确写「本轮未核实」的项才可声明缺数；北向净买为 0 须如实写 0，不算失败。"
        "禁止编造「字段变动」「穿透三级接口」等未在摘要/error 中出现的原因。"
        "若摘要写明某标的「数据不可用」及原因，开篇必须如实告知该原因，"
        "禁止改口成「建议确认是否为A股」等猜测。"
        "若用户问被套/回本/解套/整盘亏损等开放处境：禁止「无法分析」拒答，也禁止把问题扭成单票ETF/杠杆鉴定；"
        "必须先给账户层面的回本算术与可检验技术路径，并结合本轮大盘/板块事实；持仓列表仅作收尾细化。"
        "若本轮激活多路由（大盘+讨论+个股等），开篇先答用户主问题，再把其他路由证据织进同一篇，禁止只答一路。"
        "禁止 Markdown；禁止复述 Agent JSON；禁止提内部协作过程；禁止编造未给出的数据。"
    )
    return "\n\n".join(parts)


def build_team_lead_messages(
    *,
    plan: QueryPlan,
    scope_note: str,
    message: str,
    fetched: dict[str, Any],
    agent_cards: list[dict[str, Any]],
    memory_block: str = "",
    extra_parts: list[str] | None = None,
    scoreboard: dict[str, Any] | None = None,
    thin_sheet: str | None = None,
) -> list[dict[str, str]]:
    # Skill 正文只给各并行 Agent；Lead 只做评分卡综合，不注入 Skill 全文
    lead_scope = (
        f"{scope_note}\n"
        "你是给用户写最终答案的人：把各 Agent 评分卡当成内部素材消化掉，"
        "不要提 Agent、评分卡、协作过程；紧扣用户本轮问题写成连贯口语——"
        "问什么答什么，专业深度展开；方向题先【观点结论】分短线/中期，再写依据与风险；"
        "除【观点结论】外不要套其它【小标题】。"
        f"（本轮 workflow={getattr(plan, 'workflow', '') or '—'}）"
    ).strip()
    system = build_chat_system_prompt(skill_names=(), scope_note=lead_scope)
    user = build_team_lead_user_blob(
        message=message,
        fetched=fetched,
        agent_cards=agent_cards,
        memory_block=memory_block,
        extra_parts=extra_parts,
        scoreboard=scoreboard,
        thin_sheet=thin_sheet,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
