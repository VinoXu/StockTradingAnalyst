"""Collect and format market-data-as-of timestamps for chat replies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from modules.data_fetcher import _normalize_symbol
from modules.db import get_connection, init_db
from modules.market_data import load_latest_breadth
from modules.realtime_quotes import get_live_quote, get_live_quotes, is_a_share_trading_hours
from modules.ta_analysis import load_snapshot

DAILY_CLOSE_GRANULARITY = "daily_close"
INTRADAY_GRANULARITY = "intraday"
DAILY_CLOSE_NOTE = "日K收盘价（A股当日收盘约15:00）"
INTRADAY_NOTE = "股价为东财盘中快照；MACD/均线等指标仍基于日K收盘计算"


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def as_of_from_trade_date(trade_date: str | None) -> dict[str, str | None]:
    if not trade_date:
        return {
            "as_of_date": None,
            "as_of_label": None,
            "granularity": DAILY_CLOSE_GRANULARITY,
        }
    return {
        "as_of_date": trade_date,
        "as_of_label": f"{trade_date} 收盘",
        "granularity": DAILY_CLOSE_GRANULARITY,
    }


def symbol_data_as_of(symbol: str) -> dict[str, Any]:
    sym = _normalize_symbol(symbol)
    snap = load_snapshot(sym)
    out: dict[str, Any] = {
        "quote": as_of_from_trade_date(snap.trade_date if snap else None),
        "indicators": as_of_from_trade_date(None),
        "capital_flow": as_of_from_trade_date(None),
    }
    init_db()
    with get_connection() as conn:
        ind = conn.execute(
            """
            SELECT trade_date FROM indicators
            WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1
            """,
            (sym,),
        ).fetchone()
        if ind:
            out["indicators"] = as_of_from_trade_date(ind["trade_date"])
        cf = conn.execute(
            """
            SELECT trade_date FROM capital_flow
            WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1
            """,
            (sym,),
        ).fetchone()
        if cf:
            out["capital_flow"] = as_of_from_trade_date(cf["trade_date"])
    return out


def market_data_as_of() -> dict[str, Any]:
    out: dict[str, Any] = {
        "breadth": as_of_from_trade_date(None),
        "index_sh": as_of_from_trade_date(None),
    }
    breadth = load_latest_breadth()
    if breadth:
        out["breadth"] = as_of_from_trade_date(breadth.get("trade_date"))
    sh = load_snapshot("INDEX.SH000001")
    if sh:
        out["index_sh"] = as_of_from_trade_date(sh.trade_date)
    return out


symbol_data_times = symbol_data_as_of
market_data_times = market_data_as_of


def primary_quote_as_of(symbol: str) -> dict[str, str | None]:
    snap = load_snapshot(_normalize_symbol(symbol))
    return as_of_from_trade_date(snap.trade_date if snap else None)


def _symbol_meta_entry(sym: str, live: dict[str, Any] | None) -> dict[str, Any]:
    as_of = symbol_data_as_of(sym)
    quote = as_of.get("quote") or {}
    indicators = as_of.get("indicators") or {}
    entry: dict[str, Any] = {
        "symbol": sym,
        "as_of_date": quote.get("as_of_date"),
        "as_of_label": quote.get("as_of_label"),
        "trade_date": quote.get("as_of_date"),
        "indicator_as_of_label": indicators.get("as_of_label"),
        "data_as_of": as_of,
    }
    if live and live.get("available"):
        entry["live_quote"] = live
        entry["price_source"] = "intraday"
        entry["display_price"] = live.get("price")
        entry["price_as_of_label"] = live.get("as_of_label")
        entry["granularity"] = INTRADAY_GRANULARITY
    else:
        entry["price_source"] = "daily_close"
        entry["granularity"] = DAILY_CLOSE_GRANULARITY
    return entry


def collect_reference_meta(
    symbols: list[str] | None = None,
    *,
    include_live: bool = True,
) -> dict[str, Any]:
    sym_list = []
    seen: set[str] = set()
    for raw in symbols or []:
        sym = _normalize_symbol(raw)
        if sym not in seen:
            seen.add(sym)
            sym_list.append(sym)

    live_map: dict[str, dict[str, Any] | None] = {}
    if include_live and sym_list:
        try:
            live_map = get_live_quotes(sym_list)
        except Exception:
            live_map = {}

    has_live = any(v and v.get("available") for v in live_map.values())
    meta: dict[str, Any] = {
        "generated_at": _now_label(),
        "granularity": INTRADAY_GRANULARITY if has_live else DAILY_CLOSE_GRANULARITY,
        "granularity_note": INTRADAY_NOTE if has_live else DAILY_CLOSE_NOTE,
        "trading_hours": is_a_share_trading_hours(),
        "market": None,
        "symbols": [],
    }

    load_latest_breadth()
    mkt_as_of = market_data_as_of()
    breadth_as_of = mkt_as_of.get("breadth") or {}
    index_as_of = mkt_as_of.get("index_sh") or {}
    index_live = None
    if include_live:
        try:
            index_live = get_live_quote("INDEX.SH000001")
        except Exception:
            index_live = None

    market_entry: dict[str, Any] | None = None
    if index_live and index_live.get("available"):
        market_entry = {
            "price_source": "intraday",
            "display_price": index_live.get("price"),
            "price_as_of_label": index_live.get("as_of_label"),
            "live_quote": index_live,
            "breadth_as_of_label": breadth_as_of.get("as_of_label"),
            "data_as_of": mkt_as_of,
        }
    elif breadth_as_of.get("as_of_date"):
        market_entry = {
            "as_of_date": breadth_as_of.get("as_of_date"),
            "as_of_label": breadth_as_of.get("as_of_label"),
            "price_source": "daily_close",
            "data_as_of": mkt_as_of,
        }
    elif index_as_of.get("as_of_date"):
        market_entry = {
            "as_of_date": index_as_of.get("as_of_date"),
            "as_of_label": index_as_of.get("as_of_label"),
            "price_source": "daily_close",
            "data_as_of": mkt_as_of,
        }
    meta["market"] = market_entry

    for sym in sym_list:
        meta["symbols"].append(_symbol_meta_entry(sym, live_map.get(sym)))

    return meta


def format_time_banner(meta: dict[str, Any]) -> str:
    lines = [f"📅 本回答生成于：{meta['generated_at']}"]
    mkt = meta.get("market")
    if mkt:
        live = mkt.get("live_quote")
        if live and live.get("available") and live.get("price") is not None:
            lines.append(
                f"📅 上证盘中：{live['price']:.2f}（{live.get('as_of_label', '')}）"
            )
            if mkt.get("breadth_as_of_label"):
                lines.append(f"📅 市场广度截止：{mkt['breadth_as_of_label']}")
        elif mkt.get("as_of_label"):
            lines.append(f"📅 大盘行情截止：{mkt['as_of_label']}")

    for item in meta.get("symbols") or []:
        code = item["symbol"].split(".")[0]
        live = item.get("live_quote")
        if live and live.get("available") and live.get("price") is not None:
            pct = live.get("change_pct")
            pct_txt = f" {pct:+.2f}%" if pct is not None else ""
            lines.append(
                f"📅 {code} 盘中价：{live['price']:.2f}{pct_txt}（{live.get('as_of_label', '')}）"
            )
            if item.get("indicator_as_of_label"):
                lines.append(f"📅 {code} 技术指标截止：{item['indicator_as_of_label']}")
        elif item.get("as_of_label"):
            lines.append(f"📅 {code} 行情截止：{item['as_of_label']}")
        else:
            lines.append(f"📅 {code}：暂无本地行情")

    note = meta.get("granularity_note") or DAILY_CLOSE_NOTE
    lines.append(f"（{note}）")
    return "\n".join(lines)


def format_conversational_opening(meta: dict[str, Any]) -> str:
    return format_time_banner(meta)


def format_reference_header(meta: dict[str, Any]) -> str:
    return format_time_banner(meta)
