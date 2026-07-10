"""Industry / concept board rankings with East Money + THS fallback."""

from __future__ import annotations

from typing import Any, Callable

from modules.akshare_client import (
    _fetch_em_clist_pages,
    fetch_ths_concept_board_summary,
    fetch_ths_industry_board_summary,
)
from modules.sector_ta import rank_boards_by_ta

_INDUSTRY_FS = "m:90+t:2+f:!50"
_CONCEPT_FS = "m:90+t:3+f:!50"
_BOARD_FIELDS = "f3,f12,f14,f104,f105,f128,f136"


def _parse_board_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boards: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("f14") or "").strip()
        if not name:
            continue
        try:
            change_pct = float(row.get("f3") or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        boards.append(
            {
                "name": name,
                "code": str(row.get("f12") or ""),
                "change_pct": change_pct,
                "rising_count": row.get("f104"),
                "falling_count": row.get("f105"),
                "lead_stock": str(row.get("f128") or ""),
                "lead_change_pct": row.get("f136"),
            }
        )
    return boards


def _rank_boards(boards: list[dict[str, Any]], *, top_n: int = 12) -> dict[str, Any]:
    if not boards:
        return {"available": False, "error": "empty board list"}
    sorted_boards = sorted(boards, key=lambda x: x["change_pct"], reverse=True)
    return {
        "available": True,
        "top_gainers": sorted_boards[:top_n],
        "top_losers": sorted(boards, key=lambda x: x["change_pct"])[:top_n],
        "all_boards": sorted_boards,
        "count": len(boards),
    }


def _fetch_boards_em(fs: str) -> list[dict[str, Any]]:
    rows = _fetch_em_clist_pages(fields=_BOARD_FIELDS, fs=fs, pz=500, fid="f3")
    return _parse_board_rows(rows)


def _classify_em_error(msg: str | None) -> str:
    if not msg:
        return "unknown"
    lower = msg.lower()
    if "curl" in lower or "connection" in lower or "abruptly" in lower:
        return "eastmoney_blocked_use_ths"
    return "eastmoney_error"


def _fetch_boards_with_fallback(
    *,
    board_type: str,
    fs: str,
    ths_fetcher: Callable[[], list[dict[str, Any]]],
    top_n: int,
) -> tuple[dict[str, Any], str | None]:
    em_err: str | None = None
    try:
        boards = _fetch_boards_em(fs)
        ranked = _rank_boards(boards, top_n=top_n)
        ranked["board_type"] = board_type
        ranked["source"] = f"eastmoney_{board_type}"
        return ranked, None
    except Exception as exc:  # noqa: BLE001
        em_err = str(exc)

    try:
        boards = ths_fetcher()
        ranked = _rank_boards(boards, top_n=top_n)
        ranked["board_type"] = board_type
        ranked["source"] = f"ths_{board_type}"
        ranked["fallback_from"] = "eastmoney"
        ranked["fallback_reason"] = _classify_em_error(em_err)
        return ranked, em_err
    except Exception as ths_exc:  # noqa: BLE001
        return {"available": False, "error": str(ths_exc)}, em_err


def collect_sector_rankings(*, top_n: int = 12) -> dict[str, Any]:
    """Industry + concept board gainers/losers for sector-level chat."""
    industry, industry_em_err = _fetch_boards_with_fallback(
        board_type="industry",
        fs=_INDUSTRY_FS,
        ths_fetcher=fetch_ths_industry_board_summary,
        top_n=top_n,
    )
    concept, concept_em_err = _fetch_boards_with_fallback(
        board_type="concept",
        fs=_CONCEPT_FS,
        ths_fetcher=fetch_ths_concept_board_summary,
        top_n=top_n,
    )

    ok = industry.get("available") or concept.get("available")
    sources = [b.get("source") for b in (industry, concept) if b.get("available")]
    return {
        "available": ok,
        "industry": industry,
        "concept": concept,
        "errors": {"industry": industry_em_err, "concept": concept_em_err},
        "data_sources": sources,
        "note": (
            "行业/概念板块来自行情数据源（东财或同花顺备用），与 LLM/阿里云 API 无关。"
            "回答板块级机会时优先用此数据，勿展开自选股。"
        ),
    }


def _merge_top_boards(sectors: dict[str, Any], *, gainers: bool, top_n: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for key, label in (("industry", "行业"), ("concept", "概念")):
        block = sectors.get(key) or {}
        if not block.get("available"):
            continue
        field = "top_gainers" if gainers else "top_losers"
        for row in block.get(field) or []:
            merged.append(
                {
                    "name": row.get("name") or "",
                    "change_pct": row.get("change_pct"),
                    "board_type": label,
                    "rising_count": row.get("rising_count"),
                    "falling_count": row.get("falling_count"),
                    "lead_stock": row.get("lead_stock"),
                    "lead_change_pct": row.get("lead_change_pct"),
                }
            )
    merged.sort(key=lambda x: float(x.get("change_pct") or 0), reverse=gainers)
    return merged[:top_n]


def _collect_all_boards(sectors: dict[str, Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, label in (("industry", "行业"), ("concept", "概念")):
        block = sectors.get(key) or {}
        if not block.get("available"):
            continue
        for row in block.get("all_boards") or []:
            name = row.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            merged.append(
                {
                    "name": name,
                    "code": row.get("code") or "",
                    "change_pct": row.get("change_pct"),
                    "board_type": label,
                    "rising_count": row.get("rising_count"),
                    "falling_count": row.get("falling_count"),
                    "lead_stock": row.get("lead_stock"),
                    "lead_change_pct": row.get("lead_change_pct"),
                }
            )
    return merged


def build_sector_pick_summary(sectors: dict[str, Any], *, top_n: int = 5) -> dict[str, Any]:
    """Rank boards by price-structure TA (lead stock), not daily gain alone."""
    if not sectors.get("available"):
        return {"available": False, "error": "sectors unavailable"}
    all_boards = _collect_all_boards(sectors)
    top = rank_boards_by_ta(all_boards, top_n=top_n, ta_scan_limit=12)
    weak = _merge_top_boards(sectors, gainers=False, top_n=3)
    scan_n = min(12, len(all_boards))
    return {
        "available": bool(top),
        "method": "technical_pattern",
        "top_picks": top,
        "weak_boards": weak,
        "scanned": {
            "industry_count": (sectors.get("industry") or {}).get("count"),
            "concept_count": (sectors.get("concept") or {}).get("count"),
            "boards_total": len(all_boards),
            "ta_scanned": scan_n,
        },
        "note": (
            "预筛选：全量700+板块按广度/蓄势规则筛候选（非涨幅榜），"
            "行业+概念各取名额后共12个做领涨股K线形态分析，最终输出5个优选。"
        ),
    }
