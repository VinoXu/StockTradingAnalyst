"""FastAPI backend for web UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from modules.app_config import read_api_key_masked, read_settings, save_llm_settings
from modules.chat_history import delete_session, load_messages, load_sessions
from modules.chat_service import (
    activate_session,
    ask,
    ask_stream_events,
    clear_history,
    create_new_chat_session,
    get_current_session_info,
    get_ui_turns,
    reset_llm_context,
    sync_one_quiet,
    sync_symbols,
)
from modules.data_fetcher import resolve_stock_name
from modules.data_timestamps import primary_quote_as_of
from modules.db import init_db
from modules.env_loader import load_env
from modules.llm import llm_available, llm_config, llm_setup_hint
from modules.portfolio import add_holding, ensure_holding_name, portfolio_summary, remove_holding
from modules.realtime_quotes import get_live_quotes, is_a_share_trading_hours
from modules.ui_log import load_ui_events, log_ui_event

load_env()
init_db()

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Profit Protector")


class ChatReq(BaseModel):
    message: str
    symbols: list[str] = []
    session_id: str | None = None


class HoldingReq(BaseModel):
    code: str
    name: str | None = None


class HoldingsBatchReq(BaseModel):
    codes: list[str]


class SyncReq(BaseModel):
    symbols: list[str]


class SettingsReq(BaseModel):
    provider: str = "openai"
    api_key: str | None = None
    base_url: str | None = None
    model: str = "deepseek-r1"


class UiLogReq(BaseModel):
    event: str
    detail: dict[str, Any] | str | None = None
    session_id: str = "default"


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/static/{path:path}")
def static_no_cache(path: str):
    target = WEB / path
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status() -> dict[str, Any]:
    pf = portfolio_summary()
    cfg = llm_config()
    settings = read_settings()
    return {
        "llm_ready": llm_available(),
        "llm": llm_setup_hint(),
        "api_key_masked": read_api_key_masked(),
        "model": settings.get("model") or cfg.get("model"),
        "provider": settings.get("provider") or cfg.get("provider"),
        "holdings_count": pf["count"],
    }


@app.get("/api/settings")
def settings_get() -> dict[str, Any]:
    s = read_settings()
    cfg = llm_config()
    return {
        "provider": s.get("provider") or cfg.get("provider"),
        "base_url": s.get("base_url") or cfg.get("base_url"),
        "api_key_masked": s["api_key_masked"],
        "model": s.get("model") or cfg.get("model"),
        "has_key": s["has_key"],
        "llm_ready": llm_available(),
    }


@app.post("/api/settings")
def settings_save(req: SettingsReq) -> dict[str, str]:
    msg = save_llm_settings(
        provider=req.provider,
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
    )
    if msg.startswith("请输入"):
        raise HTTPException(400, msg)
    return {"message": msg}


@app.post("/api/settings/test")
def settings_test() -> dict[str, Any]:
    if not llm_available():
        raise HTTPException(400, "请先保存有效的 API Key")
    try:
        from modules.llm import chat

        reply = chat(
            [
                {"role": "system", "content": "你是测试助手，只回复 OK。"},
                {"role": "user", "content": "ping"},
            ],
            temperature=0,
            timeout=60.0,
        )
        cfg = llm_config()
        return {"ok": True, "message": f"连接成功（{cfg.get('model')}）", "sample": (reply or "")[:80]}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"连接失败：{exc}") from exc


@app.post("/api/chat")
def chat_api(req: ChatReq) -> dict[str, Any]:
    log_ui_event(
        "chat_start",
        detail={"message_len": len(req.message or ""), "symbols": req.symbols},
        session_id=req.session_id,
    )
    result = ask(req.message, symbols=req.symbols, session_id=req.session_id)
    log_ui_event(
        "chat_end",
        detail={
            "ok": result.get("ok"),
            "error": result.get("error"),
            "payload_chars": result.get("payload_chars"),
            "payload_limit": result.get("payload_limit"),
            "payload_warned": result.get("payload_warned"),
            "payload_trimmed": result.get("payload_trimmed"),
            "skills": (result.get("plan") or {}).get("skills"),
        },
        session_id=req.session_id,
    )
    return result


@app.post("/api/chat/stream")
def chat_stream_api(req: ChatReq) -> StreamingResponse:
    log_ui_event(
        "chat_start",
        detail={"message_len": len(req.message or ""), "symbols": req.symbols, "stream": True},
        session_id=req.session_id,
    )

    def event_gen():
        final: dict[str, Any] | None = None
        for ev in ask_stream_events(req.message, symbols=req.symbols, session_id=req.session_id):
            if ev.get("event") == "done":
                final = ev.get("data") or {}
            elif ev.get("event") == "error":
                final = ev
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

        if final:
            if final.get("event") == "error" or final.get("ok") is False:
                log_ui_event(
                    "chat_end",
                    detail={"ok": False, "error": final.get("error"), "stream": True},
                    session_id=req.session_id,
                )
            else:
                log_ui_event(
                    "chat_end",
                    detail={
                        "ok": True,
                        "error": None,
                        "stream": True,
                        "payload_chars": final.get("payload_chars"),
                        "payload_limit": final.get("payload_limit"),
                        "payload_warned": final.get("payload_warned"),
                        "payload_trimmed": final.get("payload_trimmed"),
                        "skills": (final.get("plan") or {}).get("skills"),
                    },
                    session_id=req.session_id,
                )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/session/current")
def session_current() -> dict[str, Any]:
    return get_current_session_info()


@app.post("/api/session/new")
def session_new() -> dict[str, Any]:
    sid = create_new_chat_session()
    log_ui_event("session_new", session_id=str(sid))
    return {"session_id": str(sid), "turns": [], "title": "新对话", "summary": ""}


@app.post("/api/session/{session_id}/activate")
def session_activate(session_id: int) -> dict[str, Any]:
    try:
        info = activate_session(session_id)
    except ValueError as exc:
        raise HTTPException(404, "会话不存在") from exc
    log_ui_event("session_activate", session_id=str(session_id))
    return info


@app.get("/api/chat/turns")
def chat_turns(session_id: str | None = None) -> dict[str, Any]:
    return {"turns": get_ui_turns(session_id or "default")}


@app.get("/api/history")
def history_list() -> dict[str, Any]:
    return {"items": load_sessions()}


@app.get("/api/messages")
def all_messages() -> dict[str, Any]:
    return {"messages": load_messages()}


@app.delete("/api/history")
def history_clear() -> dict[str, str]:
    clear_history()
    return {"status": "ok"}


@app.delete("/api/history/{session_id}")
def history_delete_one(session_id: int) -> dict[str, Any]:
    ok = delete_session(session_id)
    if not ok:
        raise HTTPException(404, "记录不存在")
    current = get_current_session_info()
    return {"ok": True, "current": current}


@app.post("/api/session/reset")
def session_reset(session_id: str | None = None, reason: str = "") -> dict[str, str]:
    log_ui_event(
        "session_reset",
        detail={"reason": reason or "unspecified"},
        session_id=session_id,
    )
    reset_llm_context(session_id)
    return {"status": "ok"}


@app.post("/api/ui-log")
def ui_log_client(req: UiLogReq) -> dict[str, str]:
    log_ui_event(req.event, source="client", detail=req.detail, session_id=req.session_id)
    return {"status": "ok"}


@app.get("/api/ui-log")
def ui_log_list(limit: int = 50) -> dict[str, Any]:
    return {"items": load_ui_events(limit)}


@app.get("/api/holdings")
def holdings_list(live: bool = False) -> dict[str, Any]:
    pf = portfolio_summary()
    symbols = [p["symbol"] for p in pf["positions"]]
    live_map: dict[str, dict[str, Any] | None] = {}
    if live and symbols:
        try:
            live_map = get_live_quotes(symbols)
        except Exception:
            live_map = {}

    items = []
    for p in pf["positions"]:
        code = p["symbol"].split(".")[0]
        quote_as_of = primary_quote_as_of(p["symbol"])
        live = live_map.get(p["symbol"])
        display_name = (p.get("name") or "").strip() or (ensure_holding_name(p["symbol"]) or "")
        item: dict[str, Any] = {
            "code": code,
            "symbol": p["symbol"],
            "name": display_name,
            "quantity": p.get("quantity"),
            "cost_price": p.get("cost_price"),
            "last_close": p.get("last_close"),
            "pnl_pct": p.get("pnl_pct"),
            "daily_change_pct": p.get("daily_change_pct"),
            "weight_pct": pf["weights_pct"].get(p["symbol"]),
            "trade_date": p.get("trade_date"),
            "data_as_of_date": quote_as_of.get("as_of_date"),
            "data_as_of_label": quote_as_of.get("as_of_label"),
            "has_data": p.get("has_quotes", False),
            "price_source": "daily_close",
        }
        if live and live.get("available"):
            item.update(
                {
                    "price_source": "intraday",
                    "live_price": live.get("price"),
                    "live_change_pct": live.get("change_pct"),
                    "live_change_amount": live.get("change_amount"),
                    "live_open": live.get("open"),
                    "live_high": live.get("high"),
                    "live_low": live.get("low"),
                    "live_prev_close": live.get("prev_close"),
                    "price_as_of_label": live.get("as_of_label"),
                    "live_quote_time": live.get("quote_time"),
                    "live_session": live.get("session"),
                }
            )
            if not item["name"] and live.get("name"):
                item["name"] = live["name"]
        items.append(item)
    return {
        "items": items,
        "trading_hours": is_a_share_trading_hours(),
    }


@app.get("/api/quotes/live")
def quotes_live(codes: str = "") -> dict[str, Any]:
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        code_list = [p["symbol"].split(".")[0] for p in portfolio_summary()["positions"]]
    try:
        live_map = get_live_quotes(code_list)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"实时行情获取失败：{exc}") from exc
    return {
        "quotes": {k.split(".")[0]: v for k, v in live_map.items()},
        "trading_hours": is_a_share_trading_hours(),
    }


@app.post("/api/holdings")
def holdings_add(req: HoldingReq, background_tasks: BackgroundTasks) -> dict[str, Any]:
    code = req.code.strip()
    if not code:
        raise HTTPException(400, "代码不能为空")
    name = resolve_stock_name(code)
    row = add_holding(code, name=name, quantity=0)
    background_tasks.add_task(sync_one_quiet, code)
    return {"ok": True, "holding": row}


@app.post("/api/holdings/batch")
def holdings_add_batch(req: HoldingsBatchReq, background_tasks: BackgroundTasks) -> dict[str, Any]:
    added: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw in req.codes:
        code = raw.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        try:
            name = resolve_stock_name(code)
            row = add_holding(code, name=name, quantity=0)
            background_tasks.add_task(sync_one_quiet, code)
            norm = (row.get("symbol") or code).split(".")[0]
            added.append(norm)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{code}: {exc}")
    if not added and errors:
        raise HTTPException(400, "；".join(errors))
    return {"ok": True, "added": added, "errors": errors}


@app.delete("/api/holdings/{code}")
def holdings_remove(code: str) -> dict[str, Any]:
    ok = remove_holding(code.strip())
    return {"ok": ok}


@app.post("/api/sync")
def sync_api(req: SyncReq) -> dict[str, Any]:
    codes = req.symbols
    if not codes:
        codes = [p["symbol"].split(".")[0] for p in portfolio_summary()["positions"]]
    return sync_symbols(codes)
