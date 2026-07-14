"""Financial numeric rigor: Decimal math and cross-validation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _dec(val: Any) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def verify_yoy(current: Any, previous: Any, *, reported_yoy: Any = None, tolerance_pct: float = 1.5) -> dict[str, Any]:
    cur, prev = _dec(current), _dec(previous)
    if cur is None or prev is None or prev == 0:
        return {"ok": False, "reason": "缺少同比基数"}
    calc = (cur - prev) / abs(prev) * Decimal("100")
    out: dict[str, Any] = {
        "ok": True,
        "calculated_yoy_pct": float(round(calc, 4)),
        "current": float(cur),
        "previous": float(prev),
    }
    rep = _dec(reported_yoy)
    if rep is not None:
        diff = abs(calc - rep)
        out["reported_yoy_pct"] = float(rep)
        out["delta_pct"] = float(round(diff, 4))
        out["ok"] = diff <= Decimal(str(tolerance_pct))
        if not out["ok"]:
            out["warning"] = f"同比复算与报告值偏差 {float(diff):.2f}%"
    return out


def verify_market_cap(
    *,
    price: Any,
    shares: Any,
    reported_cap: Any,
    tolerance_pct: float = 1.0,
) -> dict[str, Any]:
    p, sh, cap = _dec(price), _dec(shares), _dec(reported_cap)
    if p is None or sh is None or cap is None or cap == 0:
        return {"ok": False, "reason": "市值验算缺字段"}
    calc = p * sh
    diff_pct = abs(calc - cap) / cap * Decimal("100")
    ok = diff_pct <= Decimal(str(tolerance_pct))
    return {
        "ok": ok,
        "calculated_cap": float(calc),
        "reported_cap": float(cap),
        "delta_pct": float(round(diff_pct, 4)),
        "warning": None if ok else f"市值偏差 {float(diff_pct):.2f}%",
    }


def verify_pe(market_cap: Any, net_profit: Any, *, reported_pe: Any = None, tolerance_pct: float = 3.0) -> dict[str, Any]:
    cap, profit = _dec(market_cap), _dec(net_profit)
    if cap is None or profit is None or profit == 0:
        return {"ok": False, "reason": "PE 验算缺字段"}
    calc = cap / profit
    out: dict[str, Any] = {
        "ok": True,
        "calculated_pe": float(round(calc, 4)),
    }
    rep = _dec(reported_pe)
    if rep is not None and rep != 0:
        diff_pct = abs(calc - rep) / abs(rep) * Decimal("100")
        out["reported_pe"] = float(rep)
        out["delta_pct"] = float(round(diff_pct, 4))
        out["ok"] = diff_pct <= Decimal(str(tolerance_pct))
        if not out["ok"]:
            out["warning"] = f"PE 偏差 {float(diff_pct):.2f}%"
    return out


def cross_validate_pair(a: Any, b: Any, *, label: str = "value", tolerance_pct: float = 2.0) -> dict[str, Any]:
    da, db = _dec(a), _dec(b)
    if da is None or db is None or db == 0:
        return {"ok": False, "label": label, "reason": "缺少对比值"}
    diff_pct = abs(da - db) / abs(db) * Decimal("100")
    ok = diff_pct <= Decimal(str(tolerance_pct))
    return {
        "ok": ok,
        "label": label,
        "a": float(da),
        "b": float(db),
        "delta_pct": float(round(diff_pct, 4)),
        "warning": None if ok else f"{label} 双源偏差 {float(diff_pct):.2f}%",
    }


def run_fundamental_checks(pack: dict[str, Any]) -> dict[str, Any]:
    """Run rigor checks on one symbol fundamentals pack."""
    if not pack.get("available"):
        return {"available": False}

    highlights = pack.get("highlights") or {}
    ths = (pack.get("ths_abstract") or {}).get("latest") or {}
    value = (pack.get("valuation") or {}).get("snapshot") or {}
    sina = (pack.get("sina_profit") or {}).get("latest") or {}

    checks: list[dict[str, Any]] = []

    if value.get("close") and value.get("total_shares") and value.get("market_cap"):
        checks.append(
            verify_market_cap(
                price=value.get("close"),
                shares=value.get("total_shares"),
                reported_cap=value.get("market_cap"),
            )
        )

    if highlights.get("market_cap") and highlights.get("net_profit") and highlights.get("pe_ttm"):
        checks.append(
            verify_pe(
                highlights.get("market_cap"),
                highlights.get("net_profit"),
                reported_pe=highlights.get("pe_ttm"),
            )
        )

    if sina.get("net_profit") is not None and ths.get("net_profit") is not None:
        checks.append(
            cross_validate_pair(
                sina.get("net_profit"),
                ths.get("net_profit"),
                label="净利润_ths_vs_sina",
                tolerance_pct=5.0,
            )
        )

    warnings = [c["warning"] for c in checks if c.get("warning")]
    return {
        "available": bool(checks),
        "checks": checks,
        "all_ok": all(c.get("ok") for c in checks) if checks else False,
        "warnings": warnings,
        "confidence_downgrade": bool(warnings),
    }
