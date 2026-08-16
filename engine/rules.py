"""Rule engine v2 — ยึด CNgoal v5.1 Machine Spec เป็นระบบหลัก

ทำไมเปลี่ยนจาก v1
-----------------
v1 ของผมเขียนกฎขึ้นเองจากหลักการทั่วไป แต่ CNgoal v5.1 ของ Nana ผ่าน backtest
แบบหักค่าธรรมเนียมและแบ่ง in-sample/out-of-sample แล้ว และเอกสารนั้นระบุชัดว่า
มี 3 อย่างที่ v1 ใช้อยู่ แต่ "ทดสอบแล้วไม่ผ่าน":

  * Donchian breakout (ทะลุ high 20/55)  -> ตัวอย่างน้อยเกินกว่าจะสรุป
  * รอย่อ pullback EMA20                 -> ผลตอบแทนต่ำกว่า ไม่ซ้ำข้ามสินทรัพย์
  * ใช้ "ราคา > EMA200" เป็น bias         -> แพ้ "EMA200 หันขึ้น" ทั้ง 3 เหรียญ

เมื่อมีหลักฐานขัดกัน ให้เชื่อหลักฐานที่ทดสอบมาแล้ว ไม่ใช่กฎที่ฟังดูดี
v1 rules จึงถูกลดชั้นเป็น "watch" — แสดงเป็นข้อมูลประกอบ ไม่นับเป็นสัญญาณเข้า
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

SPEC_VERSION = "cngoal-5.1"

# ค่าจาก Machine Spec — เปลี่ยนที่นี่ที่เดียวถ้า cngoal ออกเวอร์ชันใหม่
SWING_LOOKBACK = 10
SL_BUFFER_PCT = 0.1
SL_MIN_DIST_PCT = 0.5
RISK_PCT = 1.0
TAKER_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02


@dataclass
class Signal:
    symbol: str
    rule: str
    kind: str                 # "entry" | "exit" | "watch"
    direction: str            # "long" | "short" | "neutral"
    score: int
    price: float
    reasons: list[str] = field(default_factory=list)
    levels: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    checklist: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ helpers

def _regime(r: pd.Series) -> str:
    """ตาม cngoal v5.1: ดูทิศของ EMA200 ไม่ใช่ตำแหน่งราคา"""
    if bool(r.get("ema200_up", False)):
        return "bull"
    if bool(r.get("ema200_down", False)):
        return "bear"
    return "chop"


def _candle_name(r: pd.Series) -> str:
    for flag, name in [("bull_pin", "bullish pin bar"), ("bull_engulf", "bullish engulfing"),
                       ("bear_pin", "bearish pin bar"), ("bear_engulf", "bearish engulfing")]:
        if bool(r.get(flag, False)):
            return name
    return "-"


def _sizing(entry: float, stop: float, leverage_cap: float, equity: float | None) -> dict:
    """สูตร position size ของ cngoal v5.1

    Risk     = equity x 1%
    Position = Risk / SL%
    Position = min(Position, equity x leverage_cap)
    """
    sl_pct = abs(entry - stop) / entry * 100
    out = {
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "sl_distance_pct": round(sl_pct, 3),
        "leverage_cap": leverage_cap,
        # ค่าธรรมเนียมไป-กลับ + slippage คิดเป็น % ของ position
        "round_trip_cost_pct": round(2 * TAKER_FEE_PCT + 2 * SLIPPAGE_PCT, 3),
    }
    # ถ้าไม่บอกขนาดพอร์ต ให้ตอบเป็น % ของพอร์ตแทนจำนวนเงิน
    pos_pct = RISK_PCT / sl_pct * 100 if sl_pct > 0 else 0.0
    out["position_pct_of_equity"] = round(min(pos_pct, leverage_cap * 100), 1)
    out["capped_by_leverage"] = bool(pos_pct > leverage_cap * 100)
    if equity:
        risk_amt = equity * RISK_PCT / 100
        pos = min(risk_amt / (sl_pct / 100), equity * leverage_cap)
        out["risk_amount"] = round(risk_amt, 2)
        out["position_size"] = round(pos, 2)
        out["units"] = round(pos / entry, 6)
    return out


def _evidence(r: pd.Series) -> dict:
    keys = ["close", "open", "high", "low", "ema20", "ema50", "ema200", "rsi14",
            "atr14", "atr_pct", "swing_low10", "swing_high10", "ret_5d", "ret_20d"]
    out = {}
    for k in keys:
        v = r.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            out[k] = round(float(v), 4)
    out["regime"] = _regime(r)
    out["candle"] = _candle_name(r)
    out["spec"] = SPEC_VERSION
    return out


# --------------------------------------------------- CNgoal v5.1 entry rules

def cngoal_entry(sym: str, df: pd.DataFrame, leverage_cap: float = 5.0,
                 equity: float | None = None, long_only: bool = False) -> Signal | None:
    """4 เงื่อนไข ทุกข้อต้องผ่าน — ข้อใดไม่ผ่านคือไม่เข้า ไม่มีข้อยกเว้น

    ตัดสินที่แท่ง Daily ที่ปิดแล้วเท่านั้น (run.py ตัดแท่งที่ยังไม่ปิดออกก่อนเรียก)
    """
    r = df.iloc[-1]
    for side, (bias, ema20_ok, rsi_ok, candle) in {
        "long":  (bool(r["ema200_up"]),   r["close"] > r["ema20"], r["rsi14"] > 50,
                  bool(r["candle_bullish"])),
        "short": (bool(r["ema200_down"]), r["close"] < r["ema20"], r["rsi14"] < 50,
                  bool(r["candle_bearish"])),
    }.items():
        if long_only and side == "short":
            continue          # หุ้น US spot ชอร์ตไม่ได้ -> ไม่ต้องออกสัญญาณที่ทำตามไม่ได้
        checks = [
            {"n": 1, "name": "EMA200 หันขึ้น" if side == "long" else "EMA200 หันลง",
             "pass": bias,
             "detail": f"EMA200 = {r['ema200']:.4g} vs 20 แท่งก่อน {df['ema200'].iloc[-21]:.4g}"},
            {"n": 2, "name": f"ปิด {'>' if side == 'long' else '<'} EMA20", "pass": bool(ema20_ok),
             "detail": f"close {r['close']:.4g} vs EMA20 {r['ema20']:.4g}"},
            {"n": 3, "name": f"RSI14 {'>' if side == 'long' else '<'} 50", "pass": bool(rsi_ok),
             "detail": f"RSI14 = {r['rsi14']:.1f}"},
            {"n": 4, "name": "candle ยืนยัน", "pass": candle,
             "detail": _candle_name(r)},
        ]
        if not all(c["pass"] for c in checks):
            continue

        # ---- Stop loss: swing 10 แท่ง หรือ EMA50 เลือกอันที่ "ใกล้ราคากว่า"
        px = float(r["close"])
        if side == "long":
            cands = {"swing_low10": float(r["swing_low10"]), "ema50": float(r["ema50"])}
            cands = {k: v for k, v in cands.items() if v < px}
            if not cands:
                continue
            src = max(cands, key=cands.get)                 # ใกล้ราคาที่สุด = สูงสุด
            stop = cands[src] * (1 - SL_BUFFER_PCT / 100)
        else:
            cands = {"swing_high10": float(r["swing_high10"]), "ema50": float(r["ema50"])}
            cands = {k: v for k, v in cands.items() if v > px}
            if not cands:
                continue
            src = min(cands, key=cands.get)
            stop = cands[src] * (1 + SL_BUFFER_PCT / 100)

        sl_pct = abs(px - stop) / px * 100
        if sl_pct < SL_MIN_DIST_PCT:
            return Signal(sym, "cngoal_entry_skipped", "watch", side, 30, px,
                          [f"เข้าเงื่อนไขครบ 4 ข้อ แต่ SL ห่างแค่ {sl_pct:.2f}% "
                           f"(< {SL_MIN_DIST_PCT}%) → ข้าม trade นี้ตามกฎ"],
                          {"entry": round(px, 4), "stop": round(stop, 4),
                           "sl_distance_pct": round(sl_pct, 3)},
                          _evidence(r), checks)

        lv = _sizing(px, stop, leverage_cap, equity)
        lv["stop_source"] = src
        reasons = [
            f"ครบทั้ง 4 เงื่อนไข CNgoal v5.1 ฝั่ง {side.upper()}",
            f"EMA200 {'หันขึ้น' if side == 'long' else 'หันลง'} "
            f"({r['ema200']:.4g} เทียบ {df['ema200'].iloc[-21]:.4g} เมื่อ 20 แท่งก่อน)",
            f"ปิด {r['close']:.4g} {'เหนือ' if side == 'long' else 'ใต้'} EMA20 {r['ema20']:.4g}"
            f" · RSI14 {r['rsi14']:.1f}",
            f"candle ยืนยัน: {_candle_name(r)}",
            f"SL อิง {src} → {stop:.4g} (ห่าง {sl_pct:.2f}%)"
            + ("  ⚠ position ชนเพดาน leverage" if lv["capped_by_leverage"] else ""),
        ]
        return Signal(sym, "cngoal_entry", "entry", side, 85, px, reasons, lv,
                      _evidence(r), checks)
    return None


def cngoal_exit(sym: str, df: pd.DataFrame, position: dict) -> Signal | None:
    """Exit = trail EMA20 บน Daily — ไม่มีเงื่อนไข 'เฉพาะตอนกำไร'

    position = {"side","entry","stop","opened"} ที่ run.py เก็บไว้ใน state.json
    """
    r = df.iloc[-1]
    side = position["side"]
    entry = float(position["entry"])
    px = float(r["close"])
    pnl_pct = (px / entry - 1) * 100 * (1 if side == "long" else -1)

    hit_stop = (px <= position["stop"]) if side == "long" else (px >= position["stop"])
    cross = (px < r["ema20"]) if side == "long" else (px > r["ema20"])
    if not (hit_stop or cross):
        return None

    why = ("ชน SL" if hit_stop else
           f"ปิด {'ต่ำกว่า' if side == 'long' else 'สูงกว่า'} EMA20 ({r['ema20']:.4g})")
    reasons = [
        f"ปิดไม้ {side.upper()} — {why}",
        f"เข้าที่ {entry:.4g} เมื่อ {position['opened']} · ปัจจุบัน {px:.4g} → {pnl_pct:+.2f}% ก่อนค่าธรรมเนียม",
        "กฎ v5.1 ปิดทั้งตอนกำไรและตอนขาดทุน — ไม่มีเงื่อนไข 'เฉพาะตอนกำไร'",
    ]
    return Signal(sym, "cngoal_exit", "exit", side, 95, px, reasons,
                  {"entry": round(entry, 4), "exit": round(px, 4),
                   "pnl_pct_gross": round(pnl_pct, 2),
                   "pnl_pct_net": round(pnl_pct - 2 * TAKER_FEE_PCT - 2 * SLIPPAGE_PCT, 2),
                   "reason": "stop" if hit_stop else "ema20_cross"},
                  _evidence(r))


# ------------------------------------------------- watch-only (ไม่ใช่สัญญาณเข้า)

def watch_setup_forming(sym: str, df: pd.DataFrame) -> Signal | None:
    """ผ่าน 3 จาก 4 ข้อ — ยังไม่เข้า แต่ควรรู้ว่ากำลังจะครบ"""
    r = df.iloc[-1]
    for side in ("long", "short"):
        # ต้อง cast เป็น bool ของ python ทุกตัว — numpy.bool_ เขียนลง JSON ไม่ได้
        if side == "long":
            c = {"EMA200 หันขึ้น": bool(r["ema200_up"]),
                 "ปิด > EMA20": bool(r["close"] > r["ema20"]),
                 "RSI > 50": bool(r["rsi14"] > 50),
                 "candle bullish": bool(r["candle_bullish"])}
        else:
            c = {"EMA200 หันลง": bool(r["ema200_down"]),
                 "ปิด < EMA20": bool(r["close"] < r["ema20"]),
                 "RSI < 50": bool(r["rsi14"] < 50),
                 "candle bearish": bool(r["candle_bearish"])}
        passed = [k for k, v in c.items() if v]
        if len(passed) != 3:
            continue
        missing = [k for k, v in c.items() if not v][0]
        if missing.startswith("EMA200"):
            continue                      # ขาด bias = ไม่ใช่ setup ที่กำลังจะมา
        return Signal(sym, "setup_forming", "watch", side, 40, float(r["close"]),
                      [f"ผ่าน 3/4 ข้อฝั่ง {side.upper()} — ยังขาด: {missing}",
                       "ยังไม่ใช่สัญญาณเข้า แค่บอกให้รู้ว่าใกล้ครบ"],
                      {}, _evidence(r),
                      [{"n": i + 1, "name": k, "pass": v} for i, (k, v) in enumerate(c.items())])
    return None


def watch_volatility(sym: str, df: pd.DataFrame) -> Signal | None:
    """ATR% พุ่งเกิน 2 เท่าของมัธยฐาน 60 วัน → SL จะกว้างขึ้น position จะเล็กลงอัตโนมัติ"""
    r = df.iloc[-1]
    med = df["atr_pct"].tail(60).median()
    if math.isnan(med) or med <= 0 or r["atr_pct"] < 2 * med:
        return None
    return Signal(sym, "volatility_spike", "watch", "neutral", 45, float(r["close"]),
                  [f"ATR14 = {r['atr_pct']:.2f}% เทียบมัธยฐาน 60 วัน {med:.2f}% "
                   f"(ผันผวน {r['atr_pct']/med:.1f} เท่า)",
                   "SL จะกว้างขึ้น → สูตร position size จะลดขนาดไม้ให้เองที่ risk 1% เท่าเดิม"],
                  {}, _evidence(r))


def watch_drawdown(sym: str, df: pd.DataFrame) -> Signal | None:
    """ราคาห่างจากจุดสูงสุด 60 วันเกิน 20% — ข้อมูลประกอบ ไม่ใช่สัญญาณ"""
    r = df.iloc[-1]
    peak = df["close"].tail(60).max()
    dd = (r["close"] / peak - 1) * 100
    if dd > -20:
        return None
    return Signal(sym, "deep_drawdown", "watch", "neutral", 35, float(r["close"]),
                  [f"ต่ำกว่าจุดสูงสุด 60 วัน ({peak:.4g}) อยู่ {dd:.1f}%",
                   f"สถานะ EMA200: {_regime(r)}"],
                  {}, _evidence(r))


WATCH_RULES = [watch_setup_forming, watch_volatility, watch_drawdown]


def evaluate(sym: str, df: pd.DataFrame, leverage_cap: float = 5.0,
             equity: float | None = None, position: dict | None = None,
             include_watch: bool = True, long_only: bool = False,
             min_bars: int = 221) -> list[Signal]:
    """min_bars 221 = EMA200 (200) + slope lookback (20) + 1"""
    if len(df) < min_bars:
        return []
    out: list[Signal] = []

    if position:
        s = cngoal_exit(sym, df, position)
        if s:
            return [s]        # มีไม้เปิดอยู่ -> สนใจแค่การออก ไม่หาไม้ใหม่ในตัวเดียวกัน
        return []

    try:
        s = cngoal_entry(sym, df, leverage_cap, equity, long_only)
        if s:
            out.append(s)
    except Exception as e:
        print(f"[warn] cngoal_entry failed on {sym}: {e}")

    if include_watch and not any(s.kind == "entry" for s in out):
        for fn in WATCH_RULES:
            try:
                s = fn(sym, df)
            except Exception as e:
                print(f"[warn] {fn.__name__} failed on {sym}: {e}")
                continue
            if s:
                out.append(s)
    return sorted(out, key=lambda s: -s.score)
