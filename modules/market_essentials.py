"""Ensure market-query essentials always exist as structured fields (value or explicit miss)."""

from __future__ import annotations

import time
from typing import Any

from modules.akshare_client import humanize_fetch_error
from modules.runtime_cache import invalidate

# 大盘 essentials 自愈总预算（秒）：清缓存 + 换源重试，避免拖垮流式首字
_PARTICIPANT_HEAL_BUDGET_SEC = 8.0


def _as_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_yi(amount: float | None) -> str | None:
    if amount is None:
        return None
    return f"{amount / 1e8:.2f}亿"


def _friendly_err(err: Any) -> str:
    if err is None:
        return "未返回可用数据"
    if isinstance(err, Exception):
        return humanize_fetch_error(err)
    return humanize_fetch_error(str(err))


def build_two_market_turnover(
    index_sh: dict[str, Any] | None,
    index_sz: dict[str, Any] | None,
) -> dict[str, Any]:
    """Sum SH+SZ index spot amounts as two-market turnover proxy."""
    sh = index_sh if isinstance(index_sh, dict) else {}
    sz = index_sz if isinstance(index_sz, dict) else {}
    sh_amt = _as_float(sh.get("amount")) if sh.get("available") else None
    sz_amt = _as_float(sz.get("amount")) if sz.get("available") else None
    parts: list[str] = []
    if sh_amt is None:
        parts.append("上证成交额缺失")
    if sz_amt is None:
        parts.append("深证成交额缺失")
    if sh_amt is None and sz_amt is None:
        return {
            "available": False,
            "amount": None,
            "amount_yi": None,
            "amount_yi_text": None,
            "sh_amount": None,
            "sz_amount": None,
            "error": "；".join(parts) or "指数成交额未返回",
            "note": "两市成交额=上证指数成交额+深证成指成交额（指数口径代理，非交易所官方合计）。",
        }
    total = (sh_amt or 0.0) + (sz_amt or 0.0)
    partial = sh_amt is None or sz_amt is None
    return {
        "available": True,
        "partial": partial,
        "amount": total,
        "amount_yi": round(total / 1e8, 2),
        "amount_yi_text": _fmt_yi(total),
        "sh_amount": sh_amt,
        "sz_amount": sz_amt,
        "sh_amount_yi_text": _fmt_yi(sh_amt),
        "sz_amount_yi_text": _fmt_yi(sz_amt),
        "error": "；".join(parts) if partial else None,
        "note": "两市成交额=上证指数成交额+深证成指成交额（指数口径代理，非交易所官方合计）。",
    }


def _missing_index(code: str, name: str, error: str) -> dict[str, Any]:
    return {
        "available": False,
        "kind": "index",
        "code": code,
        "name": name,
        "price": None,
        "amount": None,
        "error": error,
    }


def _normalize_index_slot(
    quote: dict[str, Any] | None,
    *,
    code: str,
    name: str,
) -> dict[str, Any]:
    if isinstance(quote, dict) and quote.get("available") and quote.get("price") is not None:
        out = dict(quote)
        if out.get("amount") is None:
            out["amount_missing"] = True
        return out
    err = None
    if isinstance(quote, dict):
        err = quote.get("error")
    return _missing_index(code, name, err or f"{name}盘中价本轮未返回")


def ensure_market_essentials(market: dict[str, Any] | None, *, retry: bool = True) -> dict[str, Any]:
    """Attach index live / breadth / dow / two-market turnover; retry once if thin."""
    from modules.realtime_quotes import get_live_quote

    out = dict(market or {})

    slots = (
        ("index_live", "INDEX.SH000001", "000001", "上证指数"),
        ("index_live_sz", "INDEX.SZ399001", "399001", "深证成指"),
    )
    need_refresh: list[tuple[str, str, str, str, Any]] = []
    for key, sym, code, name in slots:
        q = out.get(key)
        need_retry = retry and (
            not isinstance(q, dict)
            or not q.get("available")
            or q.get("price") is None
            or q.get("amount") is None
        )
        if need_retry:
            need_refresh.append((key, sym, code, name, q))
        else:
            out[key] = _normalize_index_slot(q if isinstance(q, dict) else None, code=code, name=name)

    if need_refresh:
        from concurrent.futures import ThreadPoolExecutor

        def _refresh(item: tuple[str, str, str, str, Any]) -> tuple[str, str, str, dict[str, Any]]:
            key, sym, code, name, q = item
            try:
                fresh = get_live_quote(sym, force_refresh=True) or q
            except Exception as exc:  # noqa: BLE001
                fresh = {"available": False, "error": str(exc) or type(exc).__name__}
            return key, code, name, fresh if isinstance(fresh, dict) else {}

        with ThreadPoolExecutor(max_workers=2) as pool:
            for key, code, name, fresh in pool.map(_refresh, need_refresh):
                out[key] = _normalize_index_slot(fresh, code=code, name=name)

    breadth = out.get("breadth")
    if not isinstance(breadth, dict):
        out["breadth"] = {"available": False, "error": "本轮未拉取涨跌家数"}
    elif not breadth.get("available"):
        b = dict(breadth)
        b.setdefault("error", "涨跌家数接口未返回可用数据")
        out["breadth"] = b

    dow = out.get("dow")
    if not isinstance(dow, dict):
        out["dow"] = {"available": False, "state_cn": "缺失", "error": "本轮未拉取双指数结构"}
    elif not dow.get("available"):
        d = dict(dow)
        d.setdefault("state_cn", d.get("state_cn") or "缺失")
        d.setdefault("error", "双指数历史日K不可用")
        out["dow"] = d

    out["two_market_turnover"] = build_two_market_turnover(out.get("index_live"), out.get("index_live_sz"))

    missing: list[str] = []
    if not (out.get("breadth") or {}).get("available"):
        missing.append("涨跌家数")
    if not (out.get("index_live") or {}).get("available"):
        missing.append("上证盘中价")
    if not (out.get("index_live_sz") or {}).get("available"):
        missing.append("深证盘中价")
    if not (out.get("two_market_turnover") or {}).get("available"):
        missing.append("两市成交额")
    if not (out.get("dow") or {}).get("available"):
        missing.append("双指数结构")

    out["essentials"] = {
        "ok": not missing,
        "missing": missing,
        "note": "大盘问答必填：涨跌家数、上证/深证盘中价与成交额、双指数结构；缺项须原样告知，禁止编造。",
    }
    return out


def ensure_participant_essentials(
    participant_flow: dict[str, Any] | None,
    *,
    retry: bool = True,
) -> dict[str, Any]:
    """Guarantee northbound + market-level fund structure keys always exist.

    On miss: invalidate cache and re-fetch within a short budget (curl EM → ak → THS
    already runs inside fetch_sector_fund_flow_rank). Errors are humanized so the
    model does not echo raw OSError text like「Invalid argument」.
    """
    from modules.participant_flow import fetch_northbound_summary, fetch_sector_fund_flow_rank

    out = dict(participant_flow or {})
    deadline = time.monotonic() + (_PARTICIPANT_HEAL_BUDGET_SEC if retry else 0.0)

    nb = out.get("northbound")
    weak = (
        not isinstance(nb, dict)
        or not nb.get("available")
        or nb.get("total_net_buy") is None
    )
    if retry and weak and time.monotonic() < deadline:
        try:
            invalidate("data:northbound_summary")
            nb = fetch_northbound_summary()
        except Exception as exc:  # noqa: BLE001
            nb = {"available": False, "error": humanize_fetch_error(exc)}

    if not isinstance(nb, dict):
        nb = {"available": False, "error": "北向摘要异常"}
    else:
        nb = dict(nb)

    if nb.get("available"):
        net = _as_float(nb.get("total_net_buy"))
        if net is None:
            nb["status_note"] = "接口已返回但净买字段为空（不等于未调用接口）"
        elif net == 0.0:
            nb["status_note"] = (
                "接口已返回；北向合计净买为 0（盘中未更新或当日暂无净买，不等于接口失败）"
            )
        else:
            nb["status_note"] = "接口已返回有效北向净买"
        nb.pop("error", None)
    else:
        nb["error"] = _friendly_err(nb.get("error") or "北向资金接口未返回可用数据")
        nb["status_note"] = f"本轮未核实：{nb['error']}"
    out["northbound"] = nb

    top = out.get("sector_fund_flow_industry_top")
    if not top:
        industry: dict[str, Any] = {"available": False, "error": "未拉取"}
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            if attempt > 0:
                if time.monotonic() >= deadline:
                    break
                invalidate("data:sector_flow:行业资金流")
                # 短暂让路，降低同 IP 连打触发风控的概率
                time.sleep(0.4)
            industry = fetch_sector_fund_flow_rank(sector_type="行业资金流", top_n=8)
            if industry.get("available"):
                break
        if industry.get("available"):
            top = (industry.get("top_inflow") or [])[:8]
            out["sector_fund_flow_industry_top"] = top
            src = industry.get("source") or ""
            src_note = ""
            if src == "ths_industry_instant":
                src_note = "本轮东财不可用，已切换同花顺即时行业净额（口径可能略有差异）。"
            elif industry.get("fallback_of"):
                src_note = "本轮已自动换源补全。"
            out["fund_structure"] = {
                "available": True,
                "kind": "sector_main_net",
                "source": src or None,
                "top_inflow": top,
                "healed": bool(industry.get("fallback_of")),
                "note": (
                    "大盘级资金细分=行业主力净流入排行（东财/同花顺估算）。"
                    "不是公募/游资席位比例；后者仅龙虎榜上榜个股才有。"
                    + src_note
                ),
            }
        else:
            err = _friendly_err(industry.get("error") or "板块资金流未返回")
            out["fund_structure"] = {
                "available": False,
                "kind": "sector_main_net",
                "error": err,
                "note": (
                    "行业主力净流入排行本轮未返回（已自动重试/换源仍失败）。"
                    "禁止改口成「公募/游资比例不明」——本链路本来就不提供该比例。"
                    "禁止把底层 Errno/Invalid argument 复述成业务参数错误。"
                ),
            }
    else:
        out["fund_structure"] = {
            "available": True,
            "kind": "sector_main_net",
            "top_inflow": top[:8] if isinstance(top, list) else top,
            "note": (
                "大盘级资金细分=行业主力净流入排行（东财/同花顺估算）。"
                "不是公募/游资席位比例；后者仅龙虎榜上榜个股才有。"
            ),
        }

    out.setdefault(
        "note",
        "参与者行为为估算/上榜日明细，须结合 Skill 做意图推断，禁止当逐笔事实。",
    )
    return out
