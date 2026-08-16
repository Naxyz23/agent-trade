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

def test_entry_requires_all_four_conditions():
    """ทุกสัญญาณ entry ต้องผ่านครบ 4 ข้อ ไม่มีข้อยกเว้น"""
    n = 0
    for seed in range(25):
        for trend in (0.0018, 0.0, -0.0018):
            full = ind.enrich(_synth(500, seed=seed, trend=trend))
            for end in range(240, len(full), 2):
                for s in rules.evaluate("T", full.iloc[:end]):
                    if s.kind != "entry":
                        continue
                    n += 1
                    assert len(s.checklist) == 4
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


def test_exit_closes_on_ema20_cross_both_directions():
    """v5.1 ตัดเงื่อนไข 'เฉพาะตอนกำไร' ออก — ขาดทุนก็ต้องออก"""
    df = ind.enrich(_synth(500, seed=9, trend=0.001))
    r = df.iloc[-1]
    # ไม้ long ที่กำลังขาดทุน แต่ปิดต่ำกว่า EMA20 -> ต้องออก
    if r["close"] < r["ema20"]:
        pos = {"side": "long", "entry": float(r["close"]) * 1.20,
               "stop": float(r["close"]) * 0.5, "opened": "2025-01-01"}
        s = rules.cngoal_exit("T", df, pos)
        assert s and s.kind == "exit" and s.levels["pnl_pct_gross"] < 0
        assert s.levels["reason"] == "ema20_cross"
    # ชน stop ต้องออกเสมอ
    pos2 = {"side": "long", "entry": float(r["close"]) * 0.9,
            "stop": float(r["close"]) * 1.10, "opened": "2025-01-01"}
    s2 = rules.cngoal_exit("T", df, pos2)
    assert s2 and s2.levels["reason"] == "stop"


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


def test_gold_group_is_only_metal_class():
    import yaml as _yaml
    cfg = _yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent
                           / "watchlist.yml").read_text())
    got = engine_run.assets_for_group(cfg, "gold")
    assert got and all(a["class"] == "metal" for a in got)


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
    assert "NVDA" in pl["embeds"][0]["title"] and "SHORT" in pl["embeds"][0]["title"]
    _payload_ok(pl)
    # halt ต้องมาก่อนทุกอย่าง
    pl2 = notify.build_payload({**base, "signals": [entry], "halts": ["พัก 3 วัน"]})
    assert pl2["embeds"][0]["title"] == "หยุดเทรด" and pl2["content"].startswith("🛑")
    # errors อย่างเดียวก็ต้องส่ง ห้ามเงียบ
    assert notify.build_payload({**base, "errors": [{"symbol": "XAU", "error": "e"}]})


def test_notify_exit_sorted_before_entry():
    base = {"run_date": "d", "halts": [], "errors": []}
    mk = lambda k: {"symbol": k[:3].upper(), "kind": k, "direction": "long", "rule": "r",
                    "bar_date": "d", "reasons": ["x"],
                    "levels": {"entry": 1.0, "exit": 2.0, "pnl_pct_net": 1.0,
                               "stop": 0.5, "sl_distance_pct": 5.0}}
    pl = notify.build_payload({**base, "signals": [mk("entry"), mk("exit")]})
    assert "EXIT" in pl["embeds"][0]["title"], "ปิดไม้ต้องขึ้นก่อนเปิดไม้เสมอ"


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
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
