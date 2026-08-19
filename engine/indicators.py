"""Technical indicators — pure pandas, no TA-Lib needed.

ทุกฟังก์ชันรับ pandas Series/DataFrame แล้วคืน Series ที่ index ตรงกัน
ค่าที่คำนวณไม่ได้ (ช่วง warm-up) จะเป็น NaN — ไม่ fill ให้ เพื่อไม่ให้ rule
เข้าใจผิดว่ามีสัญญาณตั้งแต่แท่งแรก
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder_smooth(s: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing แบบมาตรฐาน: seed ด้วย SMA ของ n ค่าแรก แล้วค่อย recursive

    ต้องใช้วิธีนี้ (ไม่ใช่ ewm เฉย ๆ) เพื่อให้ค่าตรงกับ TradingView / TA-Lib
    """
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = np.where(~np.isnan(v))[0]
    if len(valid) < n:
        return pd.Series(out, index=s.index)
    start = valid[n - 1]
    out[start] = np.nanmean(v[valid[0]: start + 1])
    for i in range(start + 1, len(v)):
        out[i] = (out[i - 1] * (n - 1) + v[i]) / n
    return pd.Series(out, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI — ตรงกับค่าใน TradingView"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_smooth(gain, n)
    avg_loss = wilder_smooth(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # ถ้า avg_loss = 0 แปลว่าขึ้นล้วน -> RSI = 100
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ATR — ใช้เป็นหน่วยวัด volatility สำหรับตั้ง SL และ position size."""
    return wilder_smooth(true_range(df), n)



def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ADX (Wilder) — วัด "ความแรงของเทรนด์" ไม่บอกทิศทาง

    ใช้เป็นประตูกันตลาดออกข้างใน cngoal v6.0 — เฉพาะหุ้น US เท่านั้น
    backtest 19 ส.ค. 2026: หุ้น 13 ตัว +0.227R -> +0.409R เมื่อบังคับ ADX >= 25
    และพลิกทศวรรษ 2000s จาก -0.029R เป็น +0.111R
    ⛔ ห้ามเอาไปใช้กับ XAU (แย่ลง) หรือ crypto (กำไรรวมหายครึ่ง) — ทดสอบแล้ว

    เขียนเองแทนการพึ่ง TA-lib เพราะ requirements ของ repo นี้มีแค่ pandas/numpy
    และสูตร Wilder ตรงไปตรงมาพอที่จะเทียบตัวเลขกับ TradingView ได้
    """
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = wilder_smooth(true_range(df), n)
    pdi = 100 * wilder_smooth(pd.Series(plus_dm, index=df.index), n) / atr_
    mdi = 100 * wilder_smooth(pd.Series(minus_dm, index=df.index), n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    return wilder_smooth(dx, n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def bb_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    lo, mid, hi = bollinger(close, n, k)
    return (hi - lo) / mid


def donchian(df: pd.DataFrame, n: int = 20):
    """ช่องราคาสูงสุด/ต่ำสุด n แท่งย้อนหลัง (ไม่รวมแท่งปัจจุบัน)."""
    hi = df["high"].shift(1).rolling(n, min_periods=n).max()
    lo = df["low"].shift(1).rolling(n, min_periods=n).min()
    return lo, hi


def rel_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    return volume / volume.rolling(n, min_periods=n).mean()


def slope_pct(s: pd.Series, n: int = 10) -> pd.Series:
    """ความชันของเส้น n แท่ง คิดเป็น % ของค่าปัจจุบัน — ใช้ดูว่าเทรนด์ยังมีแรงไหม."""
    return (s - s.shift(n)) / s.abs() * 100


# ---------------------------------------------------------- candlestick

def _body(df):  return (df["close"] - df["open"]).abs()
def _upper(df): return df["high"] - df[["open", "close"]].max(axis=1)
def _lower(df): return df[["open", "close"]].min(axis=1) - df["low"]


def bullish_pin(df: pd.DataFrame) -> pd.Series:
    """ไส้ล่าง >= 2.0 x body และ ไส้บน <= 1.0 x body และปิดอยู่ครึ่งบนของแท่ง"""
    b = _body(df).replace(0.0, np.nan)
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    return ((_lower(df) >= 2.0 * b) & (_upper(df) <= 1.0 * b)
            & ((df["close"] - df["low"]) / rng >= 0.5)).fillna(False)


def bearish_pin(df: pd.DataFrame) -> pd.Series:
    b = _body(df).replace(0.0, np.nan)
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    return ((_upper(df) >= 2.0 * b) & (_lower(df) <= 1.0 * b)
            & ((df["high"] - df["close"]) / rng >= 0.5)).fillna(False)


def bullish_engulf(df: pd.DataFrame) -> pd.Series:
    po, pc = df["open"].shift(1), df["close"].shift(1)
    return ((pc < po) & (df["close"] > df["open"])
            & (df["close"] >= po) & (df["open"] <= pc)).fillna(False)


def bearish_engulf(df: pd.DataFrame) -> pd.Series:
    po, pc = df["open"].shift(1), df["close"].shift(1)
    return ((pc > po) & (df["close"] < df["open"])
            & (df["close"] <= po) & (df["open"] >= pc)).fillna(False)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """เติมทุก indicator ที่ rule engine v1 ต้องใช้ ลงใน DataFrame เดียว."""
    out = df.copy()
    c = out["close"]
    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["ema200"] = ema(c, 200)
    out["rsi14"] = rsi(c, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / c * 100
    out["adx14"] = adx(out, 14)
    out["bb_lo"], out["bb_mid"], out["bb_hi"] = bollinger(c, 20, 2.0)
    out["bb_width"] = (out["bb_hi"] - out["bb_lo"]) / out["bb_mid"]
    out["bb_width_pctile"] = out["bb_width"].rolling(120, min_periods=60).rank(pct=True)
    out["dc_lo20"], out["dc_hi20"] = donchian(out, 20)
    out["dc_lo55"], out["dc_hi55"] = donchian(out, 55)
    out["rvol20"] = rel_volume(out["volume"], 20) if "volume" in out else np.nan
    out["ema50_slope"] = slope_pct(out["ema50"], 10)
    out["ret_5d"] = c.pct_change(5) * 100
    out["ret_20d"] = c.pct_change(20) * 100

    # --- cngoal v5.1 ---
    # bias = "EMA200 หันขึ้น" (ทิศทางของเส้น) ไม่ใช่ "ราคาอยู่เหนือ EMA200"
    out["ema200_up"] = out["ema200"] > out["ema200"].shift(20)
    out["ema200_down"] = out["ema200"] < out["ema200"].shift(20)
    out["bull_pin"] = bullish_pin(out)
    out["bear_pin"] = bearish_pin(out)
    out["bull_engulf"] = bullish_engulf(out)
    out["bear_engulf"] = bearish_engulf(out)
    out["candle_bullish"] = out["bull_pin"] | out["bull_engulf"]
    out["candle_bearish"] = out["bear_pin"] | out["bear_engulf"]
    out["swing_low10"] = out["low"].rolling(10, min_periods=10).min()
    out["swing_high10"] = out["high"].rolling(10, min_periods=10).max()
    return out
