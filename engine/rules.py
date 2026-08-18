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

# --- v0.8: สองค่านี้ปรับได้ต่อสินทรัพย์ผ่าน watchlist.yml ---
ATR_STOP_MULT = 2.0          # ตัวคูณ ATR14 เมื่อ stop_mode = "atr2"
DEFAULT_STOP_MODE = "chart"  # "chart" = กฎ v5.1 เดิม | "atr2" = ATR14 x 2
DEFAULT_TRAIL_EMA = 50       # เส้นที่ใช้ trail ออก (20 = กฎเดิม | 50 = v0.8)
VALID_TRAIL_EMA = (20, 50)


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


def _sizing(entry: float, stop: float, leverage_cap: float, equity: float | None,
            risk_pct: float = RISK_PCT) -> dict:
    """สูตร position size ของ cngoal v5.1

    Risk     = equity x risk_pct%   (v5.1 = 1% และ compounding จาก equity ปัจจุบัน)
    Position = Risk / SL%
    Position = min(Position, equity x leverage_cap)

    v0.6: risk_pct รับจาก watchlist.yml (`risk.pct_per_trade`) แทนที่จะ hardcode
    ของเดิมแก้ yml แล้วไม่มีผลอะไรเลย เพราะโค้ดใช้ค่าคงที่ในโมดูล
    """
    sl_pct = abs(entry - stop) / entry * 100
    out = {
        "risk_pct": risk_pct,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "sl_distance_pct": round(sl_pct, 3),
        "leverage_cap": leverage_cap,
        # ค่าธรรมเนียมไป-กลับ + slippage คิดเป็น % ของ position
        "round_trip_cost_pct": round(2 * TAKER_FEE_PCT + 2 * SLIPPAGE_PCT, 3),
    }
    # ถ้าไม่บอกขนาดพอร์ต ให้ตอบเป็น % ของพอร์ตแทนจำนวนเงิน
    pos_pct = risk_pct / sl_pct * 100 if sl_pct > 0 else 0.0
    out["position_pct_of_equity"] = round(min(pos_pct, leverage_cap * 100), 1)
    out["capped_by_leverage"] = bool(pos_pct > leverage_cap * 100)
    if equity:
        risk_amt = equity * risk_pct / 100
        pos = min(risk_amt / (sl_pct / 100), equity * leverage_cap)
        out["risk_amount"] = round(risk_amt, 2)
        out["position_size"] = round(pos, 2)
        out["units"] = round(pos / entry, 6)
    return out


def _place_stop(side: str, r: pd.Series, stop_mode: str = DEFAULT_STOP_MODE):
    """คืน (ราคา stop, ที่มา) หรือ None ถ้าวางไม่ได้

    chart = กฎ v5.1 เดิม — swing10 หรือ EMA50 เลือกอันที่ "ใกล้ราคากว่า"
    atr2  = ATR14 x 2 จากราคาปิด (v0.8 ใช้กับหุ้น US และ XAU เท่านั้น)

    ทำไมแยกตามสินทรัพย์ ไม่ใช้อันเดียวทั้งระบบ — จาก backtest 18 ส.ค. 2026
    -------------------------------------------------------------------
    หุ้น+ทอง 15 ตัว (3,581 ไม้ 53 ปี)  chart +0.157R -> atr2 +0.178R
        และที่สำคัญกว่าคือความทนทาน: ของเดิมติดลบตั้งแต่ slippage 0.10%
        ส่วน atr2 ยังเสมอตัวที่ 0.40% · ดีขึ้น 12 จาก 15 สินทรัพย์
    BTC (5,343 แท่ง 2012-2026)         chart +2.877R -> atr2 +2.084R
        = ไม่มีเหตุผลให้เปลี่ยน crypto ตรงกับผลทดสอบ SL เมื่อ 17 ส.ค.
          ที่สรุปว่า SL ตามชาร์ตทนทานที่สุดสำหรับ crypto

    ถ้า ATR ใช้ไม่ได้ (ช่วง warm-up) จะถอยไปใช้กฎ chart แทนการทิ้งสัญญาณ
    """
    px = float(r["close"])
    if stop_mode == "atr2":
        a = r.get("atr14")
        if a is not None and not (isinstance(a, float) and math.isnan(a)) and float(a) > 0:
            dist = ATR_STOP_MULT * float(a)
            return (px - dist, "atr2") if side == "long" else (px + dist, "atr2")

    if side == "long":
        cands = {"swing_low10": float(r["swing_low10"]), "ema50": float(r["ema50"])}
        cands = {k: v for k, v in cands.items() if v < px}
        if not cands:
            return None
        src = max(cands, key=cands.get)          # ใกล้ราคาที่สุด = สูงสุด
        return cands[src] * (1 - SL_BUFFER_PCT / 100), src

    cands = {"swing_high10": float(r["swing_high10"]), "ema50": float(r["ema50"])}
    cands = {k: v for k, v in cands.items() if v > px}
    if not cands:
        return None
    src = min(cands, key=cands.get)
    return cands[src] * (1 + SL_BUFFER_PCT / 100), src


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
                 equity: float | None = None, long_only: bool = False,
                 risk_pct: float = RISK_PCT,
                 stop_mode: str = DEFAULT_STOP_MODE) -> Signal | None:
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

        # ---- Stop loss (v0.8: เลือกวิธีวางได้ตามสินทรัพย์ ดู _place_stop) ----
        px = float(r["close"])
        placed = _place_stop(side, r, stop_mode)
        if placed is None:
            continue
        stop, src = placed

        sl_pct = abs(px - stop) / px * 100
        if sl_pct < SL_MIN_DIST_PCT:
            return Signal(sym, "cngoal_entry_skipped", "watch", side, 30, px,
                          [f"เข้าเงื่อนไขครบ 4 ข้อ แต่ SL ห่างแค่ {sl_pct:.2f}% "
                           f"(< {SL_MIN_DIST_PCT}%) → ข้าม trade นี้ตามกฎ"],
                          {"entry": round(px, 4), "stop": round(stop, 4),
                           "sl_distance_pct": round(sl_pct, 3)},
                          _evidence(r), checks)

        lv = _sizing(px, stop, leverage_cap, equity, risk_pct)
        lv["stop_source"] = src
        reasons = [
            f"ครบทั้ง 4 เงื่อนไข CNgoal v5.1 ฝั่ง {side.upper()}",
            f"EMA200 {'หันขึ้น' if side == 'long' else 'หันลง'} "
            f"({r['ema200']:.4g} เทียบ {df['ema200'].iloc[-21]:.4g} เมื่อ 20 แท่งก่อน)",
            f"ปิด {r['close']:.4g} {'เหนือ' if side == 'long' else 'ใต้'} EMA20 {r['ema20']:.4g}"
            f" · RSI14 {r['rsi14']:.1f}",
            f"candle ยืนยัน: {_candle_name(r)}",
            f"SL อิง {'ATR14 x 2' if src == 'atr2' else src} → {stop:.4g} "
            f"(ห่าง {sl_pct:.2f}%)"
            + ("  ⚠ position ชนเพดาน leverage" if lv["capped_by_leverage"] else ""),
        ]
        return Signal(sym, "cngoal_entry", "entry", side, 85, px, reasons, lv,
                      _evidence(r), checks)
    return None


def cngoal_exit(sym: str, df: pd.DataFrame, position: dict,
                trail_ema: int = DEFAULT_TRAIL_EMA) -> Signal | None:
    """Exit = trail EMA บน Daily — ไม่มีเงื่อนไข "เฉพาะตอนกำไร"

    position = {"side","entry","stop","opened"} ที่ run.py เก็บไว้ใน state_<group>.json

    v0.8 — เปลี่ยนค่าเริ่มต้นจาก EMA20 เป็น EMA50
    ------------------------------------------
    backtest 18 ส.ค. 2026 พบว่ากำไรทั้งหมดของระบบมาจากหางยาว: ไม้ที่ได้เกิน 3R
    มีแค่ 6.9% แต่ทำได้ +1,030R ขณะที่อีก 93% รวมกันติดลบ -581R
    EMA20 ตัดกำไรเร็วเกินไปจึงฆ่าหางยาวนั้นทิ้ง เปลี่ยนเป็น EMA50 แล้ว:
        หุ้น+ทอง 15 ตัว  +0.053R -> +0.157R  (ไม้ >3R เพิ่มจาก 5.6% เป็น 7.9%)
        BTC 5,343 แท่ง   +1.947R -> +2.877R
    ไปทางเดียวกันบนสินทรัพย์คนละประเภท = หลักฐานหนักแน่นกว่าผลจากกลุ่มเดียว

    v0.6 แก้ 2 บั๊กที่ทำให้ state ไม่ตรงกับพอร์ตจริง (ยังคงไว้)
    1) เช็ค SL ด้วย low/high ไม่ใช่ราคาปิด — SL order จริงทำงานทันทีที่ไส้แตะ
    2) ออกที่ราคา stop จริง ถ้าเปิด gap ข้าม SL ใช้ราคาเปิด (ฝั่งที่แย่กว่า)
    """
    r = df.iloc[-1]
    side = position["side"]
    entry = float(position["entry"])
    stop = float(position["stop"])
    close = float(r["close"])
    is_long = side == "long"

    if trail_ema not in VALID_TRAIL_EMA:
        raise ValueError(f"trail_ema ต้องเป็นหนึ่งใน {VALID_TRAIL_EMA} (ได้ {trail_ema})")
    trail_col = f"ema{trail_ema}"
    trail_val = float(r[trail_col])

    hit_stop = (float(r["low"]) <= stop) if is_long else (float(r["high"]) >= stop)
    # ช่วง warm-up เส้นยังเป็น NaN — ถือว่ายังไม่มีสัญญาณ trail ดีกว่าปิดไม้มั่ว
    cross = (not math.isnan(trail_val)) and (
        (close < trail_val) if is_long else (close > trail_val))
    if not (hit_stop or cross):
        return None

    if hit_stop:
        o = float(r["open"])
        gapped = (o < stop) if is_long else (o > stop)
        px = o if gapped else stop
        why = "ชน SL (เปิด gap ข้าม SL — ได้ราคาเปิดจริง)" if gapped else "ชน SL"
        reason_code = "stop_gap" if gapped else "stop"
    else:
        px = close
        why = (f"ปิด {'ต่ำกว่า' if is_long else 'สูงกว่า'} "
               f"EMA{trail_ema} ({trail_val:.4g})")
        reason_code = f"ema{trail_ema}_cross"

    pnl_pct = (px / entry - 1) * 100 * (1 if is_long else -1)
    reasons = [
        f"ปิดไม้ {side.upper()} — {why}",
        f"เข้าที่ {entry:.4g} เมื่อ {position['opened']} · ออกที่ {px:.4g} "
        f"→ {pnl_pct:+.2f}% ก่อนค่าธรรมเนียม",
        "กฎ v5.1 ปิดทั้งตอนกำไรและตอนขาดทุน — ไม่มีเงื่อนไข 'เฉพาะตอนกำไร'",
    ]
    if hit_stop and close > stop and is_long:
        reasons.append(f"⚠ ไส้ล่างหลุด SL ({r['low']:.4g}) แล้วเด้งกลับปิดที่ {close:.4g} "
                       f"— SL order บนกระดานตัดไปแล้ว ระบบจึงถือว่าไม้นี้ปิด")
    if hit_stop and close < stop and not is_long:
        reasons.append(f"⚠ ไส้บนหลุด SL ({r['high']:.4g}) แล้วย่อกลับปิดที่ {close:.4g} "
                       f"— SL order บนกระดานตัดไปแล้ว ระบบจึงถือว่าไม้นี้ปิด")

    return Signal(sym, "cngoal_exit", "exit", side, 95, px, reasons,
                  {"entry": round(entry, 4), "exit": round(px, 4),
                   "stop": round(stop, 4), "close": round(close, 4),
                   "pnl_pct_gross": round(pnl_pct, 2),
                   "pnl_pct_net": round(pnl_pct - 2 * TAKER_FEE_PCT - 2 * SLIPPAGE_PCT, 2),
                   "reason": reason_code},
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
             min_bars: int = 221, risk_pct: float = RISK_PCT,
             stop_mode: str = DEFAULT_STOP_MODE,
             trail_ema: int = DEFAULT_TRAIL_EMA) -> list[Signal]:
    """min_bars 221 = EMA200 (200) + slope lookback (20) + 1

    v0.8: `stop_mode` และ `trail_ema` ตั้งได้ต่อสินทรัพย์จาก watchlist.yml
    ค่าเริ่มต้นคือ chart + EMA50 · หุ้น US และ XAU ตั้งเป็น atr2 ใน watchlist
    """
    if len(df) < min_bars:
        return []
    out: list[Signal] = []

    if position:
        s = cngoal_exit(sym, df, position, trail_ema=trail_ema)
        if s:
            return [s]        # มีไม้เปิดอยู่ -> สนใจแค่การออก ไม่หาไม้ใหม่ในตัวเดียวกัน
        return []

    try:
        s = cngoal_entry(sym, df, leverage_cap, equity, long_only, risk_pct,
                         stop_mode=stop_mode)
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
