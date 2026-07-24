"""Participant flow: northbound, LHB institution vs hot-money, order-size structure."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import akshare as ak

from modules.akshare_client import (
    fetch_em_sector_fund_flow_rank,
    fetch_with_retry,
    humanize_fetch_error,
)
from modules.capital_flow import consecutive_net_inflow_days, fetch_stock_fund_flow, save_capital_flow
from modules.data_fetcher import _normalize_symbol, _symbol_to_ak_code
from modules.db import get_connection, init_db
from modules.runtime_cache import get_or_set, invalidate

_NORTH_CACHE_TTL = 120.0
_SECTOR_FLOW_TTL = 180.0
_LHB_CACHE_TTL = 300.0

# 常见营业部席位关键词（游资代理，非官方分类）
_HOT_MONEY_KEYWORDS = (
    "东方财富",
    "拉萨",
    "国泰君安上海江苏路",
    "华泰深圳益田路",
    "中信上海溧阳路",
    "银河北京中关村",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_northbound_summary() -> dict[str, Any]:
    """Market-wide northbound net buy today (沪股通+深股通)."""
    def _load() -> dict[str, Any]:
        try:
            raw = fetch_with_retry(ak.stock_hsgt_fund_flow_summary_em, max_retry=2, base_delay=1.0)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": humanize_fetch_error(exc)}
        if raw is None or raw.empty:
            return {"available": False, "error": "北向摘要返回空"}
        rows: list[dict[str, Any]] = []
        total_net = 0.0
        has_net = False
        for _, row in raw.iterrows():
            direction = str(row.get("资金方向") or "")
            if direction != "北向":
                continue
            block = str(row.get("板块") or "")
            # 南向「港股通(沪/深)」；北向为「沪股通」「深股通」（名称不含「港股通」前缀）
            if block.startswith("港股通") or "股通" not in block:
                continue
            net = row.get("成交净买额")
            inflow = row.get("资金净流入")
            try:
                net_f = float(net) if net is not None and str(net) not in ("", "nan") else None
            except (TypeError, ValueError):
                net_f = None
            if net_f is None or net_f == 0:
                try:
                    net_f = float(inflow) if inflow is not None else net_f
                except (TypeError, ValueError):
                    pass
            if net_f is not None:
                total_net += net_f
                has_net = True
            rows.append(
                {
                    "board": block,
                    "trade_date": str(row.get("交易日") or ""),
                    "net_buy": net_f,
                    "net_inflow": row.get("资金净流入"),
                    "rising": row.get("上涨数"),
                    "falling": row.get("下跌数"),
                    "index": row.get("相关指数"),
                    "index_change_pct": row.get("指数涨跌幅"),
                }
            )
        if not rows:
            return {"available": False, "error": "no northbound rows"}
        return {
            "available": True,
            "trade_date": rows[0].get("trade_date"),
            "total_net_buy": total_net if has_net else None,
            "boards": rows,
            "note": "北向=沪深港通资金；日频/盘中更新，非逐笔席位。",
        }

    hit = get_or_set("data:northbound_summary", _NORTH_CACHE_TTL, _load)
    if not hit.get("available"):
        invalidate("data:northbound_summary")
    return hit


def fetch_lhb_participant_breakdown(symbol: str, *, lookback_days: int = 5) -> dict[str, Any]:
    """Institution vs hot-money proxy from LHB (listed stocks only, recent dates)."""
    code = _symbol_to_ak_code(symbol)

    def _load() -> dict[str, Any]:
        try:
            raw = fetch_with_retry(ak.stock_lhb_jgmx_sina)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc)}
        if raw is None or raw.empty:
            return {"available": False, "error": "empty lhb institution detail"}
        code_col = "股票代码" if "股票代码" in raw.columns else raw.columns[0]
        sub = raw[raw[code_col].astype(str).str.zfill(6) == code]
        if sub.empty:
            return {"available": False, "on_lhb_recent": False, "note": "近期未上龙虎榜或无席位明细"}
        buy_col = "机构席位买入额" if "机构席位买入额" in sub.columns else None
        sell_col = "机构席位卖出额" if "机构席位卖出额" in sub.columns else None
        if not buy_col or not sell_col:
            return {"available": False, "error": "missing institution columns"}
        inst_buy = float(sub[buy_col].fillna(0).sum())
        inst_sell = float(sub[sell_col].fillna(0).sum())
        dates = sorted({str(x)[:10] for x in sub.get("交易日期", sub.iloc[:, 2]).tolist()}, reverse=True)
        return {
            "available": True,
            "on_lhb_recent": True,
            "trade_dates": dates[:lookback_days],
            "institution_buy": inst_buy,
            "institution_sell": inst_sell,
            "institution_net": inst_buy - inst_sell,
            "note": (
                "机构席=龙虎榜公布的机构专用席位；游资需结合营业部明细（上榜日才有）。"
                "非上榜股无此数据。"
            ),
        }

    return get_or_set(f"data:lhb_inst:{code}", _LHB_CACHE_TTL, _load)


def _parse_sector_flow_df(raw: Any, *, sector_type: str, top_n: int, source: str, note: str) -> dict[str, Any]:
    if raw is None or getattr(raw, "empty", True):
        return {"available": False, "sector_type": sector_type, "error": "板块资金流返回空"}
    name_col = next(
        (c for c in raw.columns if "名称" in str(c) or str(c) == "行业"),
        raw.columns[1] if len(raw.columns) > 1 else raw.columns[0],
    )
    # Prefer explicit 主力净流入; avoid matching 超大单/大单 first
    net_col = next(
        (c for c in raw.columns if "主力" in str(c) and "净" in str(c) and "流入" in str(c) and "额" in str(c)),
        None,
    )
    if net_col is None:
        net_col = next((c for c in raw.columns if str(c) in ("净额", "今日主力净流入-净额")), None)
    if net_col is None:
        net_col = next((c for c in raw.columns if "净" in str(c) and "流入" in str(c)), raw.columns[-1])
    items: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        name = str(row.get(name_col) or "").strip()
        if not name:
            continue
        try:
            net = float(row.get(net_col) or 0)
        except (TypeError, ValueError):
            net = 0.0
        items.append({"name": name, "main_net_inflow": net})
    if not items:
        return {"available": False, "sector_type": sector_type, "error": "板块资金流无有效行"}
    items.sort(key=lambda x: x["main_net_inflow"], reverse=True)
    return {
        "available": True,
        "sector_type": sector_type,
        "source": source,
        "top_inflow": items[:top_n],
        "top_outflow": sorted(items, key=lambda x: x["main_net_inflow"])[:top_n],
        "note": note,
    }


def fetch_sector_fund_flow_rank(*, sector_type: str = "行业资金流", top_n: int = 15) -> dict[str, Any]:
    """Sector fund flow rank: curl_cffi EM → akshare EM → THS industry fallback."""

    def _from_em_curl() -> dict[str, Any]:
        try:
            raw = fetch_em_sector_fund_flow_rank(sector_type=sector_type, top_n=top_n)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "sector_type": sector_type, "error": humanize_fetch_error(exc)}
        return _parse_sector_flow_df(
            raw,
            sector_type=sector_type,
            top_n=top_n,
            source="eastmoney_clist_curl",
            note="东财板块主力净流入估算（curl 多 host），非交易所逐笔。",
        )

    def _from_em_akshare() -> dict[str, Any]:
        try:
            raw = fetch_with_retry(
                ak.stock_sector_fund_flow_rank,
                indicator="今日",
                sector_type=sector_type,
                max_retry=2,
                base_delay=1.0,
            )
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "sector_type": sector_type, "error": humanize_fetch_error(exc)}
        return _parse_sector_flow_df(
            raw,
            sector_type=sector_type,
            top_n=top_n,
            source="eastmoney_clist",
            note="东财板块主力净流入估算，非交易所逐笔。",
        )

    def _from_industry_fallback() -> dict[str, Any]:
        """同花顺即时行业资金流；净额单位为亿元，换算为元与东财口径对齐。"""
        try:
            raw = fetch_with_retry(ak.stock_fund_flow_industry, symbol="即时", max_retry=2, base_delay=0.8)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "sector_type": sector_type, "error": humanize_fetch_error(exc)}
        if raw is None or raw.empty:
            return {"available": False, "sector_type": sector_type, "error": "同花顺行业资金流返回空"}
        # 单位换算：亿元 → 元
        name_col = "行业" if "行业" in raw.columns else raw.columns[1]
        net_col = "净额" if "净额" in raw.columns else None
        if net_col is None:
            return {"available": False, "sector_type": sector_type, "error": "同花顺缺净额字段"}
        scaled = raw.copy()
        try:
            scaled[net_col] = scaled[net_col].astype(float) * 1e8
        except (TypeError, ValueError):
            return {"available": False, "sector_type": sector_type, "error": "同花顺净额无法解析"}
        # 借用名称列别名，供统一解析
        if "名称" not in scaled.columns:
            scaled["名称"] = scaled[name_col]
        scaled["今日主力净流入-净额"] = scaled[net_col]
        return _parse_sector_flow_df(
            scaled,
            sector_type=sector_type,
            top_n=top_n,
            source="ths_industry_instant",
            note=(
                "同花顺即时行业资金净额（已×1亿换算为元）；"
                "为东财板块资金流断开时的备用源，非公募/游资席位比例。"
            ),
        )

    def _load() -> dict[str, Any]:
        errors: list[str] = []
        primary = _from_em_curl()
        if primary.get("available"):
            return primary
        errors.append(f"东财curl({primary.get('error') or '失败'})")

        secondary = _from_em_akshare()
        if secondary.get("available"):
            secondary["fallback_of"] = primary.get("error") or "eastmoney_curl_failed"
            return secondary
        errors.append(f"东财ak({secondary.get('error') or '失败'})")

        if sector_type == "行业资金流":
            fb = _from_industry_fallback()
            if fb.get("available"):
                fb["fallback_of"] = "；".join(errors)
                return fb
            errors.append(f"同花顺({fb.get('error') or '失败'})")

        return {
            "available": False,
            "sector_type": sector_type,
            "error": "；".join(errors) if errors else "板块资金流未返回",
        }

    key = f"data:sector_flow:{sector_type}"
    # 失败不长缓存，避免整轮问答被一次断连锁死
    hit = get_or_set(key, _SECTOR_FLOW_TTL, _load)
    if not hit.get("available"):
        invalidate(key)
    return hit


def match_sector_fund_flows(sector_names: list[str]) -> list[dict[str, Any]]:
    """Match user/sector keywords to industry+concept fund flow rows."""
    if not sector_names:
        return []
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_ind = pool.submit(fetch_sector_fund_flow_rank, sector_type="行业资金流")
        fut_con = pool.submit(fetch_sector_fund_flow_rank, sector_type="概念资金流")
        industry = fut_ind.result()
        concept = fut_con.result()
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in (industry, concept):
        if not block.get("available"):
            continue
        for row in (block.get("top_inflow") or []) + (block.get("top_outflow") or []):
            name = row.get("name") or ""
            if not name or name in seen:
                continue
            if any(kw in name or name in kw for kw in sector_names if len(kw) >= 2):
                seen.add(name)
                hits.append({**row, "board_type": block.get("sector_type", "")})
    return hits[:12]


def _behavior_tags(
    *,
    main: float | None,
    retail: float | None,
    price_up: bool | None = None,
) -> list[str]:
    tags: list[str] = []
    if main is None or retail is None:
        return tags
    if main > 0 and retail < 0:
        tags.append("主力净流入、散户净流出（吸筹/推升代理）")
    elif main < 0 and retail > 0:
        tags.append("主力净流出、散户净流入（派发/接盘代理）")
    elif main > 0 and retail > 0:
        tags.append("主力与散户同步净流入（跟风放量）")
    elif main < 0 and retail < 0:
        tags.append("主力与散户同步净流出（踩踏/撤离）")
    if price_up is True and main < 0:
        tags.append("价涨资金出（量价资金背离）")
    if price_up is False and main > 0:
        tags.append("价跌资金进（下跌承接/抄底代理）")
    return tags


def analyze_participant_structure(symbol: str, *, price_change_pct: float | None = None) -> dict[str, Any]:
    """Rich capital flow with large/small order split + LHB + behavior tags."""
    sym = _normalize_symbol(symbol)
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM capital_flow WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (sym,),
        ).fetchone()
    if not row:
        return {
            "available": False,
            "symbol": sym,
            "note": "无本地资金流，将尝试在线拉取",
        }

    r = dict(row)
    super_large = r.get("super_large_net")
    large = r.get("large_net")
    medium = r.get("medium_net")
    small = r.get("small_net")
    main = r.get("main_net_inflow")

    def _sum(*vals: float | None) -> float | None:
        nums = [v for v in vals if v is not None]
        return sum(nums) if nums else None

    main_calc = _sum(super_large, large)
    retail_calc = _sum(medium, small)
    price_up = None if price_change_pct is None else price_change_pct > 0

    lhb = fetch_lhb_participant_breakdown(sym)
    streak = consecutive_net_inflow_days(sym)
    tags = _behavior_tags(main=main_calc or main, retail=retail_calc, price_up=price_up)

    notes: list[str] = []
    if main_calc is not None and retail_calc is not None:
        notes.append(f"大单侧净流入 {main_calc:,.0f}（超大+大）")
        notes.append(f"中小单侧净流入 {retail_calc:,.0f}（中+小，散户代理）")
    elif main is not None:
        notes.append(f"主力净流入 {main:,.0f}")
    notes.extend(tags)
    if streak >= 3:
        notes.append(f"连续 {streak} 日主力净流入")
    if r.get("northbound_hold_ratio") is not None:
        notes.append(f"北向持股占比 {r['northbound_hold_ratio']:.2f}%")
    if r.get("margin_balance") is not None:
        notes.append(f"融资余额 {r['margin_balance']:,.0f}")
    if lhb.get("available") and lhb.get("on_lhb_recent"):
        net = lhb.get("institution_net")
        notes.append(f"龙虎榜机构席净{'买' if (net or 0) >= 0 else '卖'} {abs(net or 0):,.0f}（上榜日）")
    elif r.get("on_lhb"):
        notes.append("近期登上龙虎榜（无席位明细）")

    bias = "neutral"
    score = 0
    effective_main = main_calc if main_calc is not None else main
    effective_retail = retail_calc
    if effective_main is not None:
        score += 1 if effective_main > 0 else -1 if effective_main < 0 else 0
    if effective_retail is not None and effective_main is not None:
        if effective_main > 0 and effective_retail < 0:
            score += 1
        elif effective_main < 0 and effective_retail > 0:
            score -= 1
    if score >= 1:
        bias = "bullish"
    elif score <= -1:
        bias = "bearish"

    return {
        "available": True,
        "symbol": sym,
        "trade_date": r.get("trade_date"),
        "main_net_inflow": main,
        "super_large_net": super_large,
        "large_net": large,
        "medium_net": medium,
        "small_net": small,
        "main_side_net": main_calc,
        "retail_proxy_net": retail_calc,
        "behavior_tags": tags,
        "streak_days": streak,
        "northbound_hold_ratio": r.get("northbound_hold_ratio"),
        "margin_balance": r.get("margin_balance"),
        "on_lhb": bool(r.get("on_lhb")),
        "lhb_institution": lhb if lhb.get("available") else None,
        "bias": bias,
        "notes": notes,
        "invalidation": "东财算法估算口径；非真实机构/游资/散户逐笔；涨停/一字板可能失真",
    }


def ensure_symbol_capital_flow(symbol: str) -> dict[str, Any]:
    """Fetch online if missing locally (on-demand for chat)."""
    sym = _normalize_symbol(symbol)
    init_db()
    with get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM capital_flow WHERE symbol = ?", (sym,)).fetchone()["c"]
    if n:
        return {"status": "cached", "symbol": sym}
    try:
        df = fetch_stock_fund_flow(sym)
        if df.empty:
            return {"status": "failed", "symbol": sym, "error": "empty fund flow"}
        save_capital_flow(df)
        return {"status": "ok", "symbol": sym, "rows": len(df)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "symbol": sym, "error": str(exc)}


def collect_market_participant_context(*, sector_keywords: list[str] | None = None) -> dict[str, Any]:
    """Northbound + optional sector fund flow for market/sector questions."""
    from concurrent.futures import ThreadPoolExecutor

    kws = [k for k in (sector_keywords or []) if k and len(k) >= 2]
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_nb = pool.submit(fetch_northbound_summary)
        fut_ind = pool.submit(fetch_sector_fund_flow_rank, sector_type="行业资金流")
        nb = fut_nb.result()
        industry = fut_ind.result()

    # 行业已在缓存；有关键词时再补概念匹配（避免与行业请求并行双打）
    matched = match_sector_fund_flows(kws) if kws else []

    out: dict[str, Any] = {
        "northbound": nb,
        "note": (
            "参与者行为为估算/上榜日明细，须结合 Skill 做意图推断，禁止当逐笔事实。"
            "大盘级资金细分仅有行业主力净流入排行；公募/游资席位比例仅龙虎榜上榜个股才有，本链路不提供。"
        ),
    }
    if matched:
        out["sector_fund_flow_matched"] = matched
    if industry.get("available"):
        out["sector_fund_flow_industry_top"] = (industry.get("top_inflow") or [])[:8]
        out["sector_fund_flow_meta"] = {
            "available": True,
            "source": industry.get("source"),
            "note": industry.get("note"),
        }
    else:
        out["sector_fund_flow_meta"] = {
            "available": False,
            "error": humanize_fetch_error(industry.get("error")),
            "note": "行业主力净流入排行本轮未返回；禁止改口成公募/游资比例不明。",
        }
    return out
