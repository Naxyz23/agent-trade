"""Data layer — แยกออกจาก logic เพื่อให้เปลี่ยนแหล่งข้อมูลได้โดยไม่แตะ rule

หมายเหตุสำคัญ: ไฟล์นี้ต้องรันในที่ที่ "ออกเน็ตได้จริง" (GitHub Actions หรือเครื่อง Nana)
sandbox ของ Cowork ออกเน็ตไปหา API การเงินไม่ได้ — ดูเหตุผลใน README

v0.6 — ทำไมเปลี่ยนจาก Yahoo มาเป็น Kraken สำหรับ crypto
-------------------------------------------------------
17 ส.ค. 2026 รอบ crypto รันตอน 01:55 UTC แต่ได้ bar_date = 2026-08-15
คือกราฟช้ากว่าวันที่รันไป 2 วัน เพราะ Yahoo ปล่อยแท่ง Daily ของ crypto ช้า
Kraken ปิดแท่ง 00:00 UTC ตรงเวลาและให้ดึงได้ทันที จึงใช้เป็นแหล่งหลักของ crypto
ส่วน Yahoo เก็บไว้เป็นตัวสำรอง (และยังเป็นตัวหลักของหุ้น/ทอง ซึ่ง Kraken ไม่มี)

รองรับ `sources: [kraken, yahoo]` ใน watchlist.yml — ไล่ทีละตัวจนกว่าจะได้ข้อมูล
"ที่สดพอ" ถ้าตัวแรกคืนข้อมูลเก่าเกินเกณฑ์ จะข้ามไปตัวถัดไปแทนที่จะใช้ของเก่า
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]

# ชื่อ pair บน Kraken ไม่เหมือนใคร — BTC = XBT
KRAKEN_PAIRS = {
    "BTC-USD": "XBTUSD", "ETH-USD": "ETHUSD", "SOL-USD": "SOLUSD",
    "XBTUSD": "XBTUSD", "ETHUSD": "ETHUSD", "SOLUSD": "SOLUSD",
    "PAXGUSD": "PAXGUSD",
    # BNB ไม่มีบน Kraken — ต้องใช้ Yahoo อย่างเดียว
}


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
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df.dropna(subset=["close"])


def fetch_kraken(pair: str, interval_min: int = 1440) -> pd.DataFrame:
    """crypto + ทองคำ (PAXGUSD) — public API ไม่ต้องใช้ key เลย

    ข้อจำกัด: คืนได้สูงสุด 720 แท่ง — พอสำหรับ EMA200 + slope 20 (ต้องการ 221)
    ข้อดีที่ทำให้เลือกเป็นตัวหลัก: แท่ง Daily ปิด 00:00 UTC แล้วดึงได้ทันที
    """
    import requests

    pair = KRAKEN_PAIRS.get(pair, pair)
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
    return df.sort_index()


SOURCES = {"yahoo": fetch_yahoo, "kraken": fetch_kraken}


def bar_age_days(df: pd.DataFrame, now: datetime | None = None) -> int:
    """อายุของแท่งล่าสุดเป็นจำนวนวัน — ใช้ตัดสินว่าข้อมูลสดพอไหม"""
    if df is None or df.empty:
        return 10_000
    now = now or datetime.now(timezone.utc)
    last = pd.Timestamp(df.index[-1]).normalize()
    today = pd.Timestamp(now.date())
    return int((today - last).days)


def load(symbol: str, source: str = "yahoo", retries: int = 3, **kw) -> pd.DataFrame:
    """ดึงจากแหล่งเดียว พร้อม retry — เก็บไว้เพื่อความเข้ากันได้กับโค้ด/เทสต์เดิม"""
    fn = SOURCES[source]
    last = None
    for i in range(retries):
        try:
            return fn(symbol, **kw)
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed for {symbol} via {source}: {last}")


def load_best(item: dict, max_age_days: int, now: datetime | None = None,
              retries: int = 2) -> tuple[pd.DataFrame, dict]:
    """ไล่แหล่งข้อมูลตามลำดับใน `sources` แล้วคืนอันแรกที่ "สดพอ"

    คืน (df, meta) โดย meta บอกว่าใช้แหล่งไหน อายุกี่วัน และลองอะไรไปบ้าง
    ถ้าไม่มีแหล่งไหนสดพอเลย → คืนอันที่สดที่สุดที่ได้มา พร้อม meta["stale"] = True
    เพื่อให้ run.py ตัดสินใจว่าจะข้ามสัญญาณของตัวนี้ (ไม่ใช่ให้เงียบไปเฉย ๆ)
    """
    sources = item.get("sources") or [item.get("source", "yahoo")]
    sym = item.get("fetch_symbol", item["symbol"])
    tried, best_df, best_meta = [], None, None

    for src in sources:
        fetch_sym = item.get(f"fetch_symbol_{src}", sym)
        try:
            df = load(fetch_sym, source=src, retries=retries)
        except Exception as e:
            tried.append({"source": src, "error": str(e)[:120]})
            continue
        age = bar_age_days(df, now)
        tried.append({"source": src, "bars": len(df), "bar_age_days": age})
        if best_df is None or age < best_meta["bar_age_days"]:
            best_df, best_meta = df, {"source": src, "bar_age_days": age}
        if age <= max_age_days:
            return df, {"source": src, "bar_age_days": age, "stale": False, "tried": tried}

    if best_df is None:
        raise RuntimeError("ทุกแหล่งข้อมูลล้มเหลว: "
                           + "; ".join(f"{t['source']}={t.get('error', '?')}" for t in tried))
    return best_df, {**best_meta, "stale": True, "tried": tried}
