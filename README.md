# Trading Signal Agent — v0.5

ระบบเฝ้าดู 21 สินทรัพย์ ตรวจเงื่อนไขตาม **CNgoal v5.1** ทุกวัน แล้วส่งสรุปมาให้ **คนตัดสินใจ**
ระบบนี้ **ไม่ส่งคำสั่งซื้อขาย** และไม่ได้ออกแบบให้ทำแบบนั้น

---

## v0.2 → v0.3 เปลี่ยนอะไร — และทำไม

v0.1 ผมเขียนกฎขึ้นเองจากหลักการทั่วไป พอได้อ่าน **CNgoal v5.1** ที่ผ่าน backtest
แบบหักค่าธรรมเนียมและแบ่ง in-sample/out-of-sample มาแล้ว พบว่า **กฎของผม 3 ข้อ
อยู่ในตาราง "ทดสอบแล้วไม่ผ่าน" ของ v5.1 พอดี**

| กฎเดิมใน v0.1 | v5.1 บอกว่า |
|---|---|
| `trend_pullback` (รอย่อ EMA20) | ผลตอบแทนต่ำกว่า และไม่ซ้ำข้ามสินทรัพย์ |
| `breakout_squeeze` (Donchian 20/55) | มีแค่ 5-10 ไม้ ตัวอย่างน้อยเกินกว่าจะสรุป |
| `_regime()` ใช้ "ราคา > EMA200" | แพ้ "EMA200 หันขึ้น" ทั้ง BTC ETH BNB |

**เมื่อมีหลักฐานขัดกัน ให้เชื่อหลักฐานที่ทดสอบมาแล้ว ไม่ใช่กฎที่ฟังดูดี**
v0.2 จึงยึด CNgoal v5.1 Machine Spec เป็นระบบหลัก และลดกฎเดิมเป็น `watch` (ข้อมูลประกอบเท่านั้น)

**v0.3 แก้ข้อผิดพลาดของผมเอง:** v0.2 ผมตั้งหุ้น US เป็น `long_only` + leverage x1
โดยเดาว่า Nana ซื้อ spot ซึ่ง **ผิด** — Nana เทรดผ่าน margin/CFD จึงชอร์ตได้
และ `cngoal_backtest.py` ก็รองรับ `dir = ±1` มาตั้งแต่ต้น แปลว่าฝั่ง short
เป็นส่วนหนึ่งของระบบที่ทดสอบมาแล้ว ไม่ใช่ของแถม
→ ตอนนี้ทุกสินทรัพย์เข้าได้ทั้ง long/short ที่ leverage x5 เท่ากัน

**v0.4 → v0.5 เรื่อง repo เป็น public/private:** ลองตั้ง private ไปรอบหนึ่งเพื่อความเป็นส่วนตัว
แต่ผลคือ Cowork อ่าน `signals.json` ไม่ได้เลย ต้องพึ่ง Discord อย่างเดียวซึ่งเห็นผลช้า
(v5.1 เทรดแค่ ~0.7-1 ครั้ง/เดือน กว่าจะรู้ว่าท่อทำงานจริงต้องรอสัญญาณแรก)
v0.5 จึงกลับมาเป็น **public** เพื่อให้ Cowork อ่านตรงได้ทุกรอบและยืนยันได้ทันทีว่าระบบทำงานจริง
ข้อแลกเปลี่ยนคือ `data/state.json` (ไม้ที่เปิดอยู่ + P&L) เปิดให้ทุกคนเห็นได้ — ไม่มีข้อมูลระบุตัวตน
แต่เป็นสถิติการเทรดที่เปิดเผย ถ้าไม่สบายใจ กลับไป private ได้ทุกเมื่อ (ดูรายละเอียดใน `SETUP.html`)

---

## การแจ้งเตือน

**Cowork scheduled task คือช่องทางหลัก** — อ่าน `signals.json` ผ่าน `raw.githubusercontent.com`
ตรง ๆ ทุกรอบที่ GitHub Actions สแกนเสร็จ แล้วสรุปเป็นภาษาคนส่งเข้าแชท
ข้อดีคือได้บริบทเพิ่ม (เช็ค earnings, ข่าว) ที่ตัวโค้ดเองไม่รู้

**Discord webhook เป็นช่องทางเสริม** (ไม่บังคับ) — GitHub Actions ส่งเอง ไม่ผ่าน Cowork
เหมาะถ้าอยากได้แจ้งเตือนทันทีที่มีสัญญาณโดยไม่ต้องรอเปิดแอป Cowork
ค่าเริ่มต้น **ส่งเฉพาะวันที่มี entry / exit / halt / error จริง** — เปลี่ยนได้ที่
`notify.on_empty` ใน `watchlist.yml`

ตั้งค่าทั้งหมดดูที่ **`SETUP.html`**

---

## ทำไม architecture ถึงเป็นแบบนี้

ทดสอบแล้วว่า Cowork sandbox ต่อเน็ตไปหา API ราคา **ไม่ได้** — proxy บล็อกทุก host
ที่ไม่ใช่ package registry:

| ปลายทาง | shell | WebFetch |
|---|---|---|
| Yahoo, Stooq, Binance, CoinGecko, Twelve Data | ❌ 403 | ❌ robots.txt |
| Kraken, Coinbase, Alpha Vantage, Finnhub | ❌ 403 | ✅ แต่ตัดข้อมูล |
| `raw.githubusercontent.com` | ✅ | ✅ |

WebFetch ยังไม่ใช่ท่อข้อมูล — ขอแท่งเทียน 60 แท่งได้กลับมาจริง 4 แท่ง
**ข้อสรุป:** ชั้นดึงข้อมูล + คำนวณ ต้องรันที่ GitHub Actions

```
GitHub Actions (cron)            GitHub repo           Cowork scheduled task
┌──────────────────────┐        ┌────────────┐        ┌────────────────────┐
│ fetch  ดึงราคา 3 ปี   │        │            │        │ อ่าน signals.json  │
│ indicators คำนวณ      │─push─▶ │signals.json│──GET──▶│ Claude เขียนอธิบาย │
│ rules  CNgoal v5.1    │        │ state.json │        │ ส่งแชท + Gmail     │
│ run    ติดตามไม้เปิด   │        └────────────┘        └────────────────────┘
└──────────────────────┘
   ตรรกะกำหนดได้ ทำซ้ำได้                                   ภาษาคน + บริบท
```

---

## กฎที่ใช้จริง — CNgoal v5.1

### Entry: 4 เงื่อนไข ต้องผ่านครบทุกข้อ

| # | Long | Short |
|---|------|-------|
| 1 | EMA200 วันนี้ > EMA200 เมื่อ 20 แท่งก่อน (**ทิศของเส้น** ไม่ใช่ตำแหน่งราคา) | กลับด้าน |
| 2 | ปิด Daily > EMA20 | ปิด < EMA20 |
| 3 | RSI(14) > 50 | RSI < 50 |
| 4 | pin bar หรือ engulfing ฝั่งขึ้น | ฝั่งลง |

ตัดสินที่ **แท่ง Daily ที่ปิดแล้วเท่านั้น** — `run.py` ตัดแท่งวันนี้ที่ยังวิ่งอยู่ทิ้งก่อนเสมอ

### Stop Loss / Position Size

```
SL long  = min(swing low 10 แท่ง, EMA50) ที่ใกล้ราคากว่า  แล้ว −0.1% buffer
ถ้า SL ห่าง < 0.5%  → ข้าม trade (ระบบออกเป็น watch ไม่ใช่ entry)

Risk     = พอร์ต × 1%
Position = Risk ÷ SL%
Position = min(Position, พอร์ต × leverage_cap)
```

`leverage_cap` = **5 ทุกสินทรัพย์** ตาม Machine Spec — หุ้น US เทรดผ่าน margin/CFD
จึงชอร์ตได้และใช้เพดานเดียวกัน ทุกตัวเข้าได้ทั้ง **long และ short** เป็นค่าเริ่มต้น
ถ้าตัวไหนโบรกไม่ให้ชอร์ต ใส่ `long_only: true` ในบรรทัดนั้นได้

### Exit — trail EMA20 เท่านั้น

ปิดเมื่อราคาปิด Daily สวน EMA20 **หรือ** ชน SL
ไม่มีเงื่อนไข "เฉพาะตอนกำไร" — ขาดทุนก็ปิด (v4.2 มีเงื่อนไขนี้ ทำให้ถือไม้แพ้นานกว่าไม้ชนะ)

### เพดานความเสี่ยง

- แพ้ติดกัน 3 ไม้ → ขึ้นแบนเนอร์แดง "พัก 3 วัน" บนหัว brief
- เปิดพร้อมกันได้ไม่เกิน 3 ไม้ (risk รวม 3%) — เกินแล้วสัญญาณใหม่จะถูกลดเป็น watch
- พอร์ตลดจาก peak 15% → หยุดทบทวนกฎ

---

## โครงสร้าง

```
watchlist.yml            21 สินทรัพย์ + ค่า risk  ← แก้ไฟล์นี้เป็นหลัก
engine/fetch.py          ดึงราคา (yahoo / kraken)
engine/indicators.py     EMA RSI MACD ATR BB Donchian + candle pattern
engine/rules.py          CNgoal v5.1 entry/exit + watch rules
engine/run.py            ประกอบร่าง + ติดตามไม้ที่เปิดอยู่ → data/signals.json
engine/render.py         signals.json → HTML brief
engine/notify.py         ส่งสัญญาณเข้า Discord webhook
cowork/SKILL.md          ฝั่ง Cowork ใช้อ่านผลแล้วเขียนสรุป
tests/test_engine.py     22 เทสต์
.github/workflows/       cron 2 รอบ/วัน
```

## รันเอง

```bash
pip install -r requirements.txt
python -m engine.run          # -> data/signals.json + state.json
python -m engine.render       # -> data/brief.html
python tests/test_engine.py   # 22/22 passed
python -m engine.notify --dry-run   # ดู payload ที่จะส่งเข้า Discord
```

## สิ่งที่ทดสอบแล้ว

| เทสต์ | ตรวจอะไร |
|---|---|
| RSI vs Wilder | คำนวณมือ + implementation ที่สองที่ไม่ใช้ pandas ตรงกันทุกจุด |
| candle patterns | pin bar / engulfing ตรงตามนิยามเชิงตัวเลขของ v5.1 (รวมเคสที่ต้องไม่ผ่าน) |
| EMA200 slope | ยืนยันว่าให้ผลต่างจาก "ราคา > EMA200" จริง |
| entry 4 ข้อ | ตรวจ 1,176 สัญญาณ ทุกตัวผ่านครบ 4 ข้อ และ SL อยู่ถูกฝั่งเสมอ |
| position size | risk 1% / SL 5% = 20 USDT · เคสชนเพดาน leverage x5 |
| exit | ปิดทั้งตอนกำไรและขาดทุน · หักค่าธรรมเนียมไป-กลับถูกต้อง |
| ทิศทาง | หุ้นออกสัญญาณ short ได้จริง (220 ครั้งในเทสต์) และ SL อยู่เหนือ entry เสมอ |
| `long_only` | ยังปิดฝั่ง short ได้ถ้าตั้ง flag — ไม่มี short หลุดออกมาเลย |
| Discord payload | เคารพลิมิตจริงของ API (10 embeds, 6000 ตัวอักษร) แม้ยิง 30 สัญญาณพร้อมกัน |
| กันสแปม | วันที่มีแค่ watch ต้องไม่ส่ง · exit ขึ้นก่อน entry เสมอ · errors ต้องไม่เงียบ |
| ความถี่ | 1.30 เทรด/เดือน บนข้อมูลสังเคราะห์ (v5.1 คาด 0.7-1 บนข้อมูลจริง) |

## ข้อจำกัดที่ต้องรู้

- **`files/cngoal_backtest.py` มี backtest engine พร้อมใช้อยู่แล้ว** — ใช้ตัวนั้นทำเฟส 3 ได้เลย ไม่ต้องเขียนใหม่
- **ยังไม่ได้ backtest บนข้อมูลจริง** — เทสต์ทั้งหมดตรวจว่า "โค้ดทำตามสเปกถูกไหม"
  ไม่ได้ตรวจว่า "สเปกทำเงินไหม" (อันหลัง cngoal v5.1 ทดสอบมาแล้วบางส่วน)
- v5.1 ระบุเองว่า **BTC เป็นตัวเดียวที่มีหลักฐานฝั่งบวก** — ETH/BNB/XAU ยังไม่มี
  และ **หุ้น US 13 ตัวในนี้ไม่เคยผ่าน backtest ของ v5.1 เลย** (มีแค่ NVDA)
- ความถี่ 1.30/เดือน วัดจาก random walk สังเคราะห์ ซึ่ง mean-revert มากกว่าตลาดจริง
- `yfinance` ไม่มีสัญญาบริการ อาจล่มได้ — มี retry + fallback (kraken) สำหรับ crypto/ทอง
- SPCX (SpaceX) IPO 12 มิ.ย. 2026 มีข้อมูลแค่ ~46 แท่ง ต้องรอครบ 221 แท่งราวเดือน เม.ย. 2027
