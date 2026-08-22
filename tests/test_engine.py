"""Verification — ก่อนเชื่อสัญญาณ ต้องเชื่อตัวเลขก่อน

รัน:  python tests/test_engine.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from engine import indicators as ind
from engine import rules
from engine import notify
from engine import run as engine_run

WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
          45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
          46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
          43.42, 42.66, 43.13]


def _rsi_naive(closes, n=14):
    """implementation ที่สองเขียนตรงตามนิยาม ไม่ใช้ pandas — ใช้ cross-check"""
    g = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    l = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    out = [None] * n + [100 - 100 / (1 + ag / al)]
    for i in range(n, len(g)):
        ag, al = (ag * (n - 1) + g[i]) / n, (al * (n - 1) + l[i]) / n
        out.append(100 - 100 / (1 + ag / al))
    return out


def _synth(n=500, seed=0, trend=0.0006, vol=0.02):
    rng = np.random.default_rng(seed)
    ret = rng.normal(trend, vol, n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = close * (1 - np.abs(rng.normal(0, vol / 2, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = rng.lognormal(12, 0.4, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 1e6}


# ------------------------------------------------------------ indicators

def test_rsi_matches_wilder_by_hand_and_by_second_impl():
    r = ind.rsi(pd.Series(WILDER), 14)
    # คำนวณมือ: avg gain 3.34/14, avg loss 1.40/14 -> RS 2.385714 -> RSI 70.464
    assert abs(r.iloc[14] - 70.464) < 0.01, r.iloc[14]
    assert abs(r.iloc[15] - 66.250) < 0.01, r.iloc[15]
    naive = _rsi_naive(WILDER, 14)
    for i in range(14, len(WILDER)):
        assert abs(r.iloc[i] - naive[i]) < 1e-9, (i, r.iloc[i], naive[i])
    assert r.iloc[:14].isna().all()


def test_ema_and_atr_warmup():
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert ind.ema(s, 20).iloc[:19].isna().all()
    assert ind.ema(s, 20).iloc[-1] < s.iloc[-1]
    a = ind.atr(_synth(300, seed=1), 14)
    assert a.iloc[:13].isna().all() and (a.dropna() > 0).all()


def test_macd_hist_identity():
    m, sg, h = ind.macd(_synth(300, seed=2)["close"])
    assert np.allclose((m - sg).dropna(), h.dropna())


# ------------------------------------------------ candlestick (cngoal v5.1)

def test_bullish_pin_bar_definition():
    # body 1.0 (100->101), ไส้ล่าง 3.0, ไส้บน 0.5, ปิดครึ่งบน -> ผ่าน
    df = pd.DataFrame([_bar(100, 101.5, 97, 101), _bar(100, 101.5, 97, 101)])
    assert bool(ind.bullish_pin(df).iloc[-1])
    # ไส้ล่างแค่ 1.5 x body -> ไม่ผ่าน (ต้อง >= 2.0)
    df2 = pd.DataFrame([_bar(100, 101.5, 98.5, 101), _bar(100, 101.5, 98.5, 101)])
    assert not bool(ind.bullish_pin(df2).iloc[-1])
    # ไส้บนใหญ่เกิน body -> ไม่ผ่าน
    df3 = pd.DataFrame([_bar(100, 103, 97, 101), _bar(100, 103, 97, 101)])
    assert not bool(ind.bullish_pin(df3).iloc[-1])


def test_bearish_pin_bar_definition():
    df = pd.DataFrame([_bar(101, 104, 99.5, 100), _bar(101, 104, 99.5, 100)])
    assert bool(ind.bearish_pin(df).iloc[-1])
    assert not bool(ind.bullish_pin(df).iloc[-1])


def test_engulfing_definitions():
    # แท่งก่อนแดง 102->100, แท่งนี้เขียว 99.5->102.5 กลืนทั้งตัว
    df = pd.DataFrame([_bar(102, 102.5, 99.5, 100), _bar(99.5, 103, 99, 102.5)])
    assert bool(ind.bullish_engulf(df).iloc[-1])
    assert not bool(ind.bearish_engulf(df).iloc[-1])
    # กลืนไม่หมด (open สูงกว่า close แท่งก่อน) -> ไม่ผ่าน
    df2 = pd.DataFrame([_bar(102, 102.5, 99.5, 100), _bar(100.5, 103, 100, 102.5)])
    assert not bool(ind.bullish_engulf(df2).iloc[-1])
    # กลับด้าน
    df3 = pd.DataFrame([_bar(100, 102.5, 99.5, 102), _bar(102.5, 103, 99, 99.5)])
    assert bool(ind.bearish_engulf(df3).iloc[-1])


def test_ema200_slope_is_direction_not_position():
    """หัวใจของ v5.1 — bias มาจากทิศของเส้น ไม่ใช่ตำแหน่งราคา"""
    df = ind.enrich(_synth(500, seed=3, trend=0.002))
    r = df.iloc[-1]
    assert r["ema200_up"] == (r["ema200"] > df["ema200"].iloc[-21])
    assert not (bool(r["ema200_up"]) and bool(r["ema200_down"]))
    # ต้องมีเคสที่ EMA200 หันขึ้นแต่ราคาอยู่ใต้เส้น (ไม่งั้นสองกฎนี้ก็เหมือนกัน)
    found = False
    for seed in range(30):
        d = ind.enrich(_synth(500, seed=seed, trend=0.001))
        m = d["ema200_up"] & (d["close"] < d["ema200"])
        if m.any():
            found = True
            break
    assert found, "สองนิยามนี้ต้องให้ผลต่างกันได้จริง"


# ------------------------------------------------------ cngoal v5.1 rules

def test_entry_requires_all_conditions():
    """ทุกสัญญาณ entry ต้องผ่านครบทุกข้อ ไม่มีข้อยกเว้น

    v0.9: 5 ข้อเมื่อไม่ใช้ ADX · 6 ข้อเมื่อส่ง adx_min เข้ามา
    """
    n = 0
    for seed in range(25):
        for trend in (0.0018, 0.0, -0.0018):
            full = ind.enrich(_synth(500, seed=seed, trend=trend))
            for end in range(240, len(full), 2):
                for s in rules.evaluate("T", full.iloc[:end]):
                    if s.kind != "entry":
                        continue
                    n += 1
                    assert len(s.checklist) == 5
                    assert all(c["pass"] for c in s.checklist), s.checklist
                    assert s.levels["sl_distance_pct"] >= rules.SL_MIN_DIST_PCT
                    if s.direction == "long":
                        assert s.levels["stop"] < s.levels["entry"]
                    else:
                        assert s.levels["stop"] > s.levels["entry"]
    print(f"    ตรวจสัญญาณ entry {n} ครั้ง")
    assert n > 20, n


def test_position_size_formula():
    """Position = Risk / SL%  แล้ว cap ด้วย leverage"""
    lv = rules._sizing(entry=100.0, stop=95.0, leverage_cap=5.0, equity=100.0)
    assert abs(lv["sl_distance_pct"] - 5.0) < 1e-9
    # risk 1 USDT / SL 5% = position 20 USDT (ต่ำกว่าเพดาน 500)
    assert abs(lv["risk_amount"] - 1.0) < 1e-9
    assert abs(lv["position_size"] - 20.0) < 1e-9
    assert abs(lv["units"] - 0.2) < 1e-9
    assert not lv["capped_by_leverage"]
    # SL แคบมาก -> ต้องโดน cap ที่ x5
    tight = rules._sizing(entry=100.0, stop=99.9, leverage_cap=5.0, equity=100.0)
    assert tight["capped_by_leverage"]
    assert abs(tight["position_size"] - 500.0) < 1e-9
    # หุ้น US ไม่มี leverage -> ห้ามเกินพอร์ต
    stock = rules._sizing(entry=100.0, stop=99.5, leverage_cap=1.0, equity=100.0)
    assert abs(stock["position_size"] - 100.0) < 1e-9


def test_sl_never_closer_than_min_distance():
    lv = rules._sizing(100.0, 99.9, 5.0, 100.0)
    assert lv["sl_distance_pct"] < rules.SL_MIN_DIST_PCT
    # กฎบอกให้ข้าม -> engine ต้องออกเป็น watch ไม่ใช่ entry
    got = []
    for seed in range(15):
        for trend in (0.0018, -0.0018):
            full = ind.enrich(_synth(500, seed=seed, trend=trend))   # enrich ครั้งเดียวต่อชุด
            for end in range(240, 500, 3):
                got += [s for s in rules.evaluate("T", full.iloc[:end])
                        if s.rule == "cngoal_entry_skipped"]
    for s in got:
        assert s.kind == "watch" and s.levels["sl_distance_pct"] < rules.SL_MIN_DIST_PCT
    print(f"    เจอเคส SL แคบเกินและถูกข้ามถูกต้อง {len(got)} ครั้ง")


def test_exit_trails_the_requested_ema_and_never_silently_skips():
    """v0.8: trail ได้ทั้ง EMA20 และ EMA50 — และเทสต์ต้องไม่ถูกข้ามเงียบ ๆ

    ของเดิมครอบด้วย `if r["close"] < r["ema20"]` ซึ่งเป็นจริงบ้างไม่จริงบ้างตาม seed
    พอ v0.8 เปลี่ยนค่าเริ่มต้นเป็น EMA50 เทสต์นี้ก็ยัง PASS ทั้งที่ไม่ได้ตรวจอะไรเลย
    → เขียนใหม่ให้ "หาแท่งที่เข้าเงื่อนไขจริง" แล้วบังคับให้ต้องเจออย่างน้อย 1 แท่ง
    """
    for trail in (20, 50):
        col = f"ema{trail}"
        checked = 0
        for seed in range(12):
            df = ind.enrich(_synth(500, seed=seed, trend=0.0006))
            r = df.iloc[-1]
            if not (float(r["close"]) < float(r[col])):
                continue
            # ไม้ long ที่กำลังขาดทุน แต่ปิดต่ำกว่าเส้น trail -> ต้องออก
            pos = {"side": "long", "entry": float(r["close"]) * 1.20,
                   "stop": float(r["close"]) * 0.5, "opened": "2025-01-01"}
            sig = rules.cngoal_exit("T", df, pos, trail_ema=trail)
            assert sig and sig.kind == "exit", f"trail EMA{trail} ต้องออกไม้"
            assert sig.levels["reason"] == f"ema{trail}_cross", sig.levels["reason"]
            assert sig.levels["pnl_pct_gross"] < 0, "v5.1 ต้องออกแม้ขาดทุน"
            checked += 1
        assert checked > 0, f"ไม่เจอแท่งที่ปิดใต้ EMA{trail} เลย — เทสต์นี้ไม่ได้ตรวจอะไร"
        print(f"    trail EMA{trail}: ตรวจจริง {checked} เคส")


def test_exit_hits_stop_regardless_of_trail_setting():
    """ชน SL ต้องออกเสมอ ไม่ว่าจะตั้ง trail เป็นเส้นไหน"""
    df = ind.enrich(_synth(500, seed=9, trend=0.001))
    r = df.iloc[-1]
    stop = (float(r["low"]) + min(float(r["open"]), float(r["close"]))) / 2
    pos = {"side": "long", "entry": float(r["close"]) * 0.9,
           "stop": stop, "opened": "2025-01-01"}
    for trail in (20, 50):
        sig = rules.cngoal_exit("T", df, pos, trail_ema=trail)
        assert sig and sig.levels["reason"] == "stop", sig.levels["reason"]
        # levels ปัดเป็น 4 ตำแหน่งก่อนเขียนลง JSON จึงเทียบแบบเผื่อการปัด
        assert abs(sig.levels["exit"] - round(stop, 4)) < 1e-6, \
            f"ต้องออกที่ราคา stop จริง ได้ {sig.levels['exit']} ควรเป็น {round(stop, 4)}"


def test_trail_ema_rejects_unsupported_values():
    """ตั้งค่าผิดต้องดังทันที ไม่ใช่เงียบแล้วไปพังทีหลัง"""
    df = ind.enrich(_synth(300, seed=3))
    r = df.iloc[-1]
    pos = {"side": "long", "entry": float(r["close"]),
           "stop": float(r["close"]) * 0.9, "opened": "2025-01-01"}
    for bad in (10, 100, 0):
        try:
            rules.cngoal_exit("T", df, pos, trail_ema=bad)
        except ValueError:
            continue
        raise AssertionError(f"trail_ema={bad} ควรถูกปฏิเสธ")


def test_atr_stop_is_exactly_two_atr_from_close():
    """v0.8: stop_mode="atr2" ต้องวาง SL ที่ ATR14 x 2 จากราคาปิดพอดี

    ตรวจทั้งสองฝั่ง และตรวจว่า stop_source บอกที่มาถูกต้อง เพื่อให้ brief อธิบายได้
    """
    found = 0
    for seed in range(40):
        for trend in (0.0012, -0.0012):
            df = ind.enrich(_synth(400, seed=seed, trend=trend))
            sig = rules.cngoal_entry("T", df, stop_mode="atr2")
            if sig is None or sig.kind != "entry":
                continue
            r = df.iloc[-1]
            want = float(r["close"]) + (-1 if sig.direction == "long" else 1) \
                   * rules.ATR_STOP_MULT * float(r["atr14"])
            assert abs(sig.levels["stop"] - round(want, 4)) < 1e-3, \
                f"{sig.direction}: ได้ {sig.levels['stop']} ควรเป็น {want}"
            assert sig.levels["stop_source"] == "atr2"
            found += 1
    assert found >= 3, f"เจอสัญญาณ atr2 แค่ {found} ครั้ง — น้อยเกินกว่าจะเชื่อ"
    print(f"    ตรวจ SL แบบ ATR x2 จริง {found} เคส")


def test_chart_and_atr_stops_actually_differ():
    """ถ้าสองโหมดให้ผลเหมือนกันหมด แปลว่า config ไม่ได้ถูกใช้จริง"""
    diff = 0
    for seed in range(40):
        df = ind.enrich(_synth(400, seed=seed, trend=0.0012))
        a = rules.cngoal_entry("T", df, stop_mode="chart")
        b = rules.cngoal_entry("T", df, stop_mode="atr2")
        if a and b and a.kind == b.kind == "entry":
            if abs(a.levels["stop"] - b.levels["stop"]) > 1e-6:
                diff += 1
    assert diff > 0, "stop_mode ไม่มีผลต่อราคา SL เลย — น่าจะไม่ได้ถูกส่งเข้าไป"
    print(f"    สองโหมดให้ SL ต่างกัน {diff} เคส")


def test_atr_mode_falls_back_to_chart_when_atr_unavailable():
    """ATR เป็น NaN ช่วง warm-up -> ต้องถอยไปใช้กฎเดิม ไม่ใช่ทิ้งสัญญาณ"""
    df = ind.enrich(_synth(400, seed=5, trend=0.0012))
    r = df.iloc[-1].copy()
    r["atr14"] = float("nan")
    got = rules._place_stop("long", r, "atr2")
    assert got is not None, "ต้องถอยไปใช้ chart ไม่ใช่คืน None"
    assert got[1] in ("swing_low10", "ema50"), got[1]


def test_watchlist_gives_atr_to_stocks_and_gold_but_not_crypto():
    """หัวใจของ v0.8 — ห้ามใช้ stop เดียวกันทั้งระบบ

    backtest: หุ้น/ทอง atr2 ดีกว่า (+0.157R -> +0.178R, ทน slippage 0.40%)
              BTC chart ดีกว่า (+2.877R vs +2.084R)
    """
    import yaml, pathlib
    cfg = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parent.parent / "watchlist.yml")
        .read_text(encoding="utf-8"))
    rc = cfg["rules"]
    assert rc["trail_ema"] == 50, "v0.8 ต้อง trail ด้วย EMA50"
    by = rc["stop_mode_by_class"]
    assert by["stock"] == "atr2" and by["metal"] == "atr2"
    assert by["crypto"] == "chart", "crypto ต้องคง SL ตามชาร์ต — ทดสอบแล้วแย่ลง"


def test_run_resolves_stop_mode_per_asset_class():
    """ลำดับความสำคัญ: ตั้งรายตัว > ตั้งตาม class > ค่าเริ่มต้น"""
    default, by = "chart", {"stock": "atr2", "metal": "atr2", "crypto": "chart"}
    def resolve(item):
        return item.get("stop_mode") or by.get(item.get("class", "other"), default)
    assert resolve({"class": "stock"}) == "atr2"
    assert resolve({"class": "metal"}) == "atr2"
    assert resolve({"class": "crypto"}) == "chart"
    assert resolve({"class": "context"}) == "chart"
    assert resolve({"class": "stock", "stop_mode": "chart"}) == "chart", \
        "ตั้งรายตัวต้องชนะค่าตาม class"

def test_exit_costs_are_deducted():
    df = ind.enrich(_synth(500, seed=4))
    r = df.iloc[-1]
    pos = {"side": "long", "entry": float(r["close"]) * 2, "stop": float(r["close"]) * 3,
           "opened": "2025-01-01"}
    s = rules.cngoal_exit("T", df, pos)
    gap = s.levels["pnl_pct_gross"] - s.levels["pnl_pct_net"]
    assert abs(gap - (2 * rules.TAKER_FEE_PCT + 2 * rules.SLIPPAGE_PCT)) < 1e-9


def test_position_blocks_new_entry():
    df = ind.enrich(_synth(500, seed=5, trend=0.0015))
    pos = {"side": "long", "entry": 1.0, "stop": 0.5, "opened": "2025-01-01"}
    for s in rules.evaluate("T", df, position=pos):
        assert s.kind == "exit", "มีไม้เปิดอยู่ ต้องไม่ออกสัญญาณเข้าซ้ำ"


def test_stocks_can_signal_short_by_default():
    """หุ้น US ต้องออกสัญญาณ short ได้ — เทรดผ่าน margin/CFD ชอร์ตได้"""
    shorts = longs = 0
    for seed in range(20):
        full = ind.enrich(_synth(500, seed=seed, trend=-0.0018, vol=0.016))
        for end in range(240, len(full), 3):
            for s in rules.evaluate("STOCK", full.iloc[:end], leverage_cap=5, equity=100):
                if s.kind != "entry":
                    continue
                if s.direction == "short":
                    shorts += 1
                    assert s.levels["stop"] > s.levels["entry"], "SL ของ short ต้องอยู่เหนือ entry"
                else:
                    longs += 1
    print(f"    สัญญาณ short {shorts} ครั้ง / long {longs} ครั้ง")
    assert shorts > 0, "ค่าเริ่มต้นต้องเข้า short ได้"


def test_long_only_flag_still_works():
    """แต่ถ้าตัวไหนโบรกไม่ให้ชอร์ต ต้องปิดได้ด้วย long_only=True"""
    for seed in range(20):
        full = ind.enrich(_synth(500, seed=seed, trend=-0.0018, vol=0.016))
        for end in range(240, len(full), 3):
            for s in rules.evaluate("X", full.iloc[:end], leverage_cap=5,
                                    equity=100, long_only=True):
                assert not (s.kind == "entry" and s.direction == "short")


def test_short_history_returns_nothing():
    assert rules.evaluate("T", ind.enrich(_synth(200))) == []
    assert rules.evaluate("T", ind.enrich(_synth(220))) == []


def test_trade_frequency_matches_cngoal_expectation():
    """cngoal v5.1 คาดไว้ ~0.7-1 เทรด/เดือน — เช็คว่าเราไม่ได้หลุดไปคนละโลก"""
    trades, months = 0, 0.0
    for seed in range(8):
        for trend in (0.0015, 0.0, -0.0015):
            full = ind.enrich(_synth(760, seed=seed, trend=trend))
            pos = None
            for end in range(240, len(full)):
                sl = rules.evaluate("T", full.iloc[:end], position=pos, include_watch=False)
                for s in sl:
                    if s.kind == "entry":
                        pos = {"side": s.direction, "entry": s.levels["entry"],
                               "stop": s.levels["stop"], "opened": "x"}
                        trades += 1
                    elif s.kind == "exit":
                        pos = None
            months += (760 - 240) / 30.4
    rate = trades / months
    print(f"    ความถี่ที่วัดได้: {rate:.2f} เทรด/เดือน ({trades} ไม้)")
    assert 0.3 <= rate <= 3.0, f"หลุดกรอบที่คาดไว้มาก: {rate:.2f}/เดือน"


# ------------------------------------------------------ agent group split

def test_group_split_covers_every_asset_exactly_once():
    """แยก crypto/gold/stock ต้องครอบคลุมทุกตัวใน watchlist ครบ ไม่ซ้ำ ไม่ขาด"""
    import yaml as _yaml
    cfg = _yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent
                           / "watchlist.yml").read_text())
    seen = []
    for group in engine_run.GROUP_CLASSES:
        seen += [a["symbol"] for a in engine_run.assets_for_group(cfg, group)]
    all_symbols = [a["symbol"] for a in cfg["assets"]]
    assert sorted(seen) == sorted(all_symbols), \
        "ทุกตัวใน watchlist ต้องอยู่ใน group ใด group หนึ่งเท่านั้น ไม่ตกหล่นหรือซ้ำ"


def test_crypto_group_is_only_crypto_class():
    import yaml as _yaml
    cfg = _yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent
                           / "watchlist.yml").read_text())
    got = engine_run.assets_for_group(cfg, "crypto")
    assert got and all(a["class"] == "crypto" for a in got)


def test_gold_group_is_commodities_only():
    """v0.9.1 ขยายจาก metal อย่างเดียวเป็น metal + commodity

    เดิมเทสต์นี้บังคับว่าสาย gold ต้องมีแต่ class metal — เปลี่ยนเพราะ 19 ส.ค. 2026
    รับ SLV (เงิน) กับ USOIL (น้ำมัน) เข้ามา ทั้งคู่เป็นสินค้าโภคภัณฑ์ เวลาตลาดใกล้กัน
    และใช้กฎชุดเดียวกัน (atr2 + ADX 25) จึงให้ agent เดียวกันดูแล
    แต่ยังต้องกันไม่ให้หุ้น/crypto หลุดเข้ามาปนเหมือนเดิม
    """
    import yaml as _yaml
    cfg = _yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent
                           / "watchlist.yml").read_text())
    got = engine_run.assets_for_group(cfg, "gold")
    assert got and all(a["class"] in ("metal", "commodity") for a in got)
    syms = {a["symbol"] for a in got}
    assert {"XAU", "SLV", "USOIL"} <= syms, syms
    # SLV/USOIL ต้องได้ ADX ส่วน XAU ต้องไม่ได้ (เป็นข้อยกเว้นที่ทดสอบแล้ว)
    adx = cfg["rules"]["adx_min_by_class"]
    for a in got:
        want = adx.get(a["class"])
        if a["symbol"] == "XAU":
            assert want is None, "XAU ต้องไม่ใช้ ADX — ทดสอบแล้วแย่ลง"
        else:
            assert want == 25, a["symbol"]


def test_stock_group_includes_context_symbols():
    """SPY/QQQ/DXY (signals:false) ต้องอยู่กับ agent หุ้น ไม่ใช่ลอยไปกลุ่มอื่น"""
    import yaml as _yaml
    cfg = _yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent
                           / "watchlist.yml").read_text())
    got = {a["symbol"] for a in engine_run.assets_for_group(cfg, "stock")}
    assert {"SPY", "QQQ", "DXY"} <= got


# ------------------------------------------------------------ discord notify

def _payload_ok(pl):
    """ตรวจตามข้อจำกัดจริงของ Discord webhook API"""
    import json as _j
    assert len(pl.get("embeds", [])) <= notify.MAX_EMBEDS
    assert len(_j.dumps(pl["embeds"], ensure_ascii=False)) <= notify.MAX_TOTAL
    for e in pl.get("embeds", []):
        assert len(e.get("title", "")) <= 256
        assert len(e.get("description", "")) <= notify.MAX_DESC
        assert len(e.get("fields", [])) <= notify.MAX_FIELDS
        for f in e.get("fields", []):
            assert 1 <= len(f["name"]) <= 256, f
            assert 1 <= len(f["value"]) <= notify.MAX_FIELD_VALUE, f
    assert pl["allowed_mentions"] == {"parse": []}, "ต้องกัน @everyone ที่หลุดมาจากข้อความ"


def test_notify_silent_when_nothing_actionable():
    """วันที่มีแค่ watch ต้องไม่ส่ง — กัน alert fatigue"""
    assert notify.build_payload({"run_date": "2026-08-16", "signals": [],
                                 "halts": [], "errors": []}) is None
    only_watch = {"run_date": "2026-08-16", "halts": [], "errors": [],
                  "signals": [{"symbol": "BTC", "kind": "watch", "direction": "long",
                               "reasons": ["x"], "levels": {}, "rule": "w", "bar_date": "d"}]}
    assert notify.build_payload(only_watch) is None


def test_notify_sends_on_entry_exit_halt_and_errors():
    base = {"run_date": "2026-08-16", "signals": [], "halts": [], "errors": []}
    entry = {"symbol": "NVDA", "kind": "entry", "direction": "short", "rule": "cngoal_entry",
             "bar_date": "2026-08-14", "reasons": ["a", "b"],
             "levels": {"entry": 178.0, "stop": 180.8, "sl_distance_pct": 1.58,
                        "position_size": 63.31, "risk_amount": 1.0, "leverage_cap": 5,
                        "capped_by_leverage": False},
             "checklist": [{"n": i, "name": "c", "pass": True} for i in range(1, 5)]}
    pl = notify.build_payload({**base, "signals": [entry]})
    assert pl and len(pl["embeds"]) == 1
    desc = pl["embeds"][0]["description"]
    assert "NVDA" in desc and "SHORT" in desc
    _payload_ok(pl)
    # halt ต้องมาก่อนทุกอย่าง
    pl2 = notify.build_payload({**base, "signals": [entry], "halts": ["พัก 3 วัน"]})
    assert pl2["embeds"][0]["description"].splitlines()[0].startswith("🛑")
    assert pl2["content"].startswith("🛑")
    # errors อย่างเดียวก็ต้องส่ง ห้ามเงียบ
    assert notify.build_payload({**base, "errors": [{"symbol": "XAU", "error": "e"}]})


def test_notify_exit_sorted_before_entry():
    base = {"run_date": "d", "halts": [], "errors": []}
    mk = lambda k: {"symbol": k[:3].upper(), "kind": k, "direction": "long", "rule": "r",
                    "bar_date": "d", "reasons": ["x"],
                    "levels": {"entry": 1.0, "exit": 2.0, "pnl_pct_net": 1.0,
                               "stop": 0.5, "sl_distance_pct": 5.0}}
    pl = notify.build_payload({**base, "signals": [mk("entry"), mk("exit")]})
    first = pl["embeds"][0]["description"].splitlines()[0]
    assert first.startswith(notify.EMO["exit"]), "ปิดไม้ต้องขึ้นก่อนเปิดไม้เสมอ"


def test_notify_respects_discord_limits_under_load():
    """ยิงสัญญาณเยอะเกินจริง เพื่อดูว่า payload ยังอยู่ในลิมิต"""
    sigs = [{"symbol": f"SYM{i}", "kind": "entry", "direction": "long", "rule": "cngoal_entry",
             "bar_date": "2026-08-14", "reasons": ["เหตุผลยาวมาก " * 60] * 6,
             "levels": {"entry": 1.0, "stop": 0.9, "sl_distance_pct": 10.0,
                        "position_size": 10.0, "risk_amount": 1.0, "leverage_cap": 5,
                        "capped_by_leverage": True},
             "checklist": [{"n": j, "name": "c", "pass": True} for j in range(1, 5)]}
            for i in range(30)]
    pl = notify.build_payload({"run_date": "d", "signals": sigs, "halts": [], "errors": []})
    _payload_ok(pl)


def test_notify_handles_current_live_signals_shape():
    """เช็คว่า schema จริงจาก engine.run ล่าสุด (ทุก group) เข้ากับ notify.py ได้ไม่ throw

    ตั้งใจไม่ยืนยันว่าต้อง "มี entry/exit" เพราะวันส่วนใหญ่ควรว่างเปล่าโดยดีไซน์
    (กัน alert fatigue) — เทสต์นี้จับแค่ schema mismatch ระหว่าง run.py กับ notify.py
    """
    import json as _j
    root = pathlib.Path(__file__).resolve().parent.parent
    checked = 0
    for group in engine_run.GROUP_CLASSES:
        f = root / "data" / f"signals_{group}.json"
        if not f.exists():
            continue
        data = _j.loads(f.read_text())
        pl = notify.build_payload(data)   # ต้องไม่ throw ไม่ว่าจะมีสัญญาณหรือไม่
        if pl is not None:
            _payload_ok(pl)
        checked += 1
    print(f"    ตรวจ live signals_<group>.json ที่มีอยู่จริง {checked} ไฟล์")


def test_notify_message_is_compact():
    """Nana 22 ส.ค. 2026: ข้อความยาวเกิน -> 1 สัญญาณต้องได้ 1 บรรทัด ไม่มี fields

    กันการถอยกลับไปเป็น embed ต่อสัญญาณ ซึ่งทำให้ต้องเลื่อนอ่านบนมือถือ
    """
    mk = lambda i, kind="entry": {
        "symbol": f"SYM{i}", "kind": kind, "direction": "long", "rule": "cngoal_entry",
        "bar_date": "2026-08-20", "reasons": ["เหตุผลยาวมาก " * 40] * 6,
        "levels": {"entry": 100.0, "stop": 95.0, "sl_distance_pct": 5.0,
                   "position_size": 10.0, "risk_amount": 1.0, "leverage_cap": 5},
        "checklist": [{"n": j, "name": "c", "pass": j < 6} for j in range(1, 7)]}
    sigs = [mk(i) for i in range(6)] + [mk(i, "watch") for i in range(6, 20)]
    pl = notify.build_payload({"run_date": "2026-08-20", "spec_version": rules.SPEC_VERSION,
                               "signals": sigs, "halts": [], "errors": []})
    _payload_ok(pl)
    assert len(pl["embeds"]) == 1, "ต้องรวมเป็น embed เดียว"
    assert not pl["embeds"][0].get("fields"), "ห้ามมี fields — บรรทัดเดียวพอ"
    lines = pl["embeds"][0]["description"].splitlines()
    # 6 entry + 1 บรรทัดรวม watch
    assert len(lines) == 7, lines
    assert all(len(x) < 160 for x in lines), "แต่ละบรรทัดต้องสั้นพอสำหรับจอมือถือ"
    assert lines[0].startswith(notify.EMO["entry"]) and "5/6" in lines[0]
    assert lines[-1].startswith(notify.EMO["watch"]) and "+6" in lines[-1], lines[-1]
    assert len(pl["embeds"][0]["description"]) < 1200, "ข้อความรวมต้องสั้น"


def test_notify_passed_counts_are_not_hardcoded():
    """checklist 5 ข้อ (crypto) กับ 6 ข้อ (stock/commodity) ต้องแสดงตัวหารตามจริง"""
    mk5 = {"checklist": [{"n": i, "name": "c", "pass": i <= 4} for i in range(1, 6)]}
    mk6 = {"checklist": [{"n": i, "name": "c", "pass": True} for i in range(1, 7)]}
    assert notify.passed(mk5) == "4/5"
    assert notify.passed(mk6) == "6/6"
    assert notify.passed({}) == ""


def test_notify_price_precision_is_order_ready():
    """142.79 ห้ามกลายเป็น 142.8 — ราคาต้องเอาไปตั้งออเดอร์ได้ตรง"""
    assert notify._fmt(142.7899932861328) == "142.79"
    assert notify._fmt(3421.5) == "3,421.50"
    assert notify._fmt(0.00012345678) == "0.000123457"


# ==================================================================== v0.6
# ทั้งหมดนี้คือช่องโหว่ที่ v0.5 ไม่มี test ครอบเลย จึงไม่มีใครรู้ว่ามันไม่ทำงาน

from datetime import date, timedelta          # noqa: E402
from engine import portfolio as pf            # noqa: E402
from engine import fetch as fetchmod          # noqa: E402

TODAY = date(2026, 8, 17)


def _wick_bar_df(stop_at: float):
    """สร้าง df ที่แท่งสุดท้าย 'ไส้หลุด SL แล้วเด้งกลับปิดเหนือ SL'"""
    df = ind.enrich(_synth(400, seed=3, trend=0.001)).copy()
    i = df.index[-1]
    close = float(df.at[i, "close"])
    df.at[i, "open"] = close * 1.01
    df.at[i, "high"] = close * 1.02
    df.at[i, "low"] = stop_at * 0.99      # ไส้ล่างหลุด SL
    df.at[i, "close"] = close             # แต่ปิดเหนือ SL
    return df


# ---------------------------------------------------------- exit ตามไส้เทียน

def test_exit_fires_when_wick_breaks_stop_even_if_close_recovers():
    df = ind.enrich(_synth(400, seed=3, trend=0.001))
    close = float(df["close"].iloc[-1])
    stop = close * 0.98
    df2 = _wick_bar_df(stop)
    pos = {"side": "long", "entry": close * 0.9, "stop": stop, "opened": "2026-08-01"}
    s = rules.cngoal_exit("T", df2, pos)
    assert s is not None, "ไส้หลุด SL แล้วต้องถือว่าไม้ปิด — SL order จริงตัดไปแล้ว"
    assert s.levels["reason"] == "stop"
    assert abs(s.levels["exit"] - round(stop, 4)) < 1e-6, "ต้องออกที่ราคา stop ไม่ใช่ราคาปิด"
    assert s.levels["close"] > s.levels["stop"], "เคสนี้ต้องเป็นเคสที่ปิดเหนือ SL จริง ๆ"


def test_exit_uses_open_price_when_bar_gaps_through_stop():
    df = ind.enrich(_synth(400, seed=11, trend=-0.001)).copy()
    i = df.index[-1]
    close = float(df.at[i, "close"])
    stop = close * 1.05                        # เปิดมาก็ต่ำกว่า SL แล้ว
    df.at[i, "open"] = close * 1.01
    df.at[i, "low"] = close * 0.99
    pos = {"side": "long", "entry": close * 1.10, "stop": stop, "opened": "2026-08-01"}
    s = rules.cngoal_exit("T", df, pos)
    assert s and s.levels["reason"] == "stop_gap"
    assert abs(s.levels["exit"] - round(float(df.at[i, "open"]), 4)) < 1e-6


def test_short_exit_also_uses_high_not_close():
    df = ind.enrich(_synth(400, seed=7)).copy()
    i = df.index[-1]
    close = float(df.at[i, "close"])
    stop = close * 1.01
    df.at[i, "high"] = stop * 1.01              # ไส้บนหลุด SL
    df.at[i, "open"] = close
    pos = {"side": "short", "entry": close * 1.1, "stop": stop, "opened": "2026-08-01"}
    s = rules.cngoal_exit("T", df, pos)
    assert s and s.levels["reason"] == "stop"


# ------------------------------------------------------------- risk จาก config

def test_risk_pct_is_read_from_config_not_hardcoded():
    a = rules._sizing(100.0, 95.0, 5.0, 1000.0, risk_pct=1.0)
    b = rules._sizing(100.0, 95.0, 5.0, 1000.0, risk_pct=2.0)
    assert abs(b["position_size"] - 2 * a["position_size"]) < 1e-6
    assert a["risk_amount"] == 10.0 and b["risk_amount"] == 20.0
    assert a["risk_pct"] == 1.0 and b["risk_pct"] == 2.0


def test_run_passes_config_risk_into_rules():
    import inspect
    src = inspect.getsource(engine_run.main)
    assert "pct_per_trade" in src and "risk_pct=risk_pct" in src, \
        "run.py ต้องส่งค่า risk จาก watchlist.yml เข้า rules ไม่ใช่ปล่อยให้ใช้ค่า default"


# ----------------------------------------------------------- พัก 3 วัน (deadlock)

def test_three_losses_trigger_pause_and_reset_streak():
    st = pf.default_state()
    for _ in range(2):
        pf.register_loss_streak(st, True, TODAY, pause_after=3, pause_days=3)
    assert st["paused_until"] is None and st["consecutive_losses"] == 2
    notes = pf.register_loss_streak(st, True, TODAY, pause_after=3, pause_days=3)
    assert notes and st["paused_until"] == (TODAY + timedelta(days=3)).isoformat()
    # จุดสำคัญ: ต้องรีเซ็ตเป็น 0 ไม่งั้นจะ halt ค้างตลอดกาล = deadlock ของ v0.5
    assert st["consecutive_losses"] == 0


def test_pause_expires_by_itself():
    st = pf.default_state()
    st["paused_until"] = (TODAY + timedelta(days=3)).isoformat()
    assert pf.is_paused(st, TODAY) is True
    assert pf.is_paused(st, TODAY + timedelta(days=2)) is True
    assert pf.is_paused(st, TODAY + timedelta(days=3)) is False, "ครบกำหนดต้องกลับมาเทรดเอง"


def test_win_resets_loss_streak():
    st = pf.default_state()
    pf.register_loss_streak(st, True, TODAY, 3, 3)
    pf.register_loss_streak(st, False, TODAY, 3, 3)
    assert st["consecutive_losses"] == 0


# --------------------------------------------------------- drawdown halt 15%

def test_drawdown_halt_triggers_at_limit_and_blocks_entries():
    p = pf.default_portfolio(100)
    pf.apply_pnl(p, -14.0)
    pf.check_drawdown_halt(p, 15)
    assert not p["halted"], "14% ยังไม่ถึงเพดาน ห้าม halt"
    pf.apply_pnl(p, -1.5)
    pf.check_drawdown_halt(p, 15)
    assert p["halted"] and "15" in p["halt_reason"]
    st = pf.default_state()
    assert pf.entry_blockers(st, p, TODAY, 1), "halt แล้วต้องบล็อกการเปิดไม้จริง ไม่ใช่แค่ข้อความ"


def test_halt_does_not_clear_itself_when_equity_recovers():
    p = pf.default_portfolio(100)
    pf.apply_pnl(p, -20.0)
    pf.check_drawdown_halt(p, 15)
    pf.apply_pnl(p, +19.0)                 # เด้งกลับเกือบเท่าเดิม
    pf.check_drawdown_halt(p, 15)
    assert p["halted"], "v5.1 บอกให้ 'ทบทวนกฎ' — ปลดเองอัตโนมัติเท่ากับไม่ได้ทบทวน"
    pf.resume(p)
    assert not p["halted"] and p["peak"] == p["equity"]


def test_equity_compounds_and_peak_only_goes_up():
    p = pf.default_portfolio(100)
    pf.apply_pnl(p, +20)
    assert p["equity"] == 120 and p["peak"] == 120
    pf.apply_pnl(p, -30)
    assert p["equity"] == 90 and p["peak"] == 120
    assert abs(pf.drawdown_pct(p) - 25.0) < 1e-9


def test_position_size_grows_with_equity():
    """compounding: v5.1 บอก risk% คิดจากยอดพอร์ตปัจจุบันเสมอ"""
    small = rules._sizing(100.0, 95.0, 5.0, 100.0, risk_pct=1.0)["position_size"]
    big = rules._sizing(100.0, 95.0, 5.0, 200.0, risk_pct=1.0)["position_size"]
    assert abs(big - 2 * small) < 1e-6


# --------------------------------------------------- โหมด auto (v0.7)

def _fake_entry_signal(sym="BTC"):
    return {"symbol": sym, "kind": "entry", "direction": "long",
            "levels": {"entry": 60000.0, "stop": 57000.0, "sl_distance_pct": 5.0,
                       "position_size": 20.0, "risk_amount": 1.0}}


def test_signal_opens_position_immediately():
    st = pf.default_state()
    pf.open_position(st, "BTC", _fake_entry_signal(), "2026-08-17")
    p = st["positions"]["BTC"]
    assert p["entry"] == 60000.0 and p["stop"] == 57000.0 and p["size"] == 20.0
    assert p["opened"] == "2026-08-17" and p["side"] == "long"
    assert p["price_basis"] == "close_of_signal_bar", \
        "ต้องบันทึกไว้ว่าราคานี้เป็นค่าประมาณจากราคาปิด ไม่ใช่ fill จริง"


def test_halt_blocks_auto_open():
    """โหมด auto ยังต้องเคารพ risk gate — นี่คือจุดต่างสำคัญจาก v0.5"""
    st, p = pf.default_state(), pf.default_portfolio(100)
    pf.apply_pnl(p, -20)
    pf.check_drawdown_halt(p, 15)
    assert pf.entry_blockers(st, p, TODAY, max_positions=1), \
        "halt แล้วต้องไม่เปิดไม้ ไม่ว่าจะโหมดไหน"


def test_pause_blocks_auto_open():
    st, p = pf.default_state(), pf.default_portfolio(100)
    st["paused_until"] = (TODAY + timedelta(days=2)).isoformat()
    assert pf.entry_blockers(st, p, TODAY, max_positions=1)


def test_position_cap_blocks_second_trade():
    st, p = pf.default_state(), pf.default_portfolio(100)
    pf.open_position(st, "BTC", _fake_entry_signal(), "2026-08-17")
    assert pf.entry_blockers(st, p, TODAY, max_positions=1)
    assert not pf.entry_blockers(st, p, TODAY, max_positions=2)


def test_full_trade_cycle_updates_equity_and_streak():
    """เปิด → ปิดขาดทุน → equity ลด → นับ streak — ครบวงจรแบบไม่ต้องยืนยันอะไร"""
    st, p = pf.default_state(), pf.default_portfolio(100)
    pf.open_position(st, "BTC", _fake_entry_signal(), "2026-08-10")
    pf.close_position(st, p, "BTC", dict(st["positions"]["BTC"]), 57000.0,
                      "2026-08-17", "stop", TODAY)
    assert p["equity"] < 100 and st["consecutive_losses"] == 1
    assert len(st["closed"]) == 1 and st["closed"][0]["pnl_pct_net"] < 0


def test_close_removes_position_from_register():
    """บั๊กที่เจอตอน smoke test: ปิดไม้แล้วไม่ถูกเอาออกจากทะเบียน
    ผลคือมันถูก 'ปิด' ซ้ำทุกวัน ยอดพอร์ตไหลลงเรื่อย ๆ และสั่งพักทั้งที่มีไม้เดียว"""
    st, p = pf.default_state(), pf.default_portfolio(100)
    pf.open_position(st, "BTC", _fake_entry_signal(), "2026-08-10")
    pf.close_position(st, p, "BTC", dict(st["positions"]["BTC"]), 57000.0,
                      "2026-08-17", "stop", TODAY)
    assert "BTC" not in st["positions"], "ปิดไม้แล้วต้องหายจาก positions ทันที"
    assert len(st["closed"]) == 1
    eq_after_first = p["equity"]
    # ถ้ารันซ้ำอีกวัน ต้องไม่มีอะไรให้ปิดอีก
    assert not st["positions"] and p["equity"] == eq_after_first


def test_migrate_drops_stale_pending_from_v06():
    st = pf.migrate_state({"positions": {}, "pending": {"BTC": {"entry": 1}},
                           "closed": [], "consecutive_losses": 0})
    assert "pending" not in st, "โหมด auto ไม่มีสถานะรอยืนยันแล้ว ต้องล้างของเก่าทิ้ง"


def test_manual_json_only_has_equity_and_resume():
    assert set(pf.MANUAL_TEMPLATE) == {"_readme", "equity", "resume"}, \
        "manual.json ต้องเหลือแค่ 2 ช่อง — confirm/close ถูกตัดออกใน v0.7"


def test_manual_equity_sync_and_resume():
    st, p = pf.default_state(), pf.default_portfolio(100)
    pf.apply_pnl(p, -20)
    pf.check_drawdown_halt(p, 15)
    manual = {"equity": 95.0, "resume": True}
    pf.apply_manual(st, p, manual, {"BTC"}, TODAY)
    assert p["equity"] == 95.0 and not p["halted"]
    assert manual["equity"] is None and manual["resume"] is False


def test_absurd_gap_is_treated_as_bad_data_not_a_real_loss():
    """หุ้นแตกพาร์ / สลับแหล่งข้อมูล / feed เสีย ต้องไม่ทำให้ยอดพอร์ตพังถาวร"""
    st, p = pf.default_state(), pf.default_portfolio(100)
    pos = {"side": "long", "entry": 100.0, "stop": 97.0, "size": 33.0, "opened": "2026-08-10"}
    notes = pf.close_position(st, p, "BTC", pos, 0.02, "2026-08-17", "stop_gap", TODAY)
    rec = st["closed"][0]
    assert rec["suspicious_data"] and rec["exit"] == 97.0
    assert rec["raw_exit"] == 0.02, "ตัวเลขดิบต้องยังเก็บไว้ให้ตรวจย้อนหลังได้"
    assert -5 < rec["pnl_pct_net"] < 0, rec["pnl_pct_net"]
    assert p["equity"] > 98, "ขาดทุนต้องอยู่ราว 1R ไม่ใช่กินพอร์ตครึ่งหนึ่ง"
    assert any("ข้อมูลเพี้ยน" in n for n in notes), "ต้องเตือนดัง ๆ ไม่ใช่แก้เงียบ ๆ"


def test_normal_stop_loss_is_not_flagged_as_suspicious():
    st, p = pf.default_state(), pf.default_portfolio(100)
    pos = {"side": "long", "entry": 100.0, "stop": 97.0, "size": 33.0, "opened": "2026-08-10"}
    pf.close_position(st, p, "BTC", pos, 96.5, "2026-08-17", "stop_gap", TODAY)
    assert "suspicious_data" not in st["closed"][0], "gap ปกติของหุ้นต้องไม่โดนธง"
    assert st["closed"][0]["exit"] == 96.5


def test_run_opens_position_not_pending():
    import inspect
    src = inspect.getsource(engine_run.main)
    assert "portfolio.open_position" in src and "add_pending" not in src
    assert "entry_blockers" in src, "ต้องเช็ค risk gate ก่อนเปิดไม้เสมอ"


# ------------------------------------------------------------ ความสดของข้อมูล

def test_bar_age_detects_stale_data():
    from datetime import datetime, timezone
    now = datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc)
    idx = pd.date_range("2026-08-01", periods=15, freq="D")
    df = pd.DataFrame({"close": range(15)}, index=idx)
    assert fetchmod.bar_age_days(df, now) == 2
    assert fetchmod.bar_age_days(df.iloc[:-2], now) == 4
    assert fetchmod.bar_age_days(pd.DataFrame(), now) > 100


def test_run_skips_signals_when_data_is_stale():
    import inspect
    src = inspect.getsource(engine_run.main)
    assert "stale" in src and "ข้ามการออกสัญญาณ" in src, \
        "ข้อมูลเก่าต้องไม่ออกสัญญาณ — ปัญหา 17 ส.ค. 2026 คือกราฟช้าไป 2 วันแต่ระบบเงียบ"


def test_kraken_is_primary_source_for_btc():
    import yaml as _y
    cfg = _y.safe_load((pathlib.Path(__file__).resolve().parent.parent
                        / "watchlist.yml").read_text(encoding="utf-8"))
    btc = next(a for a in cfg["assets"] if a["symbol"] == "BTC")
    assert btc["sources"][0] == "kraken", "Yahoo ปล่อยแท่ง Daily ของ crypto ช้า"
    assert fetchmod.KRAKEN_PAIRS["BTC-USD"] == "XBTUSD"


def test_gold_has_no_cross_instrument_fallback():
    import yaml as _y
    cfg = _y.safe_load((pathlib.Path(__file__).resolve().parent.parent
                        / "watchlist.yml").read_text(encoding="utf-8"))
    xau = next(a for a in cfg["assets"] if a["symbol"] == "XAU")
    assert xau["sources"] == ["yahoo"], \
        "ห้ามใส่ PAXG เป็น fallback ของ XAU — คนละสินทรัพย์ ราคาไม่เท่ากัน EMA จะกระโดด"


# ------------------------------------------------------------------ workflows

def test_workflows_do_not_share_a_cron_slot():
    import re
    wf = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
    crons = {}
    for f in sorted(wf.glob("*.yml")):
        m = re.search(r'cron:\s*"([^"]+)"', f.read_text(encoding="utf-8"))
        assert m, f"{f.name} ไม่มี cron"
        crons[f.name] = m.group(1)
    assert len(set(crons.values())) == len(crons), f"cron ชนกัน: {crons}"


def test_workflows_serialise_pushes_and_retry():
    wf = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
    for f in sorted(wf.glob("*.yml")):
        t = f.read_text(encoding="utf-8")
        assert "group: agent-trade-data" in t, f"{f.name} ต้องใช้ concurrency group เดียวกัน"
        assert "for i in 1 2 3 4 5" in t, f"{f.name} ต้องมี retry loop ตอน push"
        assert "git rm -f --ignore-unmatch data/signals.json" in t, \
            f"{f.name} ต้องลบไฟล์ค้างจาก v0.4"
        assert "brief_" in t and "git add -A data/" in t, \
            f"{f.name} ต้อง commit brief html ให้ Cowork เปิดดูได้"


# --------------------------------------------- v0.9 / cngoal v6.0 (19 ส.ค. 2026)

def test_adx_basic_properties():
    """ADX ต้องอยู่ในช่วง 0-100 และแรงกว่าในตลาดที่มีเทรนด์จริง"""
    trend = ind.enrich(_synth(600, seed=3, trend=0.004, vol=0.008))
    chop = ind.enrich(_synth(600, seed=3, trend=0.0, vol=0.008))
    for d in (trend, chop):
        v = d["adx14"].dropna()
        assert len(v) > 400
        assert (v >= 0).all() and (v <= 100).all()
    # ค่ากลางของตลาดมีเทรนด์ต้องสูงกว่าตลาดออกข้างอย่างชัดเจน
    assert trend["adx14"].median() > chop["adx14"].median() + 5, (
        trend["adx14"].median(), chop["adx14"].median())
    # warm-up ต้องเป็น NaN ไม่ใช่ 0 (0 จะผ่านการเทียบ >= ไม่ได้ แต่ก็ไม่ควรมีค่ามั่ว)
    assert trend["adx14"].iloc[:20].isna().any()


def test_ema_stack_actually_blocks_entries():
    """เงื่อนไข EMA20>EMA50 ต้อง "ทำงานจริง" ไม่ใช่ผ่านฟรีทุกครั้ง

    บทเรียนจาก v0.8: เทสต์ที่ครอบด้วย if แล้วไม่เคยเข้าเงื่อนไข = PASS โดยไม่ตรวจอะไร
    ที่นี่จึงนับเคสที่ "ต่างกันจริง" แล้ว assert ว่าต้อง > 0
    """
    blocked = 0
    with_stack = without = 0
    for seed in range(30):
        for trend in (0.0018, 0.0, -0.0018):
            full = ind.enrich(_synth(500, seed=seed, trend=trend))
            for end in range(240, len(full), 3):
                d = full.iloc[:end]
                a = rules.cngoal_entry("T", d, require_ema_stack=True)
                b = rules.cngoal_entry("T", d, require_ema_stack=False)
                with_stack += int(bool(a and a.kind == "entry"))
                without += int(bool(b and b.kind == "entry"))
                if (b and b.kind == "entry") and not (a and a.kind == "entry"):
                    blocked += 1
    assert without > 0 and with_stack > 0
    assert blocked > 0, "ตัวกรอง EMA20/EMA50 ไม่ได้กรองอะไรเลย = เทสต์นี้ไม่ได้ตรวจอะไร"
    assert with_stack < without, (with_stack, without)


def test_adx_gate_actually_blocks_entries():
    """ADX gate ต้องกรองจริง และยิ่งเกณฑ์สูงยิ่งเหลือน้อยลง (เป็นลำดับ)"""
    counts = {}
    for thr in (None, 15, 25, 40):
        n = 0
        for seed in range(30):
            for trend in (0.0018, -0.0018):
                full = ind.enrich(_synth(500, seed=seed, trend=trend))
                for end in range(240, len(full), 3):
                    s = rules.cngoal_entry("T", full.iloc[:end], adx_min=thr)
                    if s and s.kind == "entry":
                        n += 1
                        if thr is not None:
                            adx_check = [c for c in s.checklist if c["n"] == 6]
                            assert len(adx_check) == 1 and adx_check[0]["pass"]
                            assert s.evidence["adx14"] >= thr
        counts[thr] = n
    assert counts[None] > 0
    assert counts[None] >= counts[15] >= counts[25] >= counts[40], counts
    assert counts[25] < counts[None], "ADX 25 ไม่ได้กรองอะไรเลย"


def test_adx_nan_is_not_a_free_pass():
    """ADX ยังคำนวณไม่ได้ = ไม่ผ่าน (ไม่ใช่ผ่านฟรี) — ต่างจาก stop ที่ถอยไปใช้ chart ได้"""
    full = ind.enrich(_synth(500, seed=7, trend=0.0018))
    d = full.copy()
    d["adx14"] = np.nan
    checked = 0
    for end in range(240, len(d), 3):
        s = rules.cngoal_entry("T", d.iloc[:end], adx_min=25)
        assert s is None or s.kind != "entry"
        checked += 1
    assert checked > 20


def test_portfolio_wide_cap_blocks_across_agents():
    """เพดานรวมทั้งพอร์ตต้องนับไม้ของ agent อื่นด้วย"""
    from datetime import date
    from engine import portfolio as P
    pf = P.default_portfolio(100.0)
    st = P.default_state()
    st["positions"] = {"NVDA": {}, "MSFT": {}}
    P.sync_group_positions(pf, "crypto", 3)
    P.sync_group_positions(pf, "gold", 1)
    today = date(2026, 8, 19)
    # ต่อสายยังไม่เต็ม (2 < 5) และรวมทั้งพอร์ต 2+4 = 6 < 8 -> เปิดได้
    assert P.entry_blockers(st, pf, today, 5, portfolio_max=8, group="stock") == []
    # เพดานรวม 6 -> ตัน แม้ต่อสายจะยังว่าง
    b = P.entry_blockers(st, pf, today, 5, portfolio_max=6, group="stock")
    assert len(b) == 1 and "เพดานรวม" in b[0], b
    # ไม่ส่ง portfolio_max = พฤติกรรมเดิมทุกประการ
    assert P.entry_blockers(st, pf, today, 5) == []
    # นับเฉพาะสายอื่น ไม่นับซ้ำสายตัวเอง
    P.sync_group_positions(pf, "stock", 2)
    assert P.portfolio_open_count(pf, exclude_group="stock") == 4
    assert P.portfolio_open_count(pf) == 6


def test_correlated_group_keeps_only_strongest():
    """สัญญาณหลายตัวในกลุ่มเดียวกันวันเดียวกัน -> เปิดแค่ตัวที่ ADX สูงสุด"""
    cands = [
        {"symbol": "AMD", "score": 85, "reasons": [], "_corr_group": "us_semi", "_adx": 27.0},
        {"symbol": "NVDA", "score": 85, "reasons": [], "_corr_group": "us_semi", "_adx": 41.0},
        {"symbol": "TSM", "score": 85, "reasons": [], "_corr_group": "us_semi", "_adx": 33.0},
        {"symbol": "JPM", "score": 85, "reasons": [], "_corr_group": "us_fin", "_adx": 26.0},
    ]
    ranked = sorted(cands, key=lambda d: (-(d.get("_adx") or 0), -d["score"], d["symbol"]))
    assert [d["symbol"] for d in ranked] == ["NVDA", "TSM", "AMD", "JPM"]
    seen, opened = set(), []
    for d in ranked:
        if d["_corr_group"] in seen:
            continue
        seen.add(d["_corr_group"])
        opened.append(d["symbol"])
    assert opened == ["NVDA", "JPM"], opened


def test_spec_version_and_defaults_are_current():
    assert rules.SPEC_VERSION == "cngoal-6.1"
    assert rules.DEFAULT_REQUIRE_EMA_STACK is True
    assert rules.DEFAULT_ADX_MIN is None, "ค่าเริ่มต้นต้องไม่ใช้ ADX — เปิดผ่าน watchlist เท่านั้น"


def test_no_stale_spec_version_strings_in_code():
    """กันเลขเวอร์ชันค้าง — ของเดิม render.py/notify.py/watchlist ค้างที่ v5.1 ข้ามไป 3 เวอร์ชัน

    ตรวจเฉพาะข้อความที่ "อ้างว่าเป็นสเปกปัจจุบัน" ส่วนที่เล่าประวัติ (เช่น "กฎ v5.1 เดิม")
    ปล่อยไว้ได้ เพราะมันถูกต้องในฐานะบันทึกว่าอะไรเปลี่ยนตอนไหน
    """
    import pathlib as _pl
    root = _pl.Path(__file__).resolve().parent.parent
    cur = rules.SPEC_VERSION.replace("cngoal-", "v")     # "v6.0"
    bad = []
    patterns = ["Machine Spec เป็นระบบหลัก", "รัน CNgoal v", "คำนวณตาม CNgoal v"]
    for f in (root / "engine").glob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for pat in patterns:
                if pat in line and "v5." in line:
                    bad.append(f"{f.name}:{i}: {line.strip()[:80]}")
    assert not bad, "มีเลขเวอร์ชันค้าง:\n" + "\n".join(bad)
    assert cur == "v6.1"


def test_watchlist_adx_only_for_stocks():
    """กันคนเผลอใส่ ADX ให้ทอง/crypto ซึ่งทดสอบแล้วว่าแย่ลง"""
    import yaml, pathlib
    cfg = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parent.parent / "watchlist.yml")
        .read_text(encoding="utf-8"))
    adx = cfg["rules"].get("adx_min_by_class", {}) or {}
    assert adx.get("stock") == 25
    assert adx.get("commodity") == 25          # ยืนยันซ้ำบน SLV/HG/USOIL/GDX (ไม่รวม XAU)
    assert "metal" not in adx and "crypto" not in adx, adx
    for a in cfg["assets"]:
        if a.get("class") in ("metal", "crypto"):
            assert not a.get("adx_min"), a["symbol"]
    # ทุกหุ้นต้องมี corr_group ไม่งั้นกฎกลุ่มสัมพันธ์จะไม่ทำงานกับตัวนั้น
    for a in cfg["assets"]:
        if a.get("class") == "stock":
            assert a.get("corr_group"), a["symbol"]
    assert cfg["risk"]["max_concurrent_portfolio"] == 8
    # ทุก class ที่ใช้ต้องมีที่อยู่ในสักสาย ไม่งั้นสินทรัพย์จะเงียบหายไปเฉย ๆ
    covered = set().union(*engine_run.GROUP_CLASSES.values())
    used = {a.get("class") for a in cfg["assets"]}
    assert used <= covered, f"class ที่ไม่มี agent ไหนดูแล: {used - covered}"
    # ทุก class ที่ไม่ใช่ context ต้องมี stop_mode กำหนดไว้
    sm = cfg["rules"]["stop_mode_by_class"]
    for cl in used - {"context"}:
        assert cl in sm, f"{cl} ไม่มี stop_mode"
    # เลขเวอร์ชันใน watchlist ต้องตรงกับโค้ด — ของเดิมค้างที่ cngoal-5.1 ข้าม 3 เวอร์ชัน
    assert cfg["risk"]["spec_version"] == rules.SPEC_VERSION, (
        cfg["risk"]["spec_version"], rules.SPEC_VERSION)


def test_main_runner_collects_every_test():
    """กัน bug ที่เคยเกิด: ถ้า block __main__ ไม่ได้อยู่ท้ายไฟล์

    เทสต์ที่นิยามหลังจากนั้นจะไม่ถูก globals() เก็บ = ถูกข้ามเงียบ ๆ ตอนรัน
    `python tests/test_engine.py` ทั้งที่ pytest ยังเห็นครบ (บทเรียนจาก v0.8)
    """
    import pathlib, re
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    # ต้อง anchor ที่ต้นบรรทัด ไม่งั้นจะไปเจอ string literal ในเทสต์นี้เอง
    hits = list(re.finditer(r"^if __name__", src, re.M))
    assert len(hits) == 1, f"เจอ guard {len(hits)} จุด"
    after = src[hits[0].start():]
    assert not re.search(r"^def test_", after, re.M), \
        "มี test ถูกนิยามหลัง block __main__ -> จะถูกข้ามตอนรันตรง ๆ"
    n_file = len(re.findall(r"^def test_", src, re.M))
    n_globals = len([k for k in globals() if k.startswith("test_")])
    assert n_file == n_globals, (n_file, n_globals)


def test_run_loop_opens_one_per_correlated_group():
    """เทสต์เส้นทางจริงใน run.py ไม่ใช่แค่ logic การเรียง

    บทเรียน v0.7: smoke test ที่ไม่เดินตาม code path จริงหลอกเราได้
    ที่นี่จึงเรียก run.main() ตรง ๆ โดยปลอมแค่ชั้นดึงข้อมูลกับชั้นออกสัญญาณ
    """
    import json, tempfile, pathlib as _pl
    from datetime import datetime, timezone
    from engine import fetch as _fetch, portfolio as _P

    df = ind.enrich(_synth(400, seed=11, trend=0.002))
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D")

    def fake_load_best(item, max_age_days, now=None, retries=2):
        return df.copy(), {"source": "fake", "bar_age_days": 0, "stale": False, "tried": []}

    fires = {"NVDA": 30.0, "AMD": 45.0, "TSM": 38.0, "JPM": 33.0}

    def fake_evaluate(sym, d, **kw):
        if sym not in fires:
            return []
        px = float(d.iloc[-1]["close"])
        return [rules.Signal(
            sym, "cngoal_entry", "entry", "long", 85, px,
            reasons=["ทดสอบ"],
            levels={"entry": px, "stop": px * 0.95, "sl_distance_pct": 5.0,
                    "position_size": 20.0, "risk_amount": 1.0, "units": 0.2,
                    "stop_source": "atr2"},
            evidence={"adx14": fires[sym]}, checklist=[])]

    old_lb, old_ev = _fetch.load_best, rules.evaluate
    old_age = _fetch.bar_age_days
    old_data, old_pf, old_man = engine_run.DATA, engine_run.PORTFOLIO_FILE, engine_run.MANUAL_FILE
    tmp = _pl.Path(tempfile.mkdtemp())
    try:
        _fetch.load_best = fake_load_best
        _fetch.bar_age_days = lambda d, now=None: 0
        rules.evaluate = fake_evaluate
        engine_run.DATA = tmp
        engine_run.PORTFOLIO_FILE = tmp / "portfolio.json"
        engine_run.MANUAL_FILE = tmp / "manual.json"
        (tmp / "portfolio.json").write_text(
            json.dumps(_P.default_portfolio(100.0)), encoding="utf-8")
        engine_run.main("stock", now=datetime(2026, 8, 19, 23, tzinfo=timezone.utc))
        out = json.loads((tmp / "signals_stock.json").read_text(encoding="utf-8"))
        st = json.loads((tmp / "state_stock.json").read_text(encoding="utf-8"))
    finally:
        _fetch.load_best, rules.evaluate = old_lb, old_ev
        _fetch.bar_age_days = old_age
        engine_run.DATA, engine_run.PORTFOLIO_FILE, engine_run.MANUAL_FILE = (
            old_data, old_pf, old_man)

    opened = set(st["positions"])
    # us_semi มี 3 ตัว (NVDA/AMD/TSM) ต้องเปิดแค่ AMD ซึ่ง ADX สูงสุด · JPM คนละกลุ่มจึงเปิดได้
    assert opened == {"AMD", "JPM"}, opened
    blocked = [s for s in out["signals"] if s["kind"] == "blocked"]
    corr = [s for s in blocked if any("กลุ่ม `us_semi`" in r for r in s["reasons"])]
    assert {s["symbol"] for s in corr} == {"NVDA", "TSM"}, [s["symbol"] for s in corr]
    assert out["engine_version"] == "0.9.2"
    assert out["rules_config"]["max_concurrent_portfolio"] == 8
    # จำนวนไม้ของสายนี้ต้องถูกเขียนลง portfolio.json ให้สายอื่นเห็น
    pf = json.loads((tmp / "portfolio.json").read_text(encoding="utf-8"))
    assert pf["open_by_group"]["stock"] == 2, pf.get("open_by_group")


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for f in fns:
        try:
            f()
            print(f"PASS  {f.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {f.__name__}: {e}")
        except Exception as e:            # v0.6: ของเดิมจับแค่ AssertionError
            failed += 1                   # ถ้า test พังด้วย TypeError ทั้ง suite จะตายกลางคัน
            print(f"ERROR {f.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
