"""แปลง signals.json -> HTML brief ที่อ่านบนมือถือได้"""
from __future__ import annotations

import json
import pathlib
import sys

KIND = {
    "exit":  ("#b91c1c", "#fee2e2", "ปิดไม้"),
    "entry": ("#0f766e", "#ccfbf1", "เข้าไม้"),
    "watch": ("#6b6b68", "#f2f1ee", "เฝ้าดู"),
}

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a19;--mut:#6b6b68;--line:#e6e5e1;--card:#fff;--chip:#f2f1ee}
@media(prefers-color-scheme:dark){:root{--bg:#191918;--fg:#f0efec;--mut:#a1a09b;
 --line:#33322e;--card:#222220;--chip:#2b2a27}}
*{box-sizing:border-box}
body{margin:0;padding:20px;background:var(--bg);color:var(--fg);max-width:780px;margin-inline:auto;
 font:15px/1.65 ui-sans-serif,-apple-system,"Segoe UI",system-ui,"Noto Sans Thai",sans-serif}
h1{font-size:19px;margin:0 0 2px}h2{font-size:14px;margin:26px 0 8px;color:var(--mut);
 text-transform:uppercase;letter-spacing:.04em;font-weight:600}
.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.sym{font-weight:650;font-size:17px}
.tag{font-size:11px;font-weight:650;padding:2px 9px;border-radius:999px;letter-spacing:.03em}
.px{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--mut);font-size:13px}
ul{margin:6px 0;padding-left:18px}li{margin:2px 0}
.ck{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.ck span{font-size:11.5px;padding:2px 8px;border-radius:6px;background:var(--chip);color:var(--mut)}
.ck span.y{background:#ccfbf1;color:#0f766e}.ck span.n{background:#fee2e2;color:#b91c1c}
.lv{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;
 margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}
.lv div{font-size:12px}.lv b{display:block;font-size:14px;font-variant-numeric:tabular-nums}
.lv span{color:var(--mut)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child,th:nth-child(6),td:nth-child(6){text-align:left}
th{color:var(--mut);font-weight:500;font-size:11.5px}
td{font-variant-numeric:tabular-nums}
.bull{color:#0f766e}.bear{color:#b91c1c}.chop{color:var(--mut)}
.halt{background:#fee2e2;color:#991b1b;border-radius:10px;padding:12px 16px;margin-bottom:14px;font-weight:600}
@media(prefers-color-scheme:dark){.halt{background:#3b1518;color:#fca5a5}}
.foot{color:var(--mut);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:12px}
"""


def _sig_card(s: dict) -> str:
    fg, bg, label = KIND.get(s["kind"], ("#555", "#eee", s["kind"]))
    lv = s.get("levels") or {}
    ck = ""
    if s.get("checklist"):
        ck = "<div class=ck>" + "".join(
            f"<span class={'y' if c['pass'] else 'n'}>{c['n']}. {c['name']}</span>"
            for c in s["checklist"]) + "</div>"

    cells = []
    if s["kind"] == "exit":
        cells = [("เข้าที่", f"{lv.get('entry', 0):,.4g}"),
                 ("ออกที่", f"{lv.get('exit', 0):,.4g}"),
                 ("กำไร/ขาดทุนสุทธิ", f"{lv.get('pnl_pct_net', 0):+.2f}%")]
    elif lv.get("entry"):
        cells = [("Entry", f"{lv['entry']:,.4g}"), ("Stop", f"{lv['stop']:,.4g}"),
                 ("ระยะ SL", f"{lv['sl_distance_pct']:.2f}%")]
        if "position_size" in lv:
            cells += [("Position", f"{lv['position_size']:,.2f}"),
                      ("เสี่ยง", f"{lv.get('risk_amount', 0):,.2f}")]
        else:
            cells += [("% ของพอร์ต", f"{lv.get('position_pct_of_equity', 0):.1f}%")]

    grid = ""
    if cells:
        grid = "<div class=lv>" + "".join(
            f"<div><span>{k}</span><b>{v}</b></div>" for k, v in cells) + "</div>"

    dirn = {"long": " · LONG", "short": " · SHORT"}.get(s["direction"], "")
    return f"""<div class=card>
 <div class=hd><span class=sym>{s['symbol']}</span>
  <span class=tag style="color:{fg};background:{bg}">{label}{dirn}</span>
  <span class=px>{s['price']:,.4g}</span></div>
 <ul>{''.join(f'<li>{r}</li>' for r in s['reasons'])}</ul>
 {ck}{grid}
</div>"""


def render(p: dict) -> str:
    sigs = p.get("signals", [])
    by = lambda k: [s for s in sigs if s["kind"] == k]
    out = ["<!doctype html><meta charset=utf-8><title>Signal Brief</title>",
           "<meta name=viewport content='width=device-width,initial-scale=1'>",
           f"<style>{CSS}</style>",
           f"<h1>Signal Brief — {p.get('run_date','')}</h1>",
           f"<div class=sub>{p.get('spec_version','')} · สแกน {p.get('universe_size',0)} สินทรัพย์ · "
           f"เข้า {len(by('entry'))} · ออก {len(by('exit'))} · เฝ้าดู {len(by('watch'))} · "
           f"{p.get('generated_at','')}</div>"]

    for h in p.get("halts", []):
        out.append(f"<div class=halt>⚠ {h}</div>")

    for kind, title in [("exit", "ต้องปิดไม้"), ("entry", "สัญญาณเข้า"), ("watch", "เฝ้าดู")]:
        items = by(kind)
        if items:
            out.append(f"<h2>{title}</h2>" + "".join(_sig_card(s) for s in items))

    if not sigs:
        out.append("<div class=card>ไม่มีอะไรต้องทำวันนี้ — ไม่มีสินทรัพย์ตัวไหนเข้าเงื่อนไขครบ 4 ข้อ</div>")

    if p.get("open_positions"):
        rows = "".join(f"<tr><td>{x['symbol']}</td><td>{x['side']}</td>"
                       f"<td>{x['entry']:,.4g}</td><td>{x['stop']:,.4g}</td>"
                       f"<td>{x['opened']}</td><td></td></tr>" for x in p["open_positions"])
        out.append("<h2>ไม้ที่เปิดอยู่</h2><table><tr><th>Asset</th><th>ฝั่ง</th>"
                   f"<th>Entry</th><th>Stop</th><th>เปิดเมื่อ</th><th></th></tr>{rows}</table>")

    snap = p.get("snapshot", [])
    if snap:
        def row(x):
            n = lambda v, f: "—" if v is None else format(v, f)
            return (f"<tr><td>{x['symbol']}</td><td>{x['close']:,.4g}</td>"
                    f"<td>{n(x['chg_5d_pct'],'+.1f')}%</td><td>{n(x['rsi14'],'.0f')}</td>"
                    f"<td>{'↑' if x['above_ema20'] else '↓'}</td>"
                    f"<td class={x['regime']}>{x['regime']}</td></tr>")
        out.append("<h2>ภาพรวม watchlist</h2><table><tr><th>Asset</th><th>ราคา</th>"
                   "<th>5 วัน</th><th>RSI</th><th>vs EMA20</th><th>EMA200</th></tr>"
                   + "".join(row(x) for x in snap) + "</table>")

    if p.get("errors"):
        out.append("<div class=foot>⚠ ดึงข้อมูลไม่สำเร็จ: "
                   + ", ".join(f"{e['symbol']}" for e in p["errors"]) + "</div>")

    out.append("<div class=foot>คำนวณตาม CNgoal v5.1 Machine Spec · risk 1%/ไม้ · "
               "ตัดสินที่แท่ง Daily ที่ปิดแล้วเท่านั้น<br>"
               "เอกสารนี้ประกอบการตัดสินใจ ไม่ใช่คำแนะนำทางการเงิน</div>")
    return "\n".join(out)


if __name__ == "__main__":
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/signals.json")
    dst = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "data/brief.html")
    dst.write_text(render(json.loads(src.read_text())), encoding="utf-8")
    print(f"wrote {dst}")
