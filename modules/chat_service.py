"""Chat + portfolio logic for web UI."""

from __future__ import annotations

from modules.chart_builder import build_charts
from modules.chat_history import (
    append_turn,
    clear_all,
    create_session,
    ensure_active_session,
    get_session_summary,
    load_session_raw_turns,
    load_session_ui_turns,
    set_active_session,
)
from modules.conversation_memory import assess_context_limit, build_memory_block
from modules.data_timestamps import collect_reference_meta, format_time_banner
from modules.llm import build_chat_session_messages, chat, llm_available
from modules.outlook_format import (
    OUTLOOK_INSTRUCTION,
    OUTLOOK_SECTION_TITLE,
    build_guidance_instruction,
    parse_outlook,
)
from modules.query_planner import fetch_data_for_plan, format_fetch_block, format_sector_pick_hint, plan_query
from modules.text_format import humanize_reply

_sessions: dict[str, dict] = {}
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


def _ensure_session(session_id: str, selected: list[str]) -> dict:
    scope, symbol, symbols = _resolve_scope(selected)
    key = (scope, symbol, tuple(symbols or ()))
    state = _sessions.get(session_id, {})
    if state.get("ready") and state.get("ctx_key") == key:
        return state
    messages = build_chat_session_messages(
        scope=scope,
        symbol=symbol,
        symbols=symbols,
        include_skills=True,
    )
    state = {"ready": True, "ctx_key": key, "messages": messages}
    _sessions[session_id] = state
    return state


def hydrate_session_from_db(session_id: str) -> None:
    sid = int(session_id)
    _raw_turns[session_id] = load_session_raw_turns(sid)
    _ui_turns[session_id] = load_session_ui_turns(sid)


def reset_llm_context(session_id: str | None = None) -> None:
    """Clear LLM message state only; keep DB-backed turn memory."""
    sid = _resolve_session_id(session_id)
    _sessions.pop(sid, None)


def reset_session(session_id: str | None = None) -> None:
    """Clear all in-memory state for one session."""
    if session_id:
        sid = _resolve_session_id(session_id)
        _sessions.pop(sid, None)
        _ui_turns.pop(sid, None)
        _raw_turns.pop(sid, None)
        return
    _sessions.clear()
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
        "【工作流·大盘环境】用户未勾选标的，问题聚焦市场整体。"
        "请用 market 数据说明指数、广度与成交额，再针对性回答，不要逐个分析自选股。"
    ),
    "sector_deep_dive": (
        "【工作流·板块拆解】用户未勾选标的，问题涉及板块/行业。"
        "请从 sectors 中匹配问题相关板块（见 matched_sectors），说明涨跌、领涨股与资金特征；"
        "可结合 market 环境；不要写成自选股点评。"
    ),
    "risk_scan": (
        "【工作流·风险扫描】用户关注回调/风险。"
        "结合 market 广度与 sectors.top_losers 说明承压板块与逻辑，给出风险观察。"
    ),
    "opportunity_scan": (
        "【工作流·板块形态优选】用户问哪个板块看好。"
        "系统已遍历行业+概念板块，并按领涨股股价形态、趋势、板块广度综合评分（见 sector_picks，不是单纯涨幅排序）。"
        "第一句话直接点名评分最高的1～3个板块，用形态/趋势说理由；不要只讲今日涨跌幅，不要绕弯。"
    ),
    "capital_flow": (
        "【工作流·资金】用户关注资金流向。"
        "结合 market、sectors 与 symbols（若有）说明资金特征，禁止编造北向/主力数据。"
    ),
    "named_symbols": (
        "【工作流·点名标的】用户问题中点名了代码/名称。"
        "请重点分析 symbols 数据；可简要带一句市场环境。"
    ),
    "question_deep_dive": (
        "【工作流·问题驱动】用户未勾选任何标的。"
        "请先拆解问题意图，从 market 与 sectors 中匹配相关板块与数据，再深度回答；"
        "不要分析用户自选股列表，除非问题中明确点名某只股票/代码。"
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
    parts: list[str] = []
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
    candle_hint = _format_candle_bars_hint(fetched)
    if candle_hint:
        parts.append(candle_hint)
    parts.append(build_guidance_instruction(fetched).strip())
    parts.append(format_fetch_block(fetched))
    parts.append(
        "【用户问题】\n"
        f"{message}\n\n"
        "请基于对话记忆与本轮检索数据回答；"
        "语言要简单直白、短句连贯，像跟朋友聊股票；"
        "禁止生造词、绕口令、文艺腔，禁止「手语/批语/口诀/落地指导/化作可操作」等表述；"
        "若 live_quote 有盘中价，先报现价与涨跌；技术指标仍按日K截止日说明；"
        "禁止 Markdown 符号；需要看走势时不必画 ASCII 图，系统会在界面自动配图；"
        "全文最后 1～2 句直接写加仓/减仓/观望/空仓等态度，不要另起小标题。"
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
    body_raw = _strip_llm_time_preamble(humanize_reply(raw))
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


def ask(
    message: str,
    *,
    symbols: list[str],
    session_id: str = "default",
) -> dict:
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "请输入问题"}

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
        }

    if sid not in _raw_turns:
        hydrate_session_from_db(sid)

    prior = list(_raw_turns.get(sid, []))
    session_summary = get_session_summary(int(sid))
    ctx_before = assess_context_limit(prior, session_summary=session_summary)
    if ctx_before["context_full"]:
        return {
            "ok": False,
            "error": ctx_before["new_chat_hint"],
            "need_new_chat": True,
            **ctx_before,
        }

    turns = _ui_turns.setdefault(sid, [])
    turns.append({"role": "user", "content": message})

    try:
        state = _ensure_session(sid, effective)

        memory_block, _memory_truncated = build_memory_block(prior, session_summary=session_summary)

        plan = plan_query(message, user_selected)
        fetched = fetch_data_for_plan(
            plan,
            None if no_selection else user_selected,
        )

        llm_user = _compose_user_message(message, memory_block=memory_block, fetched=fetched)
        state["messages"].append({"role": "user", "content": llm_user})

        raw_reply = chat(state["messages"], temperature=0.25)
        reply, meta, time_banner, body, outlook = _finalize_reply(raw_reply, effective)

        charts = []
        if _should_attach_charts(plan, effective):
            chart_syms = plan.symbols or effective
            charts = build_charts(chart_syms, plan.chart_kinds or ["price"])

        state["messages"].append({"role": "assistant", "content": reply})
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
            },
            "turns": get_ui_turns(sid),
        }
    except Exception as exc:  # noqa: BLE001
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
