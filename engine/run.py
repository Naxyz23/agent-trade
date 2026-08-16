"""Orchestrator — ดึงข้อมูล → คำนวณ → รัน CNgoal v5.1 → ติดตามไม้ที่เปิดอยู่ → signals_<group>.json

ทำไมแยกเป็น group (crypto / gold / stock)
------------------------------------------
เดิมสแกนทั้ง 21 ตัวรวมกันเป็นก้อนเดียว 2 รอบ/วัน ตามเวลาที่ประนีประนอมระหว่าง
crypto/gold/stock — ผลคือหลายรอบสแกนซ้ำแท่ง Daily เดิมที่ยังไม่ปิดใหม่ (เปลืองและ
ไม่ได้สัญญาณอะไรเพิ่ม) แยกเป็น 3 workflow ให้แต่ละสายรันแค่ตอนแท่งของมันปิดจริง
ๆ ครั้งเดียว/วัน และมี state (ไม้ที่เปิดอยู่ + consecutive_losses) แยกกันเอง เพราะ
ผลเทรดของ crypto ไม่ควรทำให้ agent หุ้นหยุดพักตาม — คนละพฤติกรรมตลาด

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

from . import fetch, indicators, rules

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# class ใน watchlist.yml -> group ที่ agent นี้รับผิดชอบ
GROUP_CLASSES = {
    "crypto": {"crypto"},
    "gold": {"metal"},
    "stock": {"stock", "context"},   # SPY/QQQ/DXY เป็นบริบทของหุ้น US เอาไว้ด้วยกัน
}


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


def load_state(state_file: pathlib.Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {"positions": {}, "closed": [], "consecutive_losses": 0}


def drop_unclosed_bar(df: pd.DataFrame) -> pd.DataFrame:
    """cngoal v5.1: ตัดสินที่แท่ง Daily ที่ปิดแล้วเท่านั้น ห้ามใช้แท่งวันนี้ที่ยังวิ่งอยู่"""
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    if len(df) and df.index[-1] >= today:
        return df.iloc[:-1]
    return df


def main(group: str) -> int:
    cfg = yaml.safe_load((ROOT / "watchlist.yml").read_text())
    risk_cfg = cfg.get("risk", {})
    filt = cfg.get("filters", {})
    assets = assets_for_group(cfg, group)
    state_file = DATA / f"state_{group}.json"
    out_file = DATA / f"signals_{group}.json"
    state = load_state(state_file)
    positions: dict = state.setdefault("positions", {})
    equity = risk_cfg.get("equity_usdt")

    entries, exits, watches, snapshot, errors = [], [], [], [], []

    for item in assets:
        sym = item["symbol"]
        try:
            raw = fetch.load(item.get("fetch_symbol", sym), source=item.get("source", "yahoo"))
            df = drop_unclosed_bar(indicators.enrich(raw))
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)[:200]})
            continue
        if df.empty:
            errors.append({"symbol": sym, "error": "no closed bars"})
            continue

        last = df.iloc[-1]
        bar_date = str(df.index[-1].date())

        def num(v):
            return None if v != v else round(float(v), 2)

        snapshot.append({
            "symbol": sym, "name": item.get("name", sym), "class": item.get("class", "other"),
            "close": round(float(last["close"]), 4),
            "chg_5d_pct": num(last["ret_5d"]), "chg_20d_pct": num(last["ret_20d"]),
            "rsi14": num(last["rsi14"]), "regime": rules._regime(last),
            "above_ema20": bool(last["close"] > last["ema20"]),
            "candle": rules._candle_name(last),
            "in_position": sym in positions, "bar_date": bar_date,
        })

        if item.get("signals") is False:
            continue

        pos = positions.get(sym)
        sigs = rules.evaluate(
            sym, df,
            leverage_cap=item.get("leverage_cap", risk_cfg.get("default_leverage_cap", 5)),
            equity=equity, position=pos,
            include_watch=filt.get("include_watch", True),
            long_only=item.get("long_only", False),
        )

        for sig in sigs:
            d = sig.to_dict()
            d["bar_date"] = bar_date
            d["asset_class"] = item.get("class", "other")

            if sig.kind == "exit":
                pnl = d["levels"]["pnl_pct_net"]
                state.setdefault("closed", []).append(
                    {"symbol": sym, "closed": bar_date, "pnl_pct_net": pnl, **pos})
                state["consecutive_losses"] = (
                    state.get("consecutive_losses", 0) + 1 if pnl < 0 else 0)
                positions.pop(sym, None)
                exits.append(d)

            elif sig.kind == "entry":
                if len(positions) >= risk_cfg.get("max_concurrent_positions", 3):
                    d["kind"] = "watch"
                    d["reasons"].append(
                        f"⚠ มีไม้เปิดอยู่ {len(positions)} ไม้แล้ว (เพดาน "
                        f"{risk_cfg.get('max_concurrent_positions', 3)}) → ยังไม่เปิดเพิ่ม")
                    watches.append(d)
                    continue
                positions[sym] = {"side": sig.direction, "entry": d["levels"]["entry"],
                                  "stop": d["levels"]["stop"], "opened": bar_date}
                entries.append(d)
            else:
                watches.append(d)

    watches.sort(key=lambda d: -d["score"])
    watches = watches[: filt.get("max_watch_per_run", 6)]

    halts = []
    if state.get("consecutive_losses", 0) >= risk_cfg.get("pause_after_losses", 3):
        halts.append(f"แพ้ติดกัน {state['consecutive_losses']} ไม้ → กฎ v5.1 บอกให้พัก 3 วัน "
                     f"และทบทวน setup ก่อนเข้าไม้ใหม่")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "spec_version": rules.SPEC_VERSION,
        "group": group,
        "universe_size": len(assets),
        "signals": exits + entries + watches,     # ออกก่อน เข้าทีหลัง เฝ้าดูท้ายสุด
        "open_positions": [{"symbol": k, **v} for k, v in positions.items()],
        "halts": halts,
        "snapshot": snapshot,
        "errors": errors,
    }
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=json_safe))
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=json_safe))
    print(f"[{group}] {len(exits)} exits, {len(entries)} entries, {len(watches)} watches "
          f"from {len(snapshot)}/{len(assets)} assets"
          + (f", {len(errors)} errors" if errors else ""))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, choices=sorted(GROUP_CLASSES))
    args = ap.parse_args()
    sys.exit(main(args.group))
