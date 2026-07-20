"""Robust AKShare request helpers: timeout, retry, rate limit, fallback."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

import akshare as ak
import pandas as pd

# requests timeout: (connect, read)
DEFAULT_TIMEOUT: tuple[float, float] = (10, 15)
READ_TIMEOUT: float = DEFAULT_TIMEOUT[1]

# Anti-ban delay before each external call
REQUEST_DELAY: tuple[float, float] = (2.0, 4.0)

T = TypeVar("T")


def configure_akshare_environment() -> None:
    """Apply network-related defaults once at process start."""
    if hasattr(ak, "set_timeout"):
        ak.set_timeout(DEFAULT_TIMEOUT)

    # Step 5: bypass broken corporate proxies for data endpoints
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)


def _sleep_before_request() -> None:
    time.sleep(random.uniform(*REQUEST_DELAY))


def fetch_with_retry(
    fn: Callable[..., T],
    *args: Any,
    max_retry: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> T:
    """Exponential backoff wrapper for AKShare calls."""
    configure_akshare_environment()
    delay = base_delay
    last_error: Exception | None = None

    for attempt in range(max_retry):
        _sleep_before_request()
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - network errors vary by layer
            last_error = exc
            if attempt < max_retry - 1:
                time.sleep(delay)
                delay *= 2
    raise last_error or RuntimeError("AKShare request failed")


def _to_tx_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
    return f"{prefix}{code}"


def _to_tx_date(date: str) -> str:
    if "-" in date:
        return date
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _normalize_tx_hist(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Map Tencent K-line columns to East Money schema used by data_fetcher."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "amount": "成交量",
        }
    )
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    df["成交额"] = None
    df["换手率"] = None
    df["股票代码"] = symbol
    return df


def _fetch_daily_hist_em(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> pd.DataFrame:
    return fetch_with_retry(
        ak.stock_zh_a_hist,
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
        timeout=READ_TIMEOUT,
    )


def _fetch_daily_hist_tx(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> pd.DataFrame:
    raw = fetch_with_retry(
        ak.stock_zh_a_hist_tx,
        symbol=_to_tx_symbol(symbol),
        start_date=_to_tx_date(start_date),
        end_date=_to_tx_date(end_date),
        adjust=adjust,
        timeout=READ_TIMEOUT,
    )
    return _normalize_tx_hist(raw, symbol)


def fetch_daily_hist(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Fetch daily bars; East Money first, Tencent fallback when EM is blocked."""
    em_err = "empty"
    try:
        df = _fetch_daily_hist_em(symbol, start_date, end_date, adjust)
        if df is not None and not df.empty:
            return df
    except Exception as exc:  # noqa: BLE001
        em_err = str(exc) or type(exc).__name__
    try:
        df = _fetch_daily_hist_tx(symbol, start_date, end_date, adjust)
        if df is not None and not df.empty:
            return df
        raise RuntimeError(
            f"东财与腾讯均无日K（东财：{em_err}；腾讯：empty）"
        )
    except Exception as exc:  # noqa: BLE001
        tx_err = str(exc) or type(exc).__name__
        if "东财与腾讯" in tx_err:
            raise
        raise RuntimeError(
            f"东财与腾讯行情均失败（东财：{em_err}；腾讯：{tx_err}）"
        ) from exc


def fetch_a_spot_em() -> pd.DataFrame:
    """All A-share intraday spot quotes (East Money). Slow (~60–90s); prefer intraday per symbol."""
    return fetch_with_retry(ak.stock_zh_a_spot_em)


def fetch_index_spot_em() -> pd.DataFrame:
    """Major index intraday spot quotes (East Money)."""
    return fetch_with_retry(ak.stock_zh_index_spot_em, max_retry=3, base_delay=1.5)


def _index_code_to_tx(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith("399") or c.startswith("159"):
        return f"sz{c}"
    return f"sh{c}"


def fetch_index_quotes_tencent(codes: list[str]) -> dict[str, dict[str, Any]]:
    """Intraday index quotes via Tencent qt.gtimg.cn (fallback when East Money is blocked)."""
    import urllib.error
    import urllib.request

    wanted = [str(c).strip() for c in codes if str(c).strip()]
    if not wanted:
        return {}
    # Batch request: q=sh000001,sz399001
    tx_syms = [_index_code_to_tx(c) for c in wanted]
    url = "https://qt.gtimg.cn/q=" + ",".join(tx_syms)
    out: dict[str, dict[str, Any]] = {}
    try:
        configure_akshare_environment()
        with urllib.request.urlopen(url, timeout=READ_TIMEOUT) as resp:
            raw = resp.read()
        text = ""
        for encoding in ("gbk", "gb18030", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            return {}
        for chunk in text.replace("\r", "").split(";\n"):
            chunk = chunk.strip().rstrip(";")
            if not chunk or "=" not in chunk or "~" not in chunk:
                continue
            try:
                payload = chunk.split("=", 1)[1].strip().strip(";")
                if payload.startswith('"') and payload.endswith('"'):
                    payload = payload[1:-1]
                parts = payload.split("~")
            except (IndexError, ValueError):
                continue
            if len(parts) < 6:
                continue
            code = str(parts[2]).strip()
            try:
                price = float(parts[3]) if parts[3] else None
            except ValueError:
                price = None
            if price is None:
                continue

            def _f(idx: int) -> float | None:
                if idx >= len(parts) or parts[idx] in ("", None):
                    return None
                try:
                    return float(parts[idx])
                except ValueError:
                    return None

            amount = None
            # field 35 like "price/volume/amount"
            if len(parts) > 35 and "/" in parts[35]:
                segs = parts[35].split("/")
                if len(segs) >= 3:
                    try:
                        amount = float(segs[2])
                    except ValueError:
                        amount = None
            if amount is None:
                amount = _f(37)
                if amount is not None and amount < 1e10:
                    amount = amount * 10000.0

            qt = str(parts[30]) if len(parts) > 30 else ""
            if len(qt) >= 12 and qt.isdigit():
                quote_time = f"{qt[0:4]}-{qt[4:6]}-{qt[6:8]} {qt[8:10]}:{qt[10:12]}"
            else:
                quote_time = datetime.now().strftime("%Y-%m-%d %H:%M")

            out[code] = {
                "available": True,
                "kind": "index",
                "code": code,
                "name": parts[1].strip() if parts[1] else code,
                "price": price,
                "change_pct": _f(32),
                "change_amount": _f(31),
                "open": _f(5),
                "high": _f(33),
                "low": _f(34),
                "prev_close": _f(4),
                "volume": _f(36) or _f(6),
                "amount": amount,
                "turnover_rate": None,
                "quote_time": quote_time,
                "source": "tencent_gtimg",
            }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}
    return out


def fetch_index_spot_with_fallback(
    needed_codes: list[str] | None = None,
) -> tuple[pd.DataFrame | None, dict[str, dict[str, Any]]]:
    """East Money index spot first (with retry), then Tencent fill for missing codes."""
    needed = [str(c).strip() for c in (needed_codes or ["000001", "399001", "399006"])]
    em_df: pd.DataFrame | None = None
    try:
        em_df = fetch_index_spot_em()
        if em_df is not None and not em_df.empty:
            present = {str(r).strip() for r in em_df.get("代码", pd.Series(dtype=str)).astype(str)}
            missing = [c for c in needed if c not in present]
            if not missing:
                return em_df, {}
            tx = fetch_index_quotes_tencent(missing)
            return em_df, tx
    except Exception:
        em_df = None
    tx = fetch_index_quotes_tencent(needed)
    return em_df, tx


def fetch_intraday_em(symbol: str) -> pd.DataFrame:
    """Single-stock intraday ticks. Fast-fail (no multi-retry) to avoid blocking UI."""
    configure_akshare_environment()
    return ak.stock_intraday_em(symbol=symbol)


def fetch_stock_name_tencent(code: str) -> str | None:
    """A-share short name via Tencent qt.gtimg.cn (fallback when East Money is blocked)."""
    import urllib.error
    import urllib.request

    code = str(code).strip().zfill(6)
    if not code.isdigit():
        return None
    url = f"https://qt.gtimg.cn/q={_to_tx_symbol(code)}"
    try:
        configure_akshare_environment()
        with urllib.request.urlopen(url, timeout=READ_TIMEOUT) as resp:
            raw = resp.read()
        for encoding in ("gbk", "gb18030", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                text = ""
        if not text or "~" not in text:
            return None
        payload = text.split('"')[1] if '"' in text else text
        parts = payload.split("~")
        if len(parts) < 2:
            return None
        name = parts[1].strip()
        return name or None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def resolve_stock_code_by_name(name: str) -> str | None:
    """Resolve A-share 6-digit code from short name via Tencent smartbox."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean = (name or "").strip()
    if not clean:
        return None
    if clean.isdigit() and len(clean) == 6:
        return clean

    url = "https://smartbox.gtimg.cn/s3/?" + urllib.parse.urlencode({"v": "2", "q": clean, "t": "all"})
    try:
        configure_akshare_environment()
        with urllib.request.urlopen(url, timeout=READ_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        if "v_hint=" not in text:
            return None
        payload = text.split("v_hint=", 1)[1].strip().strip('"').strip("'")
        for part in payload.split("^"):
            fields = part.split("~")
            if len(fields) < 3:
                continue
            market, code, nm = fields[0], fields[1], fields[2]
            if market not in ("sh", "sz") or not code.isdigit():
                continue
            if nm == clean or clean in nm or nm in clean:
                return code.zfill(6)
        first = payload.split("^")[0].split("~")
        if len(first) >= 2 and first[0] in ("sh", "sz") and first[1].isdigit():
            return first[1].zfill(6)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return None


_EM_CLIST_HOSTS = (
    "https://82.push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
    "https://push2.eastmoney.com",
)

# 与 AKShare stock_zh_a_spot_em 一致：沪深京 A 股
_EM_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


def _fetch_em_clist_pages(
    *,
    fields: str = "f3,f12,f14",
    fs: str | None = None,
    pz: int = 5000,
    fid: str = "f3",
) -> list[dict[str, Any]]:
    """Paginated East Money clist; curl_cffi + multi-host fallback."""
    import math

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("curl_cffi required for East Money clist") from exc

    configure_akshare_environment()
    headers = {
        "Referer": "https://quote.eastmoney.com/center/gridlist.html",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    base_params = {
        "pz": str(pz),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": fs or _EM_A_SHARE_FS,
        "fields": fields,
    }

    last_error: Exception | None = None
    for host in _EM_CLIST_HOSTS:
        rows: list[dict[str, Any]] = []
        try:
            pn = 1
            total = None
            while True:
                params = {**base_params, "pn": str(pn)}
                url = f"{host}/api/qt/clist/get"
                resp = curl_requests.get(
                    url,
                    params=params,
                    headers=headers,
                    impersonate="chrome",
                    timeout=READ_TIMEOUT,
                )
                resp.raise_for_status()
                payload = resp.json()
                block = payload.get("data") or {}
                batch = block.get("diff") or []
                if total is None:
                    total = int(block.get("total") or 0)
                if not batch:
                    break
                rows.extend(batch)
                if total and len(rows) >= total:
                    break
                per_page = len(batch)
                if per_page <= 0:
                    break
                max_page = math.ceil(total / per_page) if total else pn
                if pn >= max_page:
                    break
                pn += 1
                time.sleep(random.uniform(0.25, 0.6))
            if rows:
                return rows
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise last_error or RuntimeError("East Money clist unreachable")


def _ths_cookie_headers() -> dict[str, str]:
    """THS q.10jqka.com.cn requests need anti-bot cookie from ths.js."""
    import py_mini_racer
    from akshare.datasets import get_ths_js

    js_code = py_mini_racer.MiniRacer()
    with open(get_ths_js("ths.js"), encoding="utf-8") as f:
        js_code.eval(f.read())
    v_code = js_code.call("v")
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": f"v={v_code}",
    }


def fetch_board_index_hist(
    *,
    board_type: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    板块指数日线。默认同花顺优先（东财板指数在本环境常被拦且超时），
    失败再试东财。区间排行批量调用时不走 REQUEST_DELAY。
    """
    configure_akshare_environment()
    name = (symbol or "").strip()
    if not name:
        return pd.DataFrame()
    kind = (board_type or "industry").strip().lower()

    # Tonghuashun first
    try:
        if kind == "concept":
            df = ak.stock_board_concept_index_ths(
                symbol=name, start_date=start_date, end_date=end_date
            )
        else:
            df = ak.stock_board_industry_index_ths(
                symbol=name, start_date=start_date, end_date=end_date
            )
        if df is not None and not df.empty:
            return df
    except Exception:  # noqa: BLE001
        pass

    # East Money fallback
    try:
        if kind == "concept":
            df = ak.stock_board_concept_hist_em(
                symbol=name,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
        else:
            df = ak.stock_board_industry_hist_em(
                symbol=name,
                start_date=start_date,
                end_date=end_date,
                period="日k",
                adjust="",
            )
        if df is not None and not df.empty:
            return df
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame()


def fetch_ths_industry_board_summary() -> list[dict[str, Any]]:
    """THS industry board rankings (fallback when East Money is blocked)."""
    df = fetch_with_retry(ak.stock_board_industry_summary_ths, max_retry=2)
    boards: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        name = str(row.get("板块") or "").strip()
        if not name:
            continue
        try:
            change_pct = float(row.get("涨跌幅") or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        boards.append(
            {
                "name": name,
                "code": "",
                "change_pct": change_pct,
                "rising_count": row.get("上涨家数"),
                "falling_count": row.get("下跌家数"),
                "lead_stock": str(row.get("领涨股") or ""),
                "lead_change_pct": row.get("领涨股-涨跌幅"),
            }
        )
    if not boards:
        raise RuntimeError("THS industry board list empty")
    return boards


def fetch_ths_concept_board_summary() -> list[dict[str, Any]]:
    """THS concept board rankings via gnSection JSON (East Money fallback)."""
    import json

    import requests
    from bs4 import BeautifulSoup

    configure_akshare_environment()
    headers = _ths_cookie_headers()
    url = "http://q.10jqka.com.cn/gn/index/field/199112/order/desc/page/1/ajax/1/"
    resp = requests.get(url, headers=headers, timeout=READ_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, features="lxml")
    node = soup.find("input", {"id": "gnSection"})
    if not node or not node.get("value"):
        raise RuntimeError("THS concept gnSection missing")
    payload = json.loads(node["value"])
    boards: list[dict[str, Any]] = []
    for item in payload.values():
        name = str(item.get("platename") or "").strip()
        if not name:
            continue
        try:
            change_pct = float(item.get("199112") or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        boards.append(
            {
                "name": name,
                "code": str(item.get("platecode") or ""),
                "change_pct": change_pct,
                "rising_count": None,
                "falling_count": None,
                "lead_stock": "",
                "lead_change_pct": None,
            }
        )
    if not boards:
        raise RuntimeError("THS concept board list empty")
    return boards


def fetch_market_breadth_sina() -> dict[str, Any]:
    """Count rise/fall from Sina hs_a quotes (aligns with Tonghuashun / Licaitong)."""
    import json
    import ssl
    import urllib.error
    import urllib.request

    configure_akshare_environment()
    ctx = ssl.create_default_context()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn/",
    }

    rising = falling = flat = 0
    limit_up = limit_down = 0
    total_rows = 0
    page = 1

    while page <= 80:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1"
            f"&node=hs_a&symbol=&_s_r_a=page"
        )
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                text = resp.read().decode("gbk", errors="replace")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Sina breadth page {page} failed") from exc

        rows = json.loads(text)
        if not rows:
            break

        for row in rows:
            try:
                pct = float(row.get("changepercent") or 0)
            except (TypeError, ValueError):
                continue
            name = str(row.get("name") or "")
            is_st = "ST" in name.upper()
            threshold = 4.85 if is_st else 9.85
            if pct > 0:
                rising += 1
            elif pct < 0:
                falling += 1
            else:
                flat += 1
            if pct >= threshold:
                limit_up += 1
            elif pct <= -threshold:
                limit_down += 1

        total_rows += len(rows)
        if len(rows) < 100:
            break
        page += 1
        time.sleep(random.uniform(0.08, 0.18))

    if total_rows < 1000:
        raise RuntimeError(f"Sina breadth incomplete ({total_rows} rows)")

    trade_date = datetime.now().strftime("%Y-%m-%d")
    return {
        "available": True,
        "source": "sina",
        "trade_date": trade_date,
        "rising_count": rising,
        "falling_count": falling,
        "flat_count": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_count": total_rows,
        "raw": {
            "source": "sina",
            "total_count": total_rows,
            "note": "新浪财经沪深京A股全市场涨跌幅统计",
        },
    }


def fetch_market_breadth_em() -> dict[str, Any]:
    """Count rise/fall from East Money full A-share spot (matches Tonghuashun / Licaitong)."""
    rows = _fetch_em_clist_pages(fields="f3,f12,f14")
    rising = falling = flat = 0
    limit_up = limit_down = 0
    for row in rows:
        ch = row.get("f3")
        if ch is None:
            continue
        try:
            pct = float(ch)
        except (TypeError, ValueError):
            continue
        name = str(row.get("f14") or "")
        is_st = "ST" in name.upper()
        threshold = 4.85 if is_st else 9.85
        if pct > 0:
            rising += 1
        elif pct < 0:
            falling += 1
        else:
            flat += 1
        if pct >= threshold:
            limit_up += 1
        elif pct <= -threshold:
            limit_down += 1

    trade_date = datetime.now().strftime("%Y-%m-%d")
    return {
        "available": True,
        "source": "eastmoney",
        "trade_date": trade_date,
        "rising_count": rising,
        "falling_count": falling,
        "flat_count": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_count": len(rows),
        "raw": {
            "source": "eastmoney",
            "total_count": len(rows),
            "note": "东方财富沪深京A股全市场涨跌幅统计",
        },
    }


def classify_network_error(error: Exception | None) -> str:
    if error is None:
        return "ok"
    msg = str(error).lower()
    if "ssl" in msg or "certificate" in msg or "hostname" in msg:
        return "ssl_proxy"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "remote" in msg or "connection" in msg or "abruptly" in msg:
        return "ip_or_firewall"
    return "unknown"


def diagnose() -> dict[str, Any]:
    """Quick connectivity check for troubleshooting."""
    try:
        import curl_cffi  # noqa: F401

        curl_ok = True
    except ImportError:
        curl_ok = False

    import urllib3

    result: dict[str, Any] = {
        "akshare_version": getattr(ak, "__version__", "unknown"),
        "curl_cffi_installed": curl_ok,
        "urllib3_version": urllib3.__version__,
        "ak_has_set_timeout": hasattr(ak, "set_timeout"),
        "note_set_timeout": "当前 AKShare 无 ak.set_timeout()，请改用 per-call timeout 参数",
        "proxy_env": {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY") if os.environ.get(k)},
        "endpoints": {},
    }

    em_error: Exception | None = None
    tx_error: Exception | None = None

    try:
        em_df = _fetch_daily_hist_em("000001", "20250601", "20250626", "qfq")
        result["endpoints"]["eastmoney"] = {
            "status": "ok" if em_df is not None and not em_df.empty else "empty",
            "rows": 0 if em_df is None else len(em_df),
        }
    except Exception as exc:  # noqa: BLE001
        em_error = exc
        result["endpoints"]["eastmoney"] = {
            "status": "failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "scenario": classify_network_error(exc),
        }

    try:
        tx_df = _fetch_daily_hist_tx("000001", "20250601", "20250626", "qfq")
        result["endpoints"]["tencent"] = {
            "status": "ok" if tx_df is not None and not tx_df.empty else "empty",
            "rows": 0 if tx_df is None else len(tx_df),
        }
    except Exception as exc:  # noqa: BLE001
        tx_error = exc
        result["endpoints"]["tencent"] = {
            "status": "failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "scenario": classify_network_error(exc),
        }

    em_ok = result["endpoints"].get("eastmoney", {}).get("status") == "ok"
    tx_ok = result["endpoints"].get("tencent", {}).get("status") == "ok"

    if em_ok:
        result["status"] = "ok"
        result["primary_source"] = "eastmoney"
    elif tx_ok:
        result["status"] = "degraded"
        result["primary_source"] = "tencent_fallback"
        result["recommendation"] = (
            "东方财富接口被阻断（常见：IP 临时封禁/公司防火墙）。"
            "项目已自动切换腾讯日线；资金面等仅 EM 接口仍可能失败。"
            "建议：换手机热点、暂停 30-120 分钟、关闭 VPN/代理。"
        )
    else:
        result["status"] = "failed"
        result["recommendation"] = (
            "全部数据源不可用：检查网络、关闭代理、换公网 IP，或等待风控解封。"
        )
        result["scenario"] = classify_network_error(em_error or tx_error)

    return result
