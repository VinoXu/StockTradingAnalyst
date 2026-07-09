"""Chat + portfolio logic for web UI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from modules.analysis_mandate import CORE_ANALYSIS_MANDATE
from modules.chart_builder import build_charts
from modules.chat_history import (
    append_turn,
    clear_all,
    create_session,
    ensure_active_session,
    ensure_session_summary,
    get_session_summary,
    load_session_raw_turns,
    load_session_ui_turns,
    refresh_session_summary_async,
    set_active_session,
)
from modules.context_guard import assess_payload, trim_fetched_aggressive
from modules.conversation_memory import assess_context_limit, build_memory_block
from modules.cot_prompt import build_cot_instruction, strip_cot_leakage
from modules.data_timestamps import collect_reference_meta, format_time_banner
from modules.llm import build_chat_system_prompt, chat, chat_stream, llm_available
from modules.outlook_format import (
    OUTLOOK_INSTRUCTION,
    OUTLOOK_SECTION_TITLE,
    build_guidance_instruction,
    parse_outlook,
)
from modules.query_planner import (
    compact_payload_for_llm,
    fetch_data_for_plan,
    format_fetch_block,
    format_sector_pick_hint,
    plan_query,
)
from modules.skill_mapper import select_skills_for_plan, skills_summary
from modules.text_format import humanize_reply, humanize_stream_display
from modules.ui_log import log_ui_event

_ui_turns: dict[str, list[dict[str, str]]] = {}
_raw_turns: dict[str, list[tuple[str, str]]] = {}


def _resolve_scope(selected: list[str]) -> tuple[str, str | None, list[str] | None]:
    sel = selected or []
    if not sel:
        return "open", None, []
    if len(sel) == 1:
        return "symbol", sel[0], None
    return "portfolio", None, sel if sel else None


def _resolve_session_id(session_id: str | None) -> str:
    if not session_id or session_id == "default":
        return str(ensure_active_session())
    return str(int(session_id))


def _scope_note(scope: str, symbol: str | None, symbols: list[str] | None) -> str:
    if scope == "open":
        return (
            "用户未勾选分析标的。请根据每轮用户消息中的检索数据作答；"
            "若问哪个板块看好，开头第一句直接点名板块。"
        )
    if scope == "symbol" and symbol:
        return f"当前聚焦单标的：{symbol}。"
    if scope == "portfolio" and symbols:
        return f"用户已勾选分析标的：{', '.join(symbols)}。"
    return "当前为组合/持仓分析 scope。"


def _build_llm_messages(
    *,
    effective: list[str],
    plan,
    memory_block: str,
    fetched: dict,
    message: str,
) -> list[dict[str, str]]:
    """Rebuild LLM messages each turn: system(skills) + one compact user blob."""
    scope, symbol, symbols = _resolve_scope(effective)
    skill_names = select_skills_for_plan(plan, scope=scope)
    system = build_chat_system_prompt(
        skill_names=skill_names,
        scope_note=_scope_note(scope, symbol, symbols),
    )
    compact = compact_payload_for_llm(fetched)
    llm_user = _compose_user_message(message, memory_block=memory_block, fetched=compact)
    llm_user = (
        f"【本轮 Skill】{skills_summary(skill_names)}\n\n{llm_user}"
        if skill_names
        else llm_user
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": llm_user},
    ]


def hydrate_session_from_db(session_id: str) -> None:
    sid = int(session_id)
    _raw_turns[session_id] = load_session_raw_turns(sid)
    _ui_turns[session_id] = load_session_ui_turns(sid)


def reset_llm_context(session_id: str | None = None) -> None:
    """Web chat rebuilds LLM payload per turn; nothing to clear in memory."""
    return


def reset_session(session_id: str | None = None) -> None:
    """Clear all in-memory state for one session."""
    if session_id:
        sid = _resolve_session_id(session_id)
        _ui_turns.pop(sid, None)
        _raw_turns.pop(sid, None)
        return
    _ui_turns.clear()
    _raw_turns.clear()


def get_ui_turns(session_id: str = "default") -> list[dict[str, str]]:
    sid = _resolve_session_id(session_id)
    if sid not in _ui_turns:
        hydrate_session_from_db(sid)
    return list(_ui_turns.get(sid, []))


def create_new_chat_session() -> int:
    sid = create_session()
    reset_session(str(sid))
    return sid


def _session_context_status(session_id: str) -> dict:
    sid = _resolve_session_id(session_id)
    if sid not in _raw_turns:
        hydrate_session_from_db(sid)
    prior = list(_raw_turns.get(sid, []))
    summary = get_session_summary(int(sid))
    return assess_context_limit(prior, summary)


def get_current_session_info() -> dict:
    sid = ensure_active_session()
    sid_str = str(sid)
    if sid_str not in _raw_turns:
        hydrate_session_from_db(sid_str)
    from modules.chat_history import load_sessions

    meta = next((x for x in load_sessions() if x["id"] == sid_str), None)
    ctx = _session_context_status(sid_str)
    return {
        "session_id": sid_str,
        "title": meta["title"] if meta else "新对话",
        "summary": get_session_summary(sid),
        "turn_count": meta["turn_count"] if meta else len(_raw_turns.get(sid_str, [])),
        "turns": get_ui_turns(sid_str),
        **ctx,
    }


def activate_session(session_id: int) -> dict:
    set_active_session(session_id)
    sid_str = str(session_id)
    reset_session(sid_str)
    hydrate_session_from_db(sid_str)
    return get_current_session_info()


_WORKFLOW_HINTS: dict[str, str] = {
    "market_overview": (
        "【工作流·大盘环境】从 Skill（趋势/广度/量价）推断：接下来大盘更可能延续还是转折；"
        "点 1～2 个受益或受损的主线方向，不要只报指数涨跌。"
    ),
    "sector_deep_dive": (
        "【工作流·板块拆解】对照 Skill 拆解 matched_sectors："
        "趋势阶段、形态位置、量价是否配合；给出「若延续则…」「若失败则…」两条路径。"
    ),
    "risk_scan": (
        "【工作流·风险扫描】用 Skill 找尚未兑现的高概率风险：假突破、背离、胀爆、支撑失守；"
        "说明触发信号与建议降仓条件，不要等跌完再事后解释。"
    ),
    "opportunity_scan": (
        "【工作流·机会挖掘】用户要机会/加仓方向。"
        "必须基于 sector_picks（形态+趋势+广度，非单纯涨幅）点名 1～3 个板块，"
        "每个板块说：为何可能继续、最佳参与方式（顺势/回踩）、证伪条件。"
        "结构配合时禁止一味说别追高；要说怎么参与才合理。"
    ),
    "capital_flow": (
        "【工作流·资金】从量价与资金面 Skill 推断资金是「持续流入可能延续」还是「拉高出货」；"
        "给出后续 3～5 日的观察点，禁止编造北向/主力明细。"
    ),
    "named_symbols": (
        "【工作流·点名标的】对照 Skill 逐条核对 symbols/summary："
        "多周期趋势、形态、振荡指标；给短期路径与失效条件。"
    ),
    "question_deep_dive": (
        "【工作流·问题驱动】先答用户真正要什么（机会还是风险），"
        "再从 market/sectors 中匹配数据，用 Skill 做 forward 推断，禁止行情复述充字数。"
    ),
}


def _format_candle_bars_hint(fetched: dict) -> str:
    """Tell LLM to use precomputed OHLC metrics for nison/ta candlestick checks."""
    rows: list[str] = []
    for s in fetched.get("symbols") or []:
        if not s.get("available"):
            continue
        bars = s.get("candle_bars") or (s.get("summary") or {}).get("candle_bars") or []
        if not bars:
            continue
        name = s.get("name") or s.get("symbol")
        rows.append(f"- {name}：近{len(bars)}根日K已含 open/high/low/close/body/upper/lower/range/mid/gap_up/gap_down")
    if not rows:
        return ""
    return (
        "【K线计量数据·判形态必用】\n"
        + "\n".join(rows)
        + "\n分析蜡烛图时：先用 symbols[].candle_bars 或 summary.candle_bars 里的数值，"
        "对照 nison-candlestick-patterns 计量定义逐条核对①②③；禁止无数据硬判。"
    )


def _compose_user_message(
    message: str,
    *,
    memory_block: str,
    fetched: dict,
) -> str:
    parts: list[str] = [CORE_ANALYSIS_MANDATE, build_cot_instruction(fetched)]
    if memory_block:
        parts.append(memory_block)

    sector_hint = ""
    portfolio_hint = ""
    outlook_hint = ""
    workflow_hint = ""
    if fetched.get("portfolio_focus"):
        labels = []
        for s in fetched.get("symbols") or []:
            if not s.get("available"):
                continue
            code = (s.get("symbol") or "").split(".")[0]
            name = (s.get("name") or code).strip()
            labels.append(f"{name}({code})" if name != code else code)
        if labels:
            portfolio_hint = (
                f"【分析范围】用户已勾选：{'、'.join(labels)}。"
                "请逐只（或按组合对比）分析这些标的的走势、技术状态与关注点；"
                "以这些标的为主作答，不要写成全市/全行业复盘。"
                "仅当用户明确问大盘或板块时，才用一两句概括环境。"
            )
            outlook_hint = OUTLOOK_INSTRUCTION
    elif fetched.get("question_driven"):
        workflow = fetched.get("workflow") or "question_deep_dive"
        workflow_hint = _WORKFLOW_HINTS.get(workflow, _WORKFLOW_HINTS["question_deep_dive"])
        matched = fetched.get("matched_sectors") or []
        if matched:
            workflow_hint += f" 问题匹配板块关键词：{'、'.join(matched)}。"
        if fetched.get("sector_only") or "sector" in (fetched.get("query_intents") or []):
            sector_hint = (
                "引用 sectors 里的涨跌幅与领涨股；板块数据来自东财/同花顺，与 LLM 配置无关。"
            )
    elif fetched.get("symbols") and not fetched.get("sector_only"):
        outlook_hint = OUTLOOK_INSTRUCTION
    elif fetched.get("sector_only") or "sector" in (fetched.get("query_intents") or []):
        sector_hint = (
            "本轮为板块/行业级问题：请按行业板块与概念板块回答（如石油、半导体、银行、新能源等），"
            "引用 sectors 里的涨跌幅与领涨股；不要逐个分析自选股，除非用户明确点名某只股票。"
            "若 sectors.available 为 true 则必须使用其中数据；"
            "板块数据来自东财/同花顺行情接口，与阿里云 LLM 配置无关，勿将行情失败归因于 API Key。"
        )

    if portfolio_hint:
        parts.append(portfolio_hint)
    if workflow_hint:
        parts.append(workflow_hint)
    if outlook_hint:
        parts.append(outlook_hint.strip())
    pick_hint = format_sector_pick_hint(fetched.get("sector_picks") or {})
    if pick_hint:
        parts.append(pick_hint)
    pf = fetched.get("participant_flow") or {}
    if pf.get("northbound", {}).get("available"):
        nb = pf["northbound"]
        net = nb.get("total_net_buy")
        net_s = f"{net:,.0f}" if net is not None else "—"
        parts.append(
            f"【北向资金·全市场】{nb.get('trade_date', '')} 北向合计净买 {net_s}。"
            "须结合内资大小单结构判断内外资是否分歧。"
        )
    matched_flow = pf.get("sector_fund_flow_matched") or []
    if matched_flow:
        lines = ["【板块主力净流入·匹配】"]
        for row in matched_flow[:6]:
            lines.append(f"- {row.get('name')}：{row.get('main_net_inflow', 0):,.0f}")
        parts.append("\n".join(lines))
    candle_hint = _format_candle_bars_hint(fetched)
    if candle_hint:
        parts.append(candle_hint)
    parts.append(build_guidance_instruction(fetched).strip())
    parts.append(format_fetch_block(fetched))
    parts.append(
        "【用户问题】\n"
        f"{message}\n\n"
        "请基于 Skill 判据 + 本轮检索数据 + 对话记忆回答；"
        "必须给出向前看的趋势/机会判断与尚未兑现的风险，不要当行情播报；"
        "语言简单直白；禁止 Markdown；"
        "若 live_quote 有盘中价先报现价；技术指标说明日K截止日；"
        "全文最后按【收尾态度】给出可执行的仓位观点。"
        + (f"\n{sector_hint}" if sector_hint else "")
    )
    return "\n\n".join(parts)


def _strip_llm_time_preamble(text: str) -> str:
    """Remove LLM-generated time intros so backend banner is authoritative."""
    lines = text.split("\n")
    out: list[str] = []
    skipping = True
    for line in lines:
        s = line.strip()
        if skipping and (
            not s
            or s.startswith("📅")
            or "数据时间" in s
            or "行情截止" in s
            or "收盘" in s and len(s) < 60
            or "快照" in s
            or "实时盘口" in s
        ):
            if not s:
                skipping = False
            continue
        skipping = False
        out.append(line)
    return "\n".join(out).strip()


def _finalize_reply(raw: str, symbols: list[str]) -> tuple[str, dict, str, str, list[dict[str, str]]]:
    meta = collect_reference_meta(symbols)
    time_banner = format_time_banner(meta)
    body_raw = _strip_llm_time_preamble(humanize_reply(strip_cot_leakage(raw)))
    outlook, body = parse_outlook(body_raw)
    reply_body = body
    if outlook:
        block_lines = [OUTLOOK_SECTION_TITLE]
        for it in outlook:
            if it.get("label") and it["label"] != "综合":
                block_lines.append(it["label"])
            block_lines.append(f"短期（1～3个交易日）：{it['short_text']}")
            block_lines.append(f"中期（1～2周）：{it['medium_text']}")
            block_lines.append("")
        outlook_block = "\n".join(block_lines).strip()
        reply_body = f"{outlook_block}\n\n{body}".strip() if body else outlook_block
    reply = f"{time_banner}\n\n{reply_body}" if reply_body else time_banner
    return reply, meta, time_banner, body, outlook


def _should_attach_charts(plan, effective: list[str]) -> bool:
    if plan.needs_charts:
        return True
    if len(effective) == 1 and ("tech" in plan.intents or "price" in plan.chart_kinds):
        return True
    return False


def _prepare_ask_llm(
    message: str,
    *,
    symbols: list[str],
    session_id: str,
    append_user_turn: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate session + build LLM messages. Returns (error, context)."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "请输入问题"}, None

    sid = _resolve_session_id(session_id)
    set_active_session(int(sid))

    user_selected = list(symbols)
    no_selection = len(user_selected) == 0
    effective = user_selected

    if not llm_available():
        return {
            "ok": False,
            "error": "大模型未配置。请点击左下角「API 设置」填写 API Key 后重试。",
            "need_settings": True,
        }, None

    if sid not in _raw_turns:
        hydrate_session_from_db(sid)

    prior = list(_raw_turns.get(sid, []))
    if prior:
        session_summary = ensure_session_summary(int(sid), prior_turns=prior)
    else:
        session_summary = get_session_summary(int(sid))
    ctx_before = assess_context_limit(prior, session_summary=session_summary)
    if ctx_before["context_full"]:
        return {
            "ok": False,
            "error": ctx_before["new_chat_hint"],
            "need_new_chat": True,
            **ctx_before,
        }, None

    turns = _ui_turns.setdefault(sid, [])
    if append_user_turn:
        turns.append({"role": "user", "content": message})

    plan = plan_query(message, user_selected)
    fetched = fetch_data_for_plan(
        plan,
        None if no_selection else user_selected,
    )

    memory_block, _memory_truncated = build_memory_block(prior, session_summary=session_summary)
    messages = _build_llm_messages(
        effective=effective,
        plan=plan,
        memory_block=memory_block,
        fetched=fetched,
        message=message,
    )
    preflight = assess_payload(messages)
    payload_trimmed = False
    if preflight["payload_blocked"]:
        before_chars = preflight["payload_chars"]
        trimmed_fetch = trim_fetched_aggressive(fetched)
        messages = _build_llm_messages(
            effective=effective,
            plan=plan,
            memory_block=memory_block,
            fetched=trimmed_fetch,
            message=message,
        )
        preflight = assess_payload(messages)
        payload_trimmed = preflight["payload_chars"] < before_chars

    skill_names = select_skills_for_plan(plan, scope=_resolve_scope(effective)[0])
    log_ui_event(
        "context_preflight",
        detail={
            "payload_chars": preflight.get("payload_chars"),
            "payload_limit": preflight.get("payload_limit"),
            "payload_warned": preflight.get("payload_warned"),
            "payload_blocked": preflight.get("payload_blocked"),
            "payload_trimmed": payload_trimmed,
            "skills": list(skill_names),
        },
        session_id=sid,
    )

    if preflight["payload_blocked"]:
        if append_user_turn and turns and turns[-1].get("role") == "user":
            turns.pop()
        return {
            "ok": False,
            "error": preflight.get("payload_hint"),
            "payload_chars": preflight.get("payload_chars"),
            "payload_limit": preflight.get("payload_limit"),
            "need_new_chat": True,
        }, None

    return None, {
        "sid": sid,
        "message": message,
        "effective": effective,
        "prior": prior,
        "turns": turns,
        "plan": plan,
        "messages": messages,
        "preflight": preflight,
        "payload_trimmed": payload_trimmed,
        "skill_names": skill_names,
    }


def _complete_ask_turn(ctx: dict[str, Any], raw_reply: str) -> dict[str, Any]:
    """Persist assistant turn and return API payload."""
    sid = ctx["sid"]
    message = ctx["message"]
    effective = ctx["effective"]
    plan = ctx["plan"]
    preflight = ctx["preflight"]
    skill_names = ctx["skill_names"]
    payload_trimmed = ctx["payload_trimmed"]
    turns = ctx["turns"]

    reply, meta, time_banner, body, outlook = _finalize_reply(raw_reply, effective)

    charts = []
    if _should_attach_charts(plan, effective):
        chart_syms = plan.symbols or effective
        charts = build_charts(chart_syms, plan.chart_kinds or ["price"])

    turns.append(
        {
            "role": "assistant",
            "content": body,
            "timeBanner": time_banner,
            "fullReply": reply,
            "charts": charts,
            "outlook": outlook,
        }
    )

    raw_list = _raw_turns.setdefault(sid, [])
    raw_list.append((message, reply))

    append_turn(int(sid), message, reply)
    refresh_session_summary_async(int(sid))
    ctx_after = _session_context_status(sid)
    return {
        "ok": True,
        "reply": reply,
        "body": body,
        "time_banner": time_banner,
        "outlook": outlook,
        "question": message,
        "session_id": sid,
        **ctx_after,
        "data_reference": meta,
        "charts": charts,
        "plan": {
            "keywords": plan.keywords,
            "intents": plan.intents,
            "symbols": plan.symbols or effective,
            "workflow": plan.workflow,
            "skills": list(skill_names),
        },
        "payload_chars": preflight.get("payload_chars"),
        "payload_limit": preflight.get("payload_limit"),
        "payload_warned": preflight.get("payload_warned"),
        "payload_trimmed": payload_trimmed,
        "turns": get_ui_turns(sid),
    }


def ask_stream_events(
    message: str,
    *,
    symbols: list[str],
    session_id: str = "default",
) -> Iterator[dict[str, Any]]:
    """SSE event generator: phase → preflight → delta* → done | error."""
    err, ctx = _prepare_ask_llm(
        message,
        symbols=symbols,
        session_id=session_id,
        append_user_turn=True,
    )
    if err:
        yield {"event": "error", **err}
        return

    yield {
        "event": "phase",
        "phase": "preparing",
        "skills": list(ctx["skill_names"]),
        "workflow": ctx["plan"].workflow,
    }
    yield {
        "event": "preflight",
        "payload_chars": ctx["preflight"].get("payload_chars"),
        "payload_limit": ctx["preflight"].get("payload_limit"),
        "payload_warned": ctx["preflight"].get("payload_warned"),
        "payload_trimmed": ctx["payload_trimmed"],
    }

    try:
        yield {"event": "phase", "phase": "streaming"}
        chunks: list[str] = []
        display_sent = ""
        for delta in chat_stream(ctx["messages"], temperature=0.25):
            chunks.append(delta)
            display = humanize_stream_display(strip_cot_leakage("".join(chunks)))
            if len(display) < len(display_sent):
                display_sent = ""
            piece = display[len(display_sent) :]
            display_sent = display
            if piece:
                yield {"event": "delta", "text": piece}

        raw_reply = "".join(chunks).strip()
        if not raw_reply:
            raise RuntimeError("模型返回空内容")

        result = _complete_ask_turn(ctx, raw_reply)
        yield {"event": "done", "data": result}
    except Exception as exc:  # noqa: BLE001
        turns = ctx["turns"]
        if turns and turns[-1].get("role") == "user":
            turns.pop()
        yield {"event": "error", "ok": False, "error": str(exc)}


def ask(
    message: str,
    *,
    symbols: list[str],
    session_id: str = "default",
) -> dict:
    err, ctx = _prepare_ask_llm(
        message,
        symbols=symbols,
        session_id=session_id,
        append_user_turn=True,
    )
    if err:
        return err

    try:
        raw_reply = chat(ctx["messages"], temperature=0.25)
        return _complete_ask_turn(ctx, raw_reply)
    except Exception as exc:  # noqa: BLE001
        turns = ctx["turns"]
        if turns and turns[-1].get("role") == "user":
            turns.pop()
        return {"ok": False, "error": str(exc)}


def sync_symbols(codes: list[str]) -> dict:
    from modules.capital_flow import sync_capital_flow
    from modules.data_fetcher import sync_symbol
    from modules.market_data import sync_market

    if not codes:
        return {"ok": False, "message": "请先勾选要同步的标的，或点击「同步全部」"}

    lines: list[str] = []
    errors: list[str] = []
    try:
        mkt = sync_market()
        if mkt.get("status") == "ok":
            lines.append(f"大盘 ✓（{mkt.get('trade_date', '')}）")
        else:
            errors.append(f"大盘：{mkt.get('error', '失败')}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"大盘：{exc}")

    for c in codes:
        try:
            sync_symbol(c)
            sync_capital_flow(c)
            lines.append(f"{c} ✓")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{c}：{exc}")

    ok = bool(lines)
    msg = " · ".join(lines) if lines else "同步失败"
    if errors:
        msg += "；失败：" + " · ".join(errors)
    from modules.runtime_cache import invalidate_prefix

    invalidate_prefix("data:")
    reset_llm_context()
    return {"ok": ok, "message": msg}


def sync_one_quiet(code: str) -> None:
    try:
        from modules.capital_flow import sync_capital_flow
        from modules.data_fetcher import sync_symbol

        sync_symbol(code)
        sync_capital_flow(code)
    except Exception:
        pass


def clear_history() -> None:
    clear_all()
    reset_session()
