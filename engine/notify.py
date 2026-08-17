"""ส่งสัญญาณเข้า Discord ผ่าน webhook

ทำไมให้ GitHub Actions ส่งเอง ไม่ใช่ให้ Cowork ส่ง
------------------------------------------------
ถ้าให้ Cowork ส่ง แปลว่าการแจ้งเตือนขึ้นกับว่า Cowork รันตรงเวลาไหม
แต่ GitHub Actions รันของมันเองอยู่แล้ว ส่งตรงจากตรงนั้นเลยจึงเชื่อถือได้กว่า
Cowork ค่อยตามมาเสริมบริบท (earnings, ข่าว) ทีหลังในรอบของมัน

กันสแปม: v5.1 คาดว่า ~0.7-1 เทรด/เดือน ถ้าส่ง "วันนี้ไม่มีอะไร" วันละ 2 ครั้ง
จะได้ข้อความไร้สาระ 60 ข้อความ/เดือน แล้วจะเลิกอ่าน -> ค่าเริ่มต้นจึงส่งเฉพาะ
วันที่มี entry/exit/halt จริง (ตั้ง notify.on_empty: true ถ้าอยากได้ทุกวัน)
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


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt(v, nd=4) -> str:
    """เลขคริปโตกับเลขหุ้นต่างกันหลายหลัก ใช้ significant digits แทนทศนิยมตายตัว"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:,.{nd}g}"


def signal_embed(s: dict) -> dict:
    side = SIDE.get(s.get("direction", ""), "")
    title = f"{s['symbol']} · {s['kind'].upper()}" + (f" {side}" if side else "")
    desc = "\n".join(f"• {r}" for r in s.get("reasons", []))
    lv = s.get("levels") or {}
    fields = []

    if s["kind"] == "exit":
        fields = [
            {"name": "เข้าที่", "value": _fmt(lv.get("entry")), "inline": True},
            {"name": "ออกที่", "value": _fmt(lv.get("exit")), "inline": True},
            {"name": "สุทธิ", "value": f"{lv.get('pnl_pct_net', 0):+.2f}%", "inline": True},
        ]
    elif lv.get("entry"):
        fields = [
            {"name": "Entry", "value": _fmt(lv["entry"]), "inline": True},
            {"name": "Stop", "value": _fmt(lv["stop"]), "inline": True},
            {"name": "ระยะ SL", "value": f"{lv['sl_distance_pct']:.2f}%", "inline": True},
        ]
        if "position_size" in lv:
            cap = " ⚠ ชนเพดาน" if lv.get("capped_by_leverage") else ""
            fields += [
                {"name": "Position", "value": f"{lv['position_size']:,.2f}{cap}", "inline": True},
                {"name": "เสี่ยง", "value": f"{lv.get('risk_amount', 0):,.2f}", "inline": True},
                {"name": "Leverage", "value": f"x{lv.get('leverage_cap', 5):g}", "inline": True},
            ]

    if s.get("checklist"):
        ck = "  ".join(("✅" if c["pass"] else "❌") + f"{c['n']}" for c in s["checklist"])
        fields.append({"name": "เงื่อนไข 4 ข้อ", "value": _clip(ck, MAX_FIELD_VALUE),
                       "inline": False})

    for f in fields:
        f["value"] = _clip(str(f["value"]) or "—", MAX_FIELD_VALUE)

    return {
        "title": _clip(title, 256),
        "description": _clip(desc, MAX_DESC),
        "color": COLOR.get(s["kind"], 0x6B7280),
        "fields": fields[:MAX_FIELDS],
        "footer": {"text": f"{s.get('rule', '')} · แท่งวันที่ {s.get('bar_date', '')}"},
    }


def build_payload(p: dict) -> dict | None:
    """คืน None ถ้าไม่มีอะไรควรส่ง"""
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
            + (f" · ถูกบล็อก {n_b}" if n_b else ""))
    if halts:
        head = "🛑 " + head

    embeds = []
    for h in halts:
        embeds.append({"title": "หยุดเทรด", "description": _clip(h, MAX_DESC),
                       "color": COLOR["halt"]})
    # ออกก่อนเข้า — รักษาเงินต้นสำคัญกว่าหาไม้ใหม่
    order = {"exit": 0, "entry": 1, "blocked": 2}
    for s in sorted(actionable, key=lambda x: order.get(x["kind"], 3)):
        embeds.append(signal_embed(s))
    if errors:
        embeds.append({
            "title": f"ดึงข้อมูลไม่สำเร็จ {len(errors)} ตัว",
            "description": _clip(", ".join(e["symbol"] for e in errors), MAX_DESC),
            "color": 0xB45309,
        })

    embeds = embeds[:MAX_EMBEDS]
    # Discord นับตัวอักษรรวมทุก embed ต้องไม่เกิน 6000 — ตัดจากท้ายถ้าเกิน
    while len(json.dumps(embeds, ensure_ascii=False)) > MAX_TOTAL and len(embeds) > 1:
        embeds.pop()

    return {"content": head, "embeds": embeds,
            "allowed_mentions": {"parse": []},
            "username": "CNgoal Signals"}


def send(webhook: str, payload: dict, timeout: int = 20) -> int:
    req = urllib.request.Request(
        webhook, method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "cngoal-agent/0.4"})
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
