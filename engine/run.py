"""Orchestrator — ดึงข้อมูล → คำนวณ → รัน CNgoal v6.0 → ติดตามไม้ → signals_<group>.json

ทำไมแยกเป็น group (crypto / gold / stock)
------------------------------------------
เดิมสแกนทั้ง 21 ตัวรวมกันเป็นก้อนเดียว 2 รอบ/วัน ตามเวลาที่ประนีประนอมระหว่าง
crypto/gold/stock — ผลคือหลายรอบสแกนซ้ำแท่ง Daily เดิมที่ยังไม่ปิดใหม่ แยกเป็น 3
workflow ให้แต่ละสายรันแค่ตอนแท่งของมันปิดจริง ๆ ครั้งเดียว/วัน

สิ่งที่ v0.6 แก้
----------------
* halt/pause บล็อกการเปิดไม้ได้จริง ไม่ใช่แค่ข้อความ (ของเดิมเขียน position ต่อ
  ทั้งที่ halts ไม่ว่าง) — สัญญาณที่ถูกบล็อกจะกลายเป็น kind "blocked" ไม่ใช่หายเงียบ
* equity เดินตามผลเทรดจริง (compounding) และเช็ค drawdown 15% ตาม v5.1
* เช็ค "ความสดของกราฟ" ไม่ใช่แค่เวลาที่รัน — ข้อมูลเก่าเกินเกณฑ์จะไม่ออกสัญญาณ

v0.7: กลับเป็นโหมด auto — สัญญาณที่ผ่าน risk gate จะถูกนับเป็นไม้ทันที
ไม่ต้องยืนยันใน manual.json แล้ว (เหลือไว้แค่ sync ยอดพอร์ตกับปลดล็อก halt)

รันที่ GitHub Actions:  python -m engine.run --group crypto|gold|stock
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

import pandas as pd
import yaml

from . import fetch, indicators, portfolio, rules

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

MANUAL_FILE = DATA / "manual.json"
PORTFOLIO_FILE = DATA / "portfolio.json"

# class ใน watchlist.yml -> group ที่ agent นี้รับผิดชอบ
GROUP_CLASSES = {
    "crypto": {"crypto"},
    "gold": {"metal"},
    "stock": {"stock", "context"},   # SPY/QQQ/DXY เป็นบริบทของหุ้น US เอาไว้ด้วยกัน
}

# กราฟเก่าได้กี่วันถึงยังเชื่อถือได้ — crypto เทรด 7 วัน/สัปดาห์ จึงเข้มกว่า
# หุ้น/ทองมีเสาร์อาทิตย์ + วันหยุดตลาด ต้องเผื่อ (จันทร์เช้าจะเห็นแท่งวันศุกร์ = 3 วัน)
DEFAULT_MAX_AGE = {"crypto": 2, "metal": 5, "stock": 5, "context": 5}


def assets_for_group(cfg: dict, group: str) -> list[dict]:
    classes = GROUP_CLASSES[group]
    return [a for a in cfg["assets"] if a.get("class") in classes]


def json_safe(o):
    """numpy scalar เขียนลง JSON ไม่ได้ — กันไว้ที่จุดเขียนไฟล์ เผื่อมีตัวไหนหลุด"""
    import numpy as np
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    raise TypeError(f"not JSON serializable: {type(o)}")


def drop_unclosed_bar(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """cngoal v5.1: ตัดสินที่แท่ง Daily ที่ปิดแล้วเท่านั้น ห้ามใช้แท่งวันนี้ที่ยังวิ่งอยู่"""
    now = now or datetime.now(timezone.utc)
    today = pd.Timestamp(now.date())
    if len(df) and pd.Timestamp(df.index[-1]).normalize() >= today:
        return df.iloc[:-1]
    return df


def main(group: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    today = now.date()

    cfg = yaml.safe_load((ROOT / "watchlist.yml").read_text(encoding="utf-8"))
    risk_cfg = cfg.get("risk", {})
    filt = cfg.get("filters", {})
    assets = assets_for_group(cfg, group)
    symbols = {a["symbol"] for a in assets}

    risk_pct = float(risk_cfg.get("pct_per_trade", rules.RISK_PCT))

    # v0.8 — กฎวาง SL / เส้น trail อ่านจาก watchlist.yml ไม่ hardcode
    rules_cfg = cfg.get("rules", {}) or {}
    trail_ema = int(rules_cfg.get("trail_ema", rules.DEFAULT_TRAIL_EMA))
    stop_default = str(rules_cfg.get("stop_mode_default", rules.DEFAULT_STOP_MODE))
    stop_by_class = rules_cfg.get("stop_mode_by_class", {}) or {}
    # v0.9 (cngoal v6.0)
    require_stack = bool(rules_cfg.get("require_ema_stack",
                                       rules.DEFAULT_REQUIRE_EMA_STACK))
    adx_by_class = rules_cfg.get("adx_min_by_class", {}) or {}
    max_positions = int(risk_cfg.get("max_concurrent_positions", 1))
    portfolio_max = risk_cfg.get("max_concurrent_portfolio")
    portfolio_max = int(portfolio_max) if portfolio_max is not None else None
    pause_after = int(risk_cfg.get("pause_after_losses", 3))
    pause_days = int(risk_cfg.get("pause_days", 3))
    dd_limit = float(risk_cfg.get("halt_drawdown_pct", 15))
    gap_guard = float(risk_cfg.get("max_gap_loss_r", 5))

    state_file = DATA / f"state_{group}.json"
    out_file = DATA / f"signals_{group}.json"
    state = portfolio.migrate_state(
        portfolio.load_json(state_file, portfolio.default_state()))
    pf = portfolio.load_json(
        PORTFOLIO_FILE, portfolio.default_portfolio(risk_cfg.get("equity_usdt", 100)))
    manual = portfolio.load_json(MANUAL_FILE, portfolio.MANUAL_TEMPLATE)

    events: list[str] = []

    # 1) รับคำสั่งด้วยมือ (sync ยอดพอร์ต / ปลดล็อก halt) ก่อนเสมอ
    notes, manual_touched = portfolio.apply_manual(state, pf, manual, symbols, today)
    events += notes

    entries, exits, watches, snapshot, errors, data_health = [], [], [], [], [], []
    candidates: list[dict] = []       # v0.9 — สัญญาณเข้าที่รอคัดหลังจบลูป

    for item in assets:
        sym = item["symbol"]
        max_age = int(item.get("max_bar_age_days",
                               DEFAULT_MAX_AGE.get(item.get("class"), 5)))
        try:
            raw, meta = fetch.load_best(item, max_age_days=max_age, now=now)
            df = drop_unclosed_bar(indicators.enrich(raw), now)
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)[:200]})
            continue
        if df.empty:
            errors.append({"symbol": sym, "error": "no closed bars"})
            continue

        last = df.iloc[-1]
        bar_date = str(pd.Timestamp(df.index[-1]).date())
        age = fetch.bar_age_days(df, now)
        stale = age > max_age
        data_health.append({"symbol": sym, "source": meta["source"], "bar_date": bar_date,
                            "bar_age_days": age, "max_age_days": max_age, "stale": stale})

        def num(v):
            return None if v != v else round(float(v), 2)

        snapshot.append({
            "symbol": sym, "name": item.get("name", sym), "class": item.get("class", "other"),
            "close": round(float(last["close"]), 4),
            "chg_5d_pct": num(last["ret_5d"]), "chg_20d_pct": num(last["ret_20d"]),
            "rsi14": num(last["rsi14"]), "regime": rules._regime(last),
            "above_ema20": bool(last["close"] > last["ema20"]),
            "candle": rules._candle_name(last),
            "in_position": sym in state["positions"],
            "bar_date": bar_date, "bar_age_days": age, "source": meta["source"],
            "stale": stale,
        })

        if stale:
            # ข้อมูลเก่าเกินไป → ห้ามออกสัญญาณ ดีกว่าออกสัญญาณจากกราฟที่ตกยุค
            errors.append({"symbol": sym, "error": f"ข้อมูลเก่า {age} วัน "
                                                   f"(เกิน {max_age}) — ข้ามการออกสัญญาณ",
                           "kind": "stale"})
            continue
        if item.get("signals") is False:
            continue

        pos = state["positions"].get(sym)
        # ตั้งรายตัว > ตั้งตาม class > ค่าเริ่มต้น
        stop_mode = item.get("stop_mode") or stop_by_class.get(
            item.get("class", "other"), stop_default)
        # ADX: ตั้งรายตัว > ตั้งตาม class > ไม่ใช้
        #   `adx_min: 0` รายตัว = ปิดการตรวจ (ต่างจากไม่ใส่ ซึ่งจะไปใช้ค่าตาม class)
        if "adx_min" in item:
            adx_min = item["adx_min"] or None
        else:
            adx_min = adx_by_class.get(item.get("class", "other"))
        adx_min = float(adx_min) if adx_min is not None else None
        sigs = rules.evaluate(
            sym, df,
            leverage_cap=item.get("leverage_cap", risk_cfg.get("default_leverage_cap", 5)),
            equity=pf.get("equity"), position=pos,
            include_watch=filt.get("include_watch", True),
            long_only=item.get("long_only", False),
            risk_pct=risk_pct,
            stop_mode=stop_mode,
            trail_ema=trail_ema,
            require_ema_stack=require_stack,
            adx_min=adx_min,
        )

        for sig in sigs:
            d = sig.to_dict()
            d["bar_date"] = bar_date
            d["asset_class"] = item.get("class", "other")

            if sig.kind == "exit":
                events += portfolio.close_position(
                    state, pf, sym, pos, float(d["levels"]["exit"]), bar_date,
                    d["levels"]["reason"], today,
                    fee_pct=rules.TAKER_FEE_PCT, slippage_pct=rules.SLIPPAGE_PCT,
                    pause_after=pause_after, pause_days=pause_days,
                    max_gap_loss_r=gap_guard)
                portfolio.check_drawdown_halt(pf, dd_limit)
                exits.append(d)

            elif sig.kind == "entry":
                # v0.9: ยังไม่เปิดตรงนี้ — เก็บไว้ก่อน แล้วค่อยคัดทีเดียวหลังจบลูป
                # เหตุผล: ถ้าเปิดในลูปเลย ใครอยู่ต้นไฟล์ watchlist จะได้ slot ก่อนเสมอ
                # ซึ่งไม่มีเหตุผลรองรับ และตัวที่สัมพันธ์กัน (NVDA/AMD/TSM) จะเข้าพร้อมกันหมด
                d["_corr_group"] = item.get("corr_group") or item.get("class", "other")
                d["_adx"] = d.get("evidence", {}).get("adx14")
                d["_bar_date"] = bar_date
                candidates.append(d)
            else:
                watches.append(d)

    # ---- v0.9: คัดสัญญาณเข้าทั้งหมดพร้อมกัน ----
    # 1) กลุ่มที่สัมพันธ์กันเอาแค่ตัวเดียว (ADX สูงสุด = เทรนด์แรงสุด)
    # 2) เรียงตาม ADX แล้วค่อยไล่เปิดจนกว่าจะชนเพดาน
    def _rank(d):
        return (-(d.get("_adx") or 0), -d["score"], d["symbol"])

    candidates.sort(key=_rank)
    seen_groups: set[str] = set()
    for d in candidates:
        sym = d["symbol"]
        g = d.pop("_corr_group")
        adx_v = d.pop("_adx")
        bar_date = d.pop("_bar_date")
        if g in seen_groups:
            d["kind"] = "blocked"
            d["reasons"].append(
                f"⛔ ไม่เปิดไม้: มีสัญญาณในกลุ่ม `{g}` ที่แรงกว่าเข้าไปแล้วในรอบนี้ "
                f"— นับเป็นความเสี่ยงก้อนเดียวกัน (กฎ correlated group v6.0)")
            watches.append(d)
            continue
        blockers = portfolio.entry_blockers(state, pf, today, max_positions,
                                            portfolio_max=portfolio_max, group=group)
        if blockers:
            d["kind"] = "blocked"
            d["reasons"] += [f"⛔ ไม่เปิดไม้: {b}" for b in blockers]
            watches.append(d)
            continue
        portfolio.open_position(state, sym, d, bar_date)
        seen_groups.add(g)
        d["reasons"].append(
            f"📌 ระบบบันทึกเป็นไม้เปิดแล้ว (size {d['levels'].get('position_size')} · "
            f"เสี่ยง {d['levels'].get('risk_amount')} USDT) — "
            f"จะติดตาม SL/EMA{trail_ema} ให้อัตโนมัติ "
            f"ราคานี้อิงราคาปิดของแท่งที่ให้สัญญาณ ของจริงอาจต่างเล็กน้อย"
            + (f" · ADX14 {adx_v:.1f}" if adx_v is not None else ""))
        entries.append(d)

    # จำนวนไม้เปิดของสายนี้ต้องเขียนลง portfolio.json ให้สายอื่นเห็น (เพดานรวมทั้งพอร์ต)
    portfolio.sync_group_positions(pf, group, len(state.get("positions", {})))

    watches.sort(key=lambda d: -d["score"])
    watches = watches[: filt.get("max_watch_per_run", 6)]

    halts = []
    if pf.get("halted"):
        halts.append(pf.get("halt_reason"))
    if portfolio.is_paused(state, today):
        halts.append(f"อยู่ในช่วงพักหลังแพ้ติดกัน {pause_after} ไม้ — "
                     f"กลับมาเทรดได้ {state['paused_until']}")
    stale_syms = [d["symbol"] for d in data_health if d["stale"]]
    if stale_syms:
        halts.append(f"ข้อมูลเก่าเกินเกณฑ์ ไม่ออกสัญญาณให้: {', '.join(stale_syms)}")

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "run_date": now.strftime("%Y-%m-%d"),
        "spec_version": rules.SPEC_VERSION,
        "engine_version": "0.9",
        "rules_config": {"trail_ema": trail_ema,
                         "stop_mode_default": stop_default,
                         "stop_mode_by_class": stop_by_class,
                         "require_ema_stack": require_stack,
                         "adx_min_by_class": adx_by_class,
                         "max_concurrent_positions": max_positions,
                         "max_concurrent_portfolio": portfolio_max},
        "group": group,
        "universe_size": len([a for a in assets if a.get("signals") is not False]),
        "context_size": len([a for a in assets if a.get("signals") is False]),
        "signals": exits + entries + watches,     # ออกก่อน เข้าทีหลัง เฝ้าดูท้ายสุด
        "open_positions": [{"symbol": k, **v} for k, v in state["positions"].items()],
        "portfolio": {"equity": pf.get("equity"), "peak": pf.get("peak"),
                      "drawdown_pct": round(portfolio.drawdown_pct(pf), 2),
                      "halted": bool(pf.get("halted")),
                      "risk_pct_per_trade": risk_pct},
        "risk_state": {"consecutive_losses": state.get("consecutive_losses", 0),
                       "paused_until": state.get("paused_until")},
        "halts": halts,
        "events": events,
        "data_health": {"oldest_bar_age_days": max([d["bar_age_days"] for d in data_health],
                                                   default=None),
                        "stale_symbols": stale_syms, "per_symbol": data_health},
        "snapshot": snapshot,
        "errors": errors,
    }
    portfolio.save_json(out_file, out, default=json_safe)
    portfolio.save_json(state_file, state, default=json_safe)
    portfolio.save_json(PORTFOLIO_FILE, pf, default=json_safe)
    if manual_touched or not MANUAL_FILE.exists():
        merged = {**portfolio.MANUAL_TEMPLATE, **manual}
        merged["_readme"] = portfolio.MANUAL_TEMPLATE["_readme"]
        portfolio.save_json(MANUAL_FILE, merged, default=json_safe)

    print(f"[{group}] {len(exits)} exits, {len(entries)} entries, {len(watches)} watches "
          f"from {len(snapshot)}/{len(assets)} assets"
          + (f", {len(errors)} errors" if errors else "")
          + (f", stale: {','.join(stale_syms)}" if stale_syms else "")
          + f" | equity {pf.get('equity')} (DD {portfolio.drawdown_pct(pf):.1f}%)")
    for e in events:
        print(f"  · {e}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, choices=sorted(GROUP_CLASSES))
    args = ap.parse_args()
    sys.exit(main(args.group))
