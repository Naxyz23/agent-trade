"""Data layer — แยกออกจาก logic เพื่อให้เปลี่ยนแหล่งข้อมูลได้โดยไม่แตะ rule

หมายเหตุสำคัญ: ไฟล์นี้ต้องรันในที่ที่ "ออกเน็ตได้จริง" (GitHub Actions หรือเครื่อง Nana)
sandbox ของ Cowork ออกเน็ตไปหา API การเงินไม่ได้ — ดูเหตุผลใน README
"""
from __future__ import annotations

import time
import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]


def fetch_yahoo(symbol: str, period: str = "3y", interval: str = "1d") -> pd.DataFrame:
    """US stocks, ETF, XAUUSD (GC=F / GLD), และ crypto ก็ได้ — ฟรี ไม่ต้องใช้ key."""
    import yfinance as yf

    df = yf.download(symbol, period=period, interval=interval,
                     auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[COLS]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.dropna(subset=["close"])


def fetch_kraken(pair: str, interval_min: int = 1440) -> pd.DataFrame:
    """สำรองสำหรับ crypto + ทองคำ (PAXGUSD) — public API ไม่ต้องใช้ key เลย.

    ข้อจำกัด: คืนได้สูงสุด 720 แท่ง ซึ่งพอสำหรับ EMA200 บน timeframe 1D
    """
    import requests

    url = "https://api.kraken.com/0/public/OHLC"
    r = requests.get(url, params={"pair": pair, "interval": interval_min}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    key = next(k for k in payload["result"] if k != "last")
    rows = payload["result"][key]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close",
                                     "vwap", "volume", "count"])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")[COLS].astype(float)
    return df


SOURCES = {"yahoo": fetch_yahoo, "kraken": fetch_kraken}


def load(symbol: str, source: str = "yahoo", retries: int = 3, **kw) -> pd.DataFrame:
    fn = SOURCES[source]
    last = None
    for i in range(retries):
        try:
            return fn(symbol, **kw)
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed for {symbol} via {source}: {last}")
