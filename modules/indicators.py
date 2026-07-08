"""Technical indicators computed from OHLCV DataFrames."""

from __future__ import annotations

import pandas as pd


def moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def exponential_moving_average(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    ema_fast = exponential_moving_average(close, fast)
    ema_slow = exponential_moving_average(close, slow)
    macd = ema_fast - ema_slow
    macd_signal = exponential_moving_average(macd, signal)
    macd_hist = macd - macd_signal
    return pd.DataFrame(
        {
            "ema12": ema_fast,
            "ema26": ema_slow,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
        }
    )


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def compute_kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> pd.DataFrame:
    lowest_low = low.rolling(window=n, min_periods=n).min()
    highest_high = high.rolling(window=n, min_periods=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, pd.NA) * 100
    k = rsv.ewm(alpha=1 / k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1 / d_smooth, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})


def compute_bollinger(
    close: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    mid = moving_average(close, window)
    std = close.rolling(window=window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return pd.DataFrame({"boll_mid": mid, "boll_upper": upper, "boll_lower": lower})


def compute_volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    avg_volume = volume.rolling(window=window, min_periods=window).mean()
    return volume / avg_volume.replace(0, pd.NA)


def compute_cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Commodity Channel Index (Lambert). Constant 0.015 in denominator."""
    tp = (high + low + close) / 3
    sma = tp.rolling(window=period, min_periods=period).mean()
    mean_dev = tp.rolling(window=period, min_periods=period).apply(
        lambda x: (abs(x - x.mean())).mean(),
        raw=True,
    )
    return (tp - sma) / (0.015 * mean_dev.replace(0, pd.NA))


def compute_williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """0–100 scale: >80 overbought, <20 oversold (chart-inverted style)."""
    highest = high.rolling(window=period, min_periods=period).max()
    lowest = low.rolling(window=period, min_periods=period).min()
    span = (highest - lowest).replace(0, pd.NA)
    return (close - lowest) / span * 100


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.DataFrame:
    """Wilder ADX / +DI / -DI."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = _wilder_smooth(tr, period)
    plus_di = 100 * _wilder_smooth(plus_dm, period) / atr.replace(0, pd.NA)
    minus_di = 100 * _wilder_smooth(minus_dm, period) / atr.replace(0, pd.NA)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA) * 100
    adx = _wilder_smooth(dx, period)

    return pd.DataFrame({"plus_di14": plus_di, "minus_di14": minus_di, "adx14": adx})


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns to a quotes DataFrame sorted by trade_date."""
    if df.empty:
        return df

    out = df.sort_values("trade_date").copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    for window in (5, 10, 20, 60, 120):
        out[f"ma{window}"] = moving_average(close, window)

    macd_df = compute_macd(close)
    out = pd.concat([out, macd_df], axis=1)

    out["rsi14"] = compute_rsi(close)

    kdj_df = compute_kdj(high, low, close)
    out = pd.concat([out, kdj_df], axis=1)

    boll_df = compute_bollinger(close)
    out = pd.concat([out, boll_df], axis=1)

    out["cci20"] = compute_cci(high, low, close, period=20)
    out["williams_r14"] = compute_williams_r(high, low, close, period=14)

    adx_df = compute_adx(high, low, close)
    out = pd.concat([out, adx_df], axis=1)

    out["volume_ratio"] = compute_volume_ratio(volume)
    return out
