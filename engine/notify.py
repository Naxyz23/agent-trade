"""ส่งสัญญาณเข้า Discord ผ่าน webhook — โหมด "สั้น" (v0.9.2)

ทำไมให้ GitHub Actions ส่งเอง ไม่ใช่ให้ Cowork ส่ง
------------------------------------------------
ถ้าให้ Cowork ส่ง แปลว่าการแจ้งเตือนขึ้นกับว่า Cowork รันตรงเวลาไหม
แต่ GitHub Actions รันของมันเองอยู่แล้ว ส่งตรงจากตรงนั้นเลยจึงเชื่อถือได้กว่า
Cowork ค่อยตามมาเสริมบริบท (earnings, ข่าว) ทีหลังในรอบของมัน

กันสแปม: ค่าเริ่มต้นส่งเฉพาะวันที่มี entry/exit/halt/error จริง
(ตั้ง notify.on_empty: true ถ้าอยากได้ทุกวัน)

ทำไมข้อความสั้นลง (Nana ขอ 22 ส.ค. 2026)
----------------------------------------
ของเดิมส่ง 1 embed ต่อ 1 สัญญาณ พร้อม reasons 6 บรรทัด + 6 fields
= ข้อความยาวจนต้องเลื่อนอ่าน ทั้งที่ 90% ของเนื้อหาซ้ำกับ brief.html อยู่แล้ว
ตอนนี้เหลือ **1 บรรทัดต่อ 1 สินทรัพย์**: ผ่านกี่ข้อ + ตัวเลขที่ต้องใช้เทรดจริง
(entry / stop / size) ส่วน watch รวบเป็นบรรทัดเดียวท้ายสุด
รายละเอียดเต็ม (reasons, evidence, checklist รายข้อ) อยู่ใน brief.html เหมือนเดิม
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# ข้อจำกัดจริงของ Discord webhook API
MAX_EMBEDS = 10
MAX_FIELDS = 25
MAX_DESC = 4096
MAX_FIELD_VALUE = 1024
MAX_TOTAL = 6000

COLOR = {
    "exit":  0xB91C1C,   # แดง — ต้องทำอะไรบางอย่างกับไม้ที่ถืออยู่
    "entry": 0x0F766E,   # เขียว — เปิดไม้ใหม่
    "blocked": 0x92400E, # ส้มเข้ม — เข้าเงื่อนไขครบ แต่กฎ risk ห้ามเปิดไม้
    "watch": 0x6B7280,   # เทา — แค่ให้รู้
    "halt":  0xDC2626,
}
SIDE = {"long": "LONG", "short": "SHORT", "neutral": ""}
EMO = {"exit": "🔴", "entry": "🟢", "blocked": "🟠", "watch": "👀"}
MAX_WATCH_ITEMS = 8      # เกินนี้รวบเป็น "+N" — บรรทัดเดียวต้องไม่ยาวเกินจอมือถือ


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt(v, nd=6) -> str:
    """ราคาต้องพอเอาไปตั้งออเดอร์ได้จริง

    ของเดิมใช้ 4 significant digits -> 142.79 กลายเป็น 142.8 (คลาดจากราคาจริง)
    เลข >= 1 ใช้ทศนิยม 2 ตำแหน่ง · เลขเล็ก (เหรียญราคาต่ำ) ค่อยใช้ significant digits
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:,.{nd}g}"


def passed(s: dict) -> str:
    """'4/6' — ไม่ hardcode จำนวนข้อ เพราะ stock/commodity มี 6 ข้อ ที่เหลือ 5"""
    ck = s.get("checklist") or []
    if not ck:
        return ""
    return f"{sum(1 for c in ck if c.get('pass'))}/{len(ck)}"


def _blocked_why(s: dict) -> str:
    for r in reversed(s.get("reasons") or []):
        if r.startswith("⛔"):
            r = r.lstrip("⛔ ")
            return r.split(":", 1)[1].strip() if ":" in r else r
    return "กฎ risk บล็อกไว้"


def signal_line(s: dict) -> str:
    """1 สัญญาณ = 1 บรรทัด"""
    side = SIDE.get(s.get("direction", ""), "")
    lv = s.get("levels") or {}
    parts = [f"{EMO.get(s['kind'], '•')} **{s['symbol']}**" + (f" {side}" if side else "")]

    if s["kind"] == "exit":
        parts.append(f"{_fmt(lv.get('entry'))} → {_fmt(lv.get('exit'))}")
        parts.append(f"**{lv.get('pnl_pct_net', 0):+.2f}%**")
        why = lv.get("why") or (s.get("reasons") or [""])[0]
        if why:
            parts.append(_clip(str(why), 48))
        return " · ".join(parts)

    if n := passed(s):
        parts.append(n)

    if s["kind"] == "blocked":
        parts.append("บล็อก: " + _clip(_blocked_why(s), 70))
        return " · ".join(parts)

    if lv.get("entry") is not None:
        parts.append(f"E {_fmt(lv['entry'])}")
        if lv.get("stop") is not None:
            parts.append(f"SL {_fmt(lv['stop'])} ({lv.get('sl_distance_pct', 0):.1f}%)")
        if "position_size" in lv:
            cap = " ⚠เพดาน" if lv.get("capped_by_leverage") else ""
            parts.append(f"size {lv['position_size']:,.2f}{cap}")
        elif lv.get("position_pct_of_equity"):
            parts.append(f"{lv['position_pct_of_equity']:.1f}% ของพอร์ต")
    return " · ".join(parts)


def watch_line(watch: list[dict]) -> str:
    """ตัวที่ยังไม่ครบ รวบเป็นบรรทัดเดียว เรียงตัวที่ใกล้ครบที่สุดขึ้นก่อน"""
    ranked = sorted(watch, key=lambda s: -sum(1 for c in s["checklist"] if c.get("pass")))
    items = [f"{s['symbol']} {passed(s)}" for s in ranked[:MAX_WATCH_ITEMS]]
    more = f" +{len(ranked) - MAX_WATCH_ITEMS}" if len(ranked) > MAX_WATCH_ITEMS else ""
    return f"{EMO['watch']} ใกล้ครบ: " + " · ".join(items) + more


def build_payload(p: dict) -> dict | None:
    """คืน None ถ้าไม่มีอะไรควรส่ง — 1 ข้อความ 1 embed เท่านั้น"""
    sigs = p.get("signals", [])
    # v0.6: "blocked" = เข้าเงื่อนไขครบแต่กฎ risk ห้ามเปิด — ต้องบอก ไม่ใช่เงียบ
    actionable = [s for s in sigs if s["kind"] in ("entry", "exit", "blocked")]
    halts = p.get("halts", [])
    errors = p.get("errors", [])

    if not (actionable or halts or errors):
        return None

    n_e = sum(1 for s in actionable if s["kind"] == "entry")
    n_x = sum(1 for s in actionable if s["kind"] == "exit")
    n_b = sum(1 for s in actionable if s["kind"] == "blocked")
    head = (f"**Signal Brief {p.get('run_date','')}** — เข้า {n_e} · ออก {n_x}"
            + (f" · บล็อก {n_b}" if n_b else ""))
    if halts:
        head = "🛑 " + head

    lines = [f"🛑 **หยุดเทรด** · {_clip(str(h), 200)}" for h in halts]
    # ออกก่อนเข้า — รักษาเงินต้นสำคัญกว่าหาไม้ใหม่
    order = {"exit": 0, "entry": 1, "blocked": 2}
    lines += [signal_line(s) for s in sorted(actionable, key=lambda x: order.get(x["kind"], 3))]

    watch = [s for s in sigs if s["kind"] == "watch" and s.get("checklist")]
    if watch:
        lines.append(watch_line(watch))
    if errors:
        lines.append("⚠ ดึงข้อมูลไม่ได้: "
                     + _clip(", ".join(str(e.get("symbol", "?")) for e in errors), 180))

    if halts or n_x:
        color = COLOR["exit"]
    elif n_e:
        color = COLOR["entry"]
    elif n_b:
        color = COLOR["blocked"]
    else:
        color = COLOR["watch"]

    spec = str(p.get("spec_version", "")).replace("cngoal-", "v")
    bar = next((s.get("bar_date") for s in actionable if s.get("bar_date")), "")
    foot = " · ".join(x for x in (f"CNgoal {spec}" if spec else "",
                                  f"แท่ง {bar}" if bar else "",
                                  "รายละเอียดเต็มใน brief.html") if x)

    desc = "\n".join(lines)
    while len(desc) > MAX_DESC and len(lines) > 1:
        lines.pop()
        desc = "\n".join(lines) + "\n…"
    embed = {"description": _clip(desc, MAX_DESC), "color": color,
             "footer": {"text": _clip(foot, 2048)}}
    # Discord นับตัวอักษรรวมทุก embed ต้องไม่เกิน 6000 — ตัดบรรทัดท้ายถ้าเกิน
    while len(json.dumps([embed], ensure_ascii=False)) > MAX_TOTAL and len(lines) > 1:
        lines.pop()
        embed["description"] = "\n".join(lines) + "\n…"

    return {"content": _clip(head, 2000), "embeds": [embed],
            "allowed_mentions": {"parse": []},
            "username": "CNgoal Signals"}


def send(webhook: str, payload: dict, timeout: int = 20) -> int:
    req = urllib.request.Request(
        webhook, method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "cngoal-agent/0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


GROUP_LABEL = {"crypto": "Crypto", "gold": "Gold", "stock": "US Stock"}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, choices=sorted(GROUP_LABEL))
    ap.add_argument("--dry-run", action="store_true")
    args, _ = ap.parse_known_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    f = root / "data" / f"signals_{args.group}.json"
    if not f.exists():
        print(f"{f} ไม่มี — engine.run ยังไม่เคยรัน group นี้สำเร็จ ข้ามการแจ้งเตือน")
        return 0
    p = json.loads(f.read_text())
    label = GROUP_LABEL[args.group]

    import yaml
    cfg = yaml.safe_load((root / "watchlist.yml").read_text()).get("notify", {}) or {}
    payload = build_payload(p)

    if payload is None:
        if not cfg.get("on_empty", False):
            print("no actionable signals — ไม่ส่ง (ตั้ง notify.on_empty: true ถ้าอยากได้ทุกวัน)")
            return 0
        payload = {"content": f"**[{label}] {p.get('run_date','')}** — ไม่มีสัญญาณเข้าเกณฑ์วันนี้",
                   "allowed_mentions": {"parse": []}, "username": "CNgoal Signals"}
    else:
        payload["content"] = f"[{label}] " + payload["content"]

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    hook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    if not hook:
        print("DISCORD_WEBHOOK ไม่ได้ตั้งไว้ — ข้ามการแจ้งเตือน (ไม่ถือว่าพัง)")
        return 0
    try:
        code = send(hook, payload)
        print(f"discord ok: {code}, {len(payload.get('embeds', []))} embeds")
    except urllib.error.HTTPError as e:
        # แจ้งเตือนพังต้องไม่ทำให้ workflow แดงจนบดบังว่าการสแกนสำเร็จ
        print(f"discord failed: HTTP {e.code} {e.read()[:200]!r}")
    except Exception as e:
        print(f"discord failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
