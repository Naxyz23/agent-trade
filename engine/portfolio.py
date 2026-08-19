"""Portfolio + risk state — v0.7

ทำไมต้องมีไฟล์นี้
-----------------
v0.5 มีช่องโหว่เรื่อง risk 5 จุดที่ทั้งหมดมาจากรากเดียวกัน: **ไม่มีใครติดตามยอดพอร์ต**

  1. `halt_drawdown_pct: 15` อยู่ใน watchlist.yml แต่ไม่มีโค้ดไหนอ่านเลย (grep = 0)
  2. `equity_usdt: 100` เป็นค่าคงที่ ไม่ compounding ตามที่ v5.1 กำหนด
  3. ต่อให้ `halts` ไม่ว่าง run.py ก็ยังเปิดไม้ใหม่ต่อ — halt เป็นแค่ข้อความ
  4. "พัก 3 วัน" ไม่มีการนับวัน ถ้า consecutive_losses ค้างที่ 3 จะ halt ตลอดกาล
     (และเข้าไม้ใหม่ไม่ได้ = ไม่มีทางชนะเพื่อรีเซ็ต = deadlock)
  5. `pct_per_trade` ใน yml ไม่ถูกอ่าน — rules.py hardcode ไว้

ไฟล์นี้เก็บ "ยอดพอร์ตจริง" ไว้ที่เดียว (data/portfolio.json) ใช้ร่วมกันทั้ง 3 agent
เพราะ v5.1 พูดถึงพอร์ตทั้งก้อน ไม่ได้แยกตามสินทรัพย์ — ถ้าแยก ตัวเลข drawdown 15%
จะไม่มีความหมาย ส่วน positions/consecutive_losses ยังแยกตาม group เหมือนเดิม
(ผลเทรด crypto ไม่ควรทำให้ agent หุ้นพักตาม — คนละพฤติกรรมตลาด)

cron ของ 3 workflow ถูกจัดให้ห่างกันแล้ว (00:15 / 06:10 / 06:40 UTC) บวกกับ
concurrency group เดียวกัน จึงไม่มีทางเขียน portfolio.json ทับกัน

v0.7 — กลับเป็นโหมด auto
------------------------
v0.6 เคยให้สัญญาณ entry ไปรอ Nana ยืนยันใน manual.json ก่อน ซึ่งถูกต้องในแง่บัญชี
แต่ยุ่งเกินไปในทางปฏิบัติ (ต้องแก้ไฟล์ทุกครั้งที่เข้า/ออกไม้) Nana จึงเลือก auto
ระบบเปิด/ปิดไม้เองตามกฎ ส่วน manual.json เหลือแค่ `equity` กับ `resume`
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timedelta, timezone

STATE_VERSION = 3


# --------------------------------------------------------------- portfolio

def default_portfolio(equity: float) -> dict:
    return {"version": STATE_VERSION, "equity": float(equity), "peak": float(equity),
            "halted": False, "halt_reason": None, "open_by_group": {}, "history": []}


def load_json(path: pathlib.Path, fallback: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return dict(fallback)
    return dict(fallback)


def save_json(path: pathlib.Path, obj: dict, default=None) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=default),
                    encoding="utf-8")


def set_equity(pf: dict, equity: float, note: str = "") -> dict:
    """ตั้งยอดพอร์ตตรง ๆ (Nana sync ยอดจริงจากกระดานมา) — peak ขยับขึ้นได้อย่างเดียว"""
    pf["equity"] = round(float(equity), 4)
    pf["peak"] = round(max(float(pf.get("peak", equity)), float(equity)), 4)
    pf.setdefault("history", []).append(
        {"at": _now_iso(), "event": "set_equity", "equity": pf["equity"], "note": note})
    return pf


def apply_pnl(pf: dict, pnl_amount: float, note: str = "") -> dict:
    """ปรับยอดพอร์ตหลังปิดไม้ — นี่คือจุดเดียวที่ทำให้ compounding ทำงานจริง"""
    pf["equity"] = round(float(pf.get("equity", 0)) + float(pnl_amount), 4)
    pf["peak"] = round(max(float(pf.get("peak", pf["equity"])), pf["equity"]), 4)
    pf.setdefault("history", []).append(
        {"at": _now_iso(), "event": "pnl", "amount": round(float(pnl_amount), 4),
         "equity": pf["equity"], "note": note})
    return pf


def drawdown_pct(pf: dict) -> float:
    peak = float(pf.get("peak", 0) or 0)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - float(pf.get("equity", peak))) / peak * 100)


def check_drawdown_halt(pf: dict, limit_pct: float) -> dict:
    """v5.1: พอร์ตลดจาก peak เกิน 15% → หยุด ทบทวนกฎทั้งชุด

    ตั้งใจให้ปลดล็อกด้วยมือเท่านั้น (manual.json → resume) เพราะกฎบอกให้ "ทบทวนกฎ"
    ไม่ใช่ "รอให้พอร์ตเด้งกลับเอง" — ถ้าปลดอัตโนมัติก็เท่ากับไม่ได้ทบทวนอะไร
    """
    dd = drawdown_pct(pf)
    if dd >= limit_pct and not pf.get("halted"):
        pf["halted"] = True
        pf["halt_reason"] = (f"พอร์ตลดจากจุดสูงสุด {dd:.1f}% (เพดาน {limit_pct}%) — "
                             f"v5.1 บอกให้หยุดและทบทวนกฎทั้งชุด "
                             f"ปลดล็อกด้วยการใส่ \"resume\": true ใน data/manual.json")
        pf.setdefault("history", []).append(
            {"at": _now_iso(), "event": "halt", "drawdown_pct": round(dd, 2)})
    return pf


def resume(pf: dict, note: str = "") -> dict:
    pf["halted"] = False
    pf["halt_reason"] = None
    pf["peak"] = float(pf.get("equity", 0))     # เริ่มนับ drawdown ใหม่จากยอดปัจจุบัน
    pf.setdefault("history", []).append({"at": _now_iso(), "event": "resume", "note": note})
    return pf


# ------------------------------------------------------------- group state

def default_state() -> dict:
    return {"version": STATE_VERSION, "positions": {}, "closed": [],
            "consecutive_losses": 0, "paused_until": None}


def migrate_state(state: dict) -> dict:
    """รับ state เก่าของ v0.5/v0.6 โดยไม่ทำข้อมูลหาย

    ถ้ามี `pending` ค้างจาก v0.6 ให้ยกเลิกทิ้ง — โหมด auto ไม่มีสถานะรอยืนยันแล้ว
    และข้อเสนอเก่าที่ค้างมาข้ามวันก็หมดอายุตามหลัก "เทรนด์ยังอยู่ ≠ จังหวะเข้ายังอยู่"
    """
    state.setdefault("positions", {})
    state.setdefault("closed", [])
    state.setdefault("consecutive_losses", 0)
    state.setdefault("paused_until", None)
    state.pop("pending", None)
    state["version"] = STATE_VERSION
    return state


def register_loss_streak(state: dict, is_loss: bool, today: date,
                         pause_after: int, pause_days: int) -> list[str]:
    """นับไม้แพ้ติดกัน แล้วสั่งพักเป็น "จำนวนวัน" จริง ๆ

    จุดสำคัญ: พอสั่งพักแล้ว **รีเซ็ต consecutive_losses เป็น 0 ทันที**
    ของเดิมไม่รีเซ็ต ทำให้ค้างที่ >= 3 ตลอดไป → halt ตลอดกาล และเข้าไม้ใหม่ไม่ได้
    เพื่อรีเซ็ต = deadlock ที่ต้องแก้ไฟล์ด้วยมือเท่านั้น
    """
    notes = []
    if not is_loss:
        state["consecutive_losses"] = 0
        return notes
    state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
    if state["consecutive_losses"] >= pause_after:
        until = today + timedelta(days=pause_days)
        state["paused_until"] = until.isoformat()
        state["consecutive_losses"] = 0
        notes.append(f"แพ้ติดกัน {pause_after} ไม้ → พัก {pause_days} วัน "
                     f"ถึง {until.isoformat()} ตามกฎ v5.1")
    return notes


def is_paused(state: dict, today: date) -> bool:
    until = state.get("paused_until")
    if not until:
        return False
    try:
        return date.fromisoformat(str(until)) > today
    except ValueError:
        return False


def sync_group_positions(pf: dict, group: str, n_open: int) -> None:
    """บันทึกจำนวนไม้เปิดของสายนี้ลง portfolio.json ที่ใช้ร่วมกันทั้ง 3 agent

    ทำไมต้องมี (v0.9)
    -----------------
    `max_concurrent_positions` เดิมนับ "ต่อ agent" เท่านั้น ทำให้เพดานจริงคือ
    3 ไม้ (3 agent x 1) โดยไม่มีใครตั้งใจให้เป็นแบบนั้น และไม่มีทางตั้งเป็นค่าอื่น
    ที่สมเหตุสมผลได้เลย เพราะ agent แต่ละตัวมองไม่เห็นไม้ของ agent อื่น
    ทั้งที่ equity เป็นก้อนเดียวกัน — ความเสี่ยงจึงบวกกันจริงแต่ไม่มีใครนับ

    ตัวเลขของสายอื่นอาจเก่าได้ถึง 1 วัน (แต่ละ workflow รันคนละเวลา) ซึ่งยอมรับได้
    เพราะทิศทางของความคลาดเคลื่อนปลอดภัย: ไม้ที่ปิดไปแล้วแต่ยังไม่ถูกล้างจะทำให้
    ระบบ "ระมัดระวังเกินจริง" ไม่ใช่ "เสี่ยงเกินจริง"
    """
    pf.setdefault("open_by_group", {})[group] = int(n_open)


def portfolio_open_count(pf: dict, exclude_group: str | None = None) -> int:
    """รวมไม้เปิดทุกสาย — ข้ามสายที่กำลังรันอยู่ (นับจาก state ของตัวเองแทน เพราะสดกว่า)"""
    by = pf.get("open_by_group", {}) or {}
    return sum(int(v) for k, v in by.items() if k != exclude_group)


def entry_blockers(state: dict, pf: dict, today: date, max_positions: int,
                   portfolio_max: int | None = None,
                   group: str | None = None) -> list[str]:
    """เหตุผลทั้งหมดที่ "ห้ามเปิดไม้ใหม่" ตอนนี้ — ว่างเปล่า = เปิดได้

    run.py ต้องเรียกอันนี้ก่อนจะบันทึกอะไรลง state เสมอ
    ของเดิม (v0.5) halt เป็นแค่ข้อความใน JSON แต่โค้ดยังเขียน position ต่อ

    v0.9 เพิ่มเพดานระดับพอร์ต (`portfolio_max`) ทับเพดานต่อ agent อีกชั้น
    จากการจำลอง equity 54 ปี: เพดาน 8 ให้ CAGR 10.7% ขณะที่เพดาน 4 ให้ 8.7%
    โดย maxDD เท่ากันที่ 28.8% — เพดานที่แคบเกินไปตัดกำไรทิ้งโดยไม่ได้ลดความเสี่ยง
    """
    out = []
    if pf.get("halted"):
        out.append(pf.get("halt_reason") or "พอร์ตอยู่ในสถานะ halt")
    if is_paused(state, today):
        out.append(f"อยู่ในช่วงพักหลังแพ้ติดกัน — กลับมาเทรดได้ {state['paused_until']}")
    open_n = len(state.get("positions", {}))
    if open_n >= max_positions:
        out.append(f"มีไม้เปิดอยู่ {open_n} ไม้ในสายนี้ (เพดานต่อสาย {max_positions})")
    if portfolio_max is not None:
        total = open_n + portfolio_open_count(pf, exclude_group=group)
        if total >= portfolio_max:
            out.append(f"ทั้งพอร์ตมีไม้เปิดอยู่ {total} ไม้ (เพดานรวม {portfolio_max})")
    return out


# ------------------------------------------------------- เปิดไม้อัตโนมัติ

def open_position(state: dict, sym: str, sig: dict, bar_date: str) -> None:
    """สัญญาณครบ 4 ข้อ + ผ่าน risk gate → นับเป็นไม้ทันที (โหมด auto)

    v0.6 เคยให้รอ Nana ยืนยันผ่าน manual.json ก่อน แต่ต้องแก้ไฟล์ทุกครั้งที่เข้า/ออกไม้
    ซึ่งยุ่งเกินไปในทางปฏิบัติ Nana จึงเลือกโหมด auto

    สิ่งที่ยังต่างจาก v0.5 และเป็นเหตุผลว่าทำไม auto รอบนี้เชื่อถือได้กว่าเดิม:
      * ก่อนเปิดต้องผ่าน entry_blockers() จริง — halt / ช่วงพัก / เพดานไม้ บล็อกได้
      * size คิดจาก equity ปัจจุบันที่เดินตามผลเทรดจริง ไม่ใช่ 100 ตายตัวตลอดกาล
      * ตอนปิดเช็ค SL ด้วยไส้เทียนและออกที่ราคา stop จริง

    ข้อจำกัดที่ต้องรู้: ราคาที่บันทึกคือ "ราคาปิดของแท่งที่ให้สัญญาณ" ของจริงจะได้
    ราคาเปิดแท่งถัดไป ตัวเลขจึงเป็นค่าประมาณ ไม่ใช่ fill จริงจากกระดาน
    ถ้าอยากให้ตรงเป๊ะต้องต่อ Binance read-only API มาอ่านไม้จริง (ยังไม่ได้ทำ)
    """
    lv = sig.get("levels", {})
    state.setdefault("positions", {})[sym] = {
        "side": sig["direction"],
        "entry": lv.get("entry"),
        "stop": lv.get("stop"),
        "size": lv.get("position_size"),
        "risk_amount": lv.get("risk_amount"),
        "sl_distance_pct": lv.get("sl_distance_pct"),
        "opened": bar_date,
        "price_basis": "close_of_signal_bar",
    }


# ------------------------------------------------------------ manual.json

MANUAL_TEMPLATE = {
    "_readme": [
        "ไฟล์นี้เหลือแค่ 2 ช่อง — ปกติไม่ต้องแตะเลย ระบบเปิด/ปิดไม้เองอัตโนมัติ",
        "equity = ใส่ยอดพอร์ตจริงจากกระดาน ถ้าตัวเลขที่ระบบคำนวณเริ่มเพี้ยนจากของจริง",
        "resume = ใส่ true เพื่อปลดล็อก หลังระบบหยุดเทรดเพราะพอร์ตลดจากจุดสูงสุด 15%",
        "แก้จากหน้าเว็บ GitHub ได้เลย (กดดินสอ → แก้ → Commit) engine จะล้างค่าให้เองรอบถัดไป",
    ],
    "equity": None,
    "resume": False,
}


def apply_manual(state: dict, pf: dict, manual: dict, symbols: set[str],
                 today: date) -> tuple[list[str], bool]:
    """รับคำสั่งด้วยมือที่เหลืออยู่แค่ 2 อย่าง — คืน (บันทึกเหตุการณ์, manual ถูกแก้ไหม)

    โหมด auto ไม่ต้องยืนยันไม้แล้ว แต่ 2 อย่างนี้ยังต้องมีคนตัดสินใจ:
      * equity — ถ้ายอดที่ระบบคำนวณเริ่มห่างจากยอดจริงบนกระดาน (ค่าธรรมเนียมจริง,
        funding, หรือเข้าไม้ที่ราคาต่างจากที่ระบบบันทึก) ต้องมีทางดึงกลับมาให้ตรงกัน
      * resume — v5.1 บอกว่า drawdown 15% ให้ "หยุดและทบทวนกฎทั้งชุด"
        ถ้าปลดล็อกเองอัตโนมัติก็เท่ากับไม่ได้ทบทวนอะไร กฎข้อนี้จะไร้ความหมายทันที
    """
    notes, touched = [], False

    if manual.get("resume"):
        resume(pf, "Nana ปลดล็อกผ่าน manual.json")
        state["paused_until"] = None
        state["consecutive_losses"] = 0
        manual["resume"] = False
        touched = True
        notes.append("ปลดล็อก halt แล้ว — เริ่มนับ drawdown ใหม่จากยอดพอร์ตปัจจุบัน")

    eq = manual.get("equity")
    if eq is not None:
        set_equity(pf, float(eq), "Nana sync ยอดจริง")
        manual["equity"] = None
        touched = True
        notes.append(f"อัปเดตยอดพอร์ตเป็น {float(eq):,.2f} ตามที่ Nana แจ้ง")

    return notes, touched


def close_position(state: dict, pf: dict, sym: str, pos: dict, exit_px: float,
                   closed_on: str, why: str, today: date,
                   fee_pct: float = 0.05, slippage_pct: float = 0.02,
                   pause_after: int = 3, pause_days: int = 3,
                   max_gap_loss_r: float = 5.0) -> list[str]:
    """ปิดไม้ → คิด P&L เป็นเงิน → ปรับ equity → เช็ค drawdown → นับไม้แพ้

    ตัวกันข้อมูลเพี้ยน (max_gap_loss_r)
    -----------------------------------
    ไม้ที่ตั้ง SL ไว้แล้วไม่ควรขาดทุนเกิน ~1R บวก slippage นิดหน่อย ถ้าคำนวณออกมาว่า
    ขาดทุนเกิน 5R แปลว่าเกือบแน่นอนว่า "ข้อมูลผิด" ไม่ใช่ตลาดพังจริง เช่น
      * หุ้นแตกพาร์ (split) แล้วราคาย้อนหลังถูกปรับ แต่ราคา entry ที่บันทึกไว้ยังเป็นของเก่า
      * fallback สลับแหล่งข้อมูล (Kraken ↔ Yahoo) แล้วสเกลราคาไม่ตรงกัน
      * feed คืนค่า 0 หรือค่าขยะมา
    ถ้าปล่อยผ่าน ยอดพอร์ตจะพังถาวรและลาก drawdown halt มาด้วย จึงบันทึกผลขาดทุน
    ที่ราคา stop ตามแผน (คือสิ่งที่ SL order จริงบนกระดานจะทำ) แล้วตะโกนบอกให้ไปเช็ค
    ตัวเลขดิบยังเก็บไว้ใน closed[] เพื่อให้ตรวจย้อนหลังได้
    """
    # เอาไม้ออกจากทะเบียนตรงนี้ที่เดียว — ถ้าปล่อยให้ผู้เรียกทำเอง วันหนึ่งจะลืม
    # (เคยลืมมาแล้วจริง ๆ: ไม้ค้างอยู่แล้วถูก "ปิด" ซ้ำทุกวัน ยอดพอร์ตไหลลงเรื่อย ๆ
    #  และ consecutive_losses พุ่งจนสั่งพักทั้งที่มีไม้เดียว)
    state.setdefault("positions", {}).pop(sym, None)

    entry = float(pos["entry"])
    sign = 1 if pos.get("side", "long") == "long" else -1
    size = pos.get("size")
    notes = []

    raw_gross = (exit_px / entry - 1) * 100 * sign
    costs = 2 * fee_pct + 2 * slippage_pct
    planned_sl_pct = abs(entry - float(pos.get("stop", entry))) / entry * 100
    used_px, suspicious = exit_px, False

    if planned_sl_pct > 0 and -raw_gross > max_gap_loss_r * planned_sl_pct:
        suspicious = True
        used_px = float(pos.get("stop", exit_px))
        notes.append(
            f"⚠️ {sym}: ราคาปิดไม้ที่ได้ ({exit_px:,.6g}) หมายถึงขาดทุน "
            f"{-raw_gross:,.1f}% ทั้งที่ SL ตั้งไว้แค่ {planned_sl_pct:.2f}% "
            f"— เกือบแน่นอนว่าข้อมูลเพี้ยน (split / สลับแหล่งข้อมูล / feed เสีย) "
            f"ไม่ใช่ตลาดพังจริง จึงบันทึกผลที่ราคา stop ตามแผนแทน "
            f"→ กรุณาเช็คยอดจริงบนกระดาน แล้ว sync ผ่าน data/manual.json (equity)")

    gross_pct = (used_px / entry - 1) * 100 * sign
    net_pct = gross_pct - costs
    pnl_amount = (net_pct / 100) * float(size) if size else 0.0

    state.setdefault("closed", []).append({
        "symbol": sym, "side": pos.get("side"), "entry": entry, "exit": round(used_px, 6),
        "opened": pos.get("opened"), "closed": closed_on, "size": size,
        "pnl_pct_net": round(net_pct, 3), "pnl_amount": round(pnl_amount, 4), "why": why,
        **({"raw_exit": round(exit_px, 6), "raw_pnl_pct": round(raw_gross - costs, 3),
            "suspicious_data": True} if suspicious else {}),
    })
    if size:
        apply_pnl(pf, pnl_amount, f"{sym} {why}")
    notes.append(f"{sym}: ปิดไม้ที่ {used_px:,.4g} → {net_pct:+.2f}% สุทธิ"
                 + (f" ({pnl_amount:+,.2f} USDT)" if size
                    else " (ไม่ทราบขนาดไม้ → ไม่ปรับยอดพอร์ต)"))
    notes += register_loss_streak(state, net_pct < 0, today, pause_after, pause_days)
    return notes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
