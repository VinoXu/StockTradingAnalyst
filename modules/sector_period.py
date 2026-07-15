"""Period (multi-day) sector board returns — semantic-driven compute, not daily quote reuse."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Literal

import pandas as pd

from modules.akshare_client import configure_akshare_environment, fetch_board_index_hist

BoardKind = Literal["industry", "concept"]

# 日历跨度略宽于交易日，保证 hist 窗口够用
_CALENDAR_PAD = 18


def infer_sector_lookback_trading_days(message: str) -> int | None:
    """从自然语言推断区间交易日数；无明确窗口则返回 None。"""
    msg = (message or "").strip()
    if not msg:
        return None

    m = re.search(r"近?\s*(\d+)\s*个?交易日", msg)
    if m:
        return max(2, min(60, int(m.group(1))))

    m = re.search(r"近?\s*(\d+)\s*周", msg)
    if m:
        return max(2, min(60, int(m.group(1)) * 5))

    if re.search(r"近?\s*两\s*周|近?\s*2\s*周|十四天|14\s*天", msg):
        return 10
    if re.search(r"近?\s*三\s*周|近?\s*3\s*周", msg):
        return 15
    if re.search(r"近?\s*一\s*周|近?\s*1\s*周|近?\s*7\s*天|近七天", msg):
        return 5
    if re.search(r"近?\s*半\s*个?月|近?\s*15\s*天", msg):
        return 10
    if re.search(r"近?\s*一\s*个?月|近?\s*30\s*天|近月", msg):
        return 20

    m = re.search(r"近?\s*(\d+)\s*天", msg)
    if m:
        # 日历日近似折算交易日
        return max(2, min(60, int(round(int(m.group(1)) * 5 / 7))))

    return None


def wants_sector_period_rank(message: str, *, lookback: int | None = None) -> bool:
    """用户是否在要区间排行（涨/跌榜），而非仅看当日涨跌幅。"""
    msg = (message or "").strip()
    days = lookback if lookback is not None else infer_sector_lookback_trading_days(msg)
    if not days:
        return False
    rankish = any(
        k in msg
        for k in (
            "排行",
            "排名",
            "榜",
            "最多",
            "最弱",
            "最强",
            "跌幅",
            "涨幅",
            "跌的",
            "涨的",
            "靠前",
            "靠后",
            "统计",
            "哪些",
        )
    )
    sectorish = any(k in msg for k in ("板块", "行业", "概念", "题材", "主线"))
    return rankish or sectorish


def _close_series(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for col in ("收盘价", "收盘", "close", "Close"):
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            return s if len(s) else None
    return None


def _period_return_pct(df: pd.DataFrame, trading_days: int) -> tuple[float | None, str, str]:
    closes = _close_series(df)
    if closes is None or len(closes) < 2:
        return None, "", ""
    need = trading_days + 1
    work = closes.tail(need)
    if len(work) < 2:
        return None, "", ""
    c0 = float(work.iloc[0])
    c1 = float(work.iloc[-1])
    if c0 == 0:
        return None, "", ""
    start_d = end_d = ""
    date_col = None
    for col in ("日期", "date", "Date"):
        if col in df.columns:
            date_col = col
            break
    if date_col:
        dates = df[date_col].astype(str).tail(len(work))
        start_d = str(dates.iloc[0])[:10]
        end_d = str(dates.iloc[-1])[:10]
    return (c1 / c0 - 1.0) * 100.0, start_d, end_d


def _one_board_return(
    *,
    name: str,
    board_type: BoardKind,
    start_date: str,
    end_date: str,
    trading_days: int,
) -> dict[str, Any] | None:
    try:
        df = fetch_board_index_hist(
            board_type=board_type,
            symbol=name,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:  # noqa: BLE001
        return None
    ret, start_d, end_d = _period_return_pct(df, trading_days)
    if ret is None:
        return None
    return {
        "name": name,
        "board_type": "行业" if board_type == "industry" else "概念",
        "period_return_pct": round(ret, 2),
        "start_date": start_d,
        "end_date": end_d,
        "trading_days": trading_days,
    }


def _pick_concept_names(sectors: dict[str, Any], *, limit: int = 24) -> list[str]:
    """概念全量过慢：取当日涨跌极端样本做区间复核。"""
    block = (sectors or {}).get("concept") or {}
    if not block.get("available"):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for field in ("top_losers", "top_gainers"):
        for row in block.get(field) or []:
            name = str((row or {}).get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
            if len(names) >= limit:
                return names
    for row in block.get("all_boards") or []:
        name = str((row or {}).get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _industry_names(sectors: dict[str, Any]) -> list[str]:
    block = (sectors or {}).get("industry") or {}
    if not block.get("available"):
        return []
    rows = block.get("all_boards") or []
    if not rows:
        rows = (block.get("top_losers") or []) + (block.get("top_gainers") or [])
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = str((row or {}).get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_sector_period_rank(
    sectors: dict[str, Any],
    *,
    trading_days: int = 10,
    top_n: int = 12,
    include_concepts: bool = True,
    concept_limit: int = 24,
    max_workers: int = 8,
) -> dict[str, Any]:
    """
    按板块指数收盘价计算近 N 个交易日累计收益，并排出涨/跌榜。
    行业尽量全量；概念取当日极端样本（避免同花顺概念指数冷启动过慢）。
    """
    configure_akshare_environment()
    days = max(2, min(60, int(trading_days)))
    end = datetime.now()
    start = end - timedelta(days=days + _CALENDAR_PAD)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    jobs: list[tuple[str, BoardKind]] = [
        (n, "industry") for n in _industry_names(sectors)
    ]
    if include_concepts:
        jobs.extend((n, "concept") for n in _pick_concept_names(sectors, limit=concept_limit))

    if not jobs:
        return {
            "available": False,
            "error": "no board names for period rank",
            "trading_days": days,
        }

    rows: list[dict[str, Any]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            pool.submit(
                _one_board_return,
                name=name,
                board_type=kind,
                start_date=start_s,
                end_date=end_s,
                trading_days=days,
            )
            for name, kind in jobs
        ]
        for fut in as_completed(futs):
            item = fut.result()
            if item:
                rows.append(item)
            else:
                errors += 1

    if not rows:
        return {
            "available": False,
            "error": "period hist empty",
            "trading_days": days,
            "scanned": len(jobs),
            "errors": errors,
        }

    losers = sorted(rows, key=lambda r: float(r["period_return_pct"]))[:top_n]
    gainers = sorted(rows, key=lambda r: float(r["period_return_pct"]), reverse=True)[:top_n]
    window = ""
    sample = losers[0] if losers else rows[0]
    if sample.get("start_date") and sample.get("end_date"):
        window = f"{sample['start_date']}→{sample['end_date']}"

    return {
        "available": True,
        "trading_days": days,
        "window": window,
        "top_losers": losers,
        "top_gainers": gainers,
        "scanned": len(jobs),
        "ok": len(rows),
        "errors": errors,
        "source": "ths_board_index",
        "note": (
            f"区间收益按板块指数近{days}个交易日收盘价计算（同花顺）；"
            "行业尽量全量，概念为当日涨跌极端样本复核。"
        ),
    }
