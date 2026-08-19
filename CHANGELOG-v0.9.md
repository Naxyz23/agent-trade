# v0.9 — ยกกฎขึ้น CNgoal v6.0 (19 ส.ค. 2026)

> **หมายเหตุเรื่องเลขเวอร์ชัน**: รอบแรกผมตั้งเป็น `v5.3` ซึ่งผิด Nana ท้วงว่า
> "เปลี่ยนเยอะขนาดนี้ต้องเปลี่ยนเลขหลัก" — ถูกต้อง เพราะสัญญาณที่ออกด้วย v5.2
> ไม่ผ่านเกณฑ์ของเวอร์ชันนี้ทั้งหมด = breaking change ตรงตัว จึงแก้เป็น **v6.0**
> และเขียนเกณฑ์การตั้งเวอร์ชันไว้ใน `engine/rules.py` เหนือ `SPEC_VERSION` แล้ว

ที่มา: การทดสอบข้อเสนอชุด `skill setup.txt` ของ Nana บน 16 สินทรัพย์ 54 ปี
รายงานเต็ม: `backtest-report-v3.html` · artifact `cngoal-v53-review`

**ผลรวม: expectancy +0.277R → +0.560R** CI95 [+0.337, +0.825] · ดีขึ้น 14/16 สินทรัพย์
ทนต่อ slippage 1.00% ได้ (v5.2 ติดลบ) · OOS 2015+ +0.659R

---

## 1. เงื่อนไขเข้าเพิ่ม 2 ข้อ (`engine/rules.py`, `engine/indicators.py`)

| ข้อ | เงื่อนไข | ใช้กับ | หลักฐาน |
|---|---|---|---|
| #2 | `EMA20 > EMA50` (กลับด้านสำหรับ short) | ทุก class | +0.182R → +0.207R · ดีขึ้น 12/15 · กำไรรวมไม่ลด |
| #6 | `ADX(14) ≥ 25` | **class `stock` เท่านั้น** | หุ้น +0.227R → +0.409R · พลิก 2000s จาก −0.029R เป็น +0.111R |

- checklist จาก 4 ข้อ → **5 ข้อ** (6 ข้อสำหรับหุ้น)
- `indicators.adx()` เขียนใหม่ด้วย Wilder smoothing เดิมของ repo (ไม่พึ่ง TA-lib)
- **ADX เป็น NaN = ไม่ผ่าน ไม่ใช่ผ่านฟรี** — ต่างจาก `_place_stop()` ที่ถอยไปใช้ chart ได้
  เพราะถ้าไม่รู้ค่า ADX ก็ไม่มีทางรู้ว่าตลาดมีเทรนด์จริงไหม

### ⛔ ห้ามใส่ ADX ให้ทอง/crypto — ทดสอบแล้วแย่ลง
- XAU: +0.115R → +0.049R (ที่เกณฑ์ 30 ติดลบ −0.228R)
- BTC: กำไรรวม 326R → 180R โดยกำไรต่อไม้ไม่เพิ่ม
- โครงสร้างเดียวกับ `stop_mode_by_class` ที่แยกตามสินทรัพย์ตั้งแต่ v0.8
- มีเทสต์ `test_watchlist_adx_only_for_stocks` กันคนเผลอใส่

### ทำไมเลือก 25
ทดสอบทั้งเส้น: ไม่ใช้ / 15 / 20 / 22 / **25** / 28 / 30 / 35
→ +0.227 / +0.238 / +0.315 / +0.334 / **+0.409** / +0.441 / +0.409 / +0.308
เป็นเนินเรียบ ไม่ใช่จุดแหลม = ผลจริง ไม่ใช่การ fit · 25 คือจุดที่กำไรต่อไม้เพิ่มมากโดยกำไรรวมยังไม่ลด

---

## 2. เพดานความเสี่ยงระดับพอร์ต (`engine/portfolio.py`, `watchlist.yml`)

### 🔴 บั๊กเชิงออกแบบที่เจอระหว่างทาง
`max_concurrent_positions: 1` เดิมนับ **ต่อ agent** → เพดานจริงคือ 3 ไม้ (3 agent × 1)
โดยไม่ได้ตั้งใจ และตั้งเป็นค่าอื่นให้สมเหตุสมผลไม่ได้เลย เพราะ agent มองไม่เห็นไม้ของกันและกัน
ทั้งที่ `equity` เป็นก้อนเดียวกัน — ความเสี่ยงบวกกันจริงแต่ไม่มีใครนับ

### แก้เป็น 2 ชั้น
```yaml
max_concurrent_positions: 5   # ต่อสาย
max_concurrent_portfolio: 8   # ★ รวมทุกสาย (นับข้าม agent ผ่าน portfolio.json)
```
- `portfolio.sync_group_positions(pf, group, n)` เขียนจำนวนไม้ของสายตัวเองลง `portfolio.json`
- `portfolio.portfolio_open_count(pf, exclude_group=...)` รวมของสายอื่น
- `entry_blockers(..., portfolio_max=, group=)` — ไม่ส่ง `portfolio_max` = พฤติกรรมเดิมทุกประการ

ตัวเลขของสายอื่นเก่าได้ถึง 1 วัน (workflow คนละเวลา) ซึ่งยอมรับได้ เพราะทิศทางความคลาดเคลื่อน
ปลอดภัย: ไม้ที่ปิดแล้วแต่ยังไม่ล้างทำให้ระบบ "ระวังเกินจริง" ไม่ใช่ "เสี่ยงเกินจริง"

### ทำไม 8
จำลอง equity 54 ปี (risk 1% ทบต้น + วัด maxDD) ด้วยกฎ v6.0:

| เพดาน | ไม้/เดือน | CAGR | maxDD |
|---|---|---|---|
| 4 | 1.6 | 8.7% | 28.0% |
| 6 | 1.9 | 10.2% | 28.8% |
| **8** | **2.0** | **10.7%** | **28.8%** |
| ไม่จำกัด | 2.1 | 11.2% | 28.8% |

maxDD ไม่ขยับเลยตั้งแต่ 5 ขึ้นไป เพราะพอมี ADX filter แล้วไม้พร้อมกันแทบไม่เคยเกิน 8
เพดานนี้เป็น **ราวกันตกกันเหตุการณ์หางที่ยังไม่เคยเกิดในข้อมูล** ไม่ใช่ตัวคุมผลตอบแทน

---

## 3. กฎกลุ่มสัมพันธ์ + ลำดับการเปิดไม้ (`engine/run.py`)

### 🔴 บั๊กเดิม: ใครอยู่ต้นไฟล์ watchlist ได้ slot ก่อน
v0.8 เปิดไม้ทันทีในลูป ทำให้ลำดับใน `watchlist.yml` เป็นตัวตัดสินว่าใครได้ slot สุดท้าย
ซึ่งไม่มีเหตุผลรองรับ และตัวที่วิ่งไปทางเดียวกัน (NVDA/AMD/TSM) จะเข้าพร้อมกันหมด
= risk 3% ก้อนเดียวที่ถูกบันทึกว่าเป็น 3 ก้อนที่กระจาย

### v0.9 เก็บสัญญาณเข้าทั้งหมดไว้ก่อน แล้วคัดทีเดียวหลังจบลูป
1. เรียงตาม **ADX สูงสุด** (แล้วค่อย score, symbol)
2. `corr_group` ซ้ำ → เอาแค่ตัวแรก (แรงสุด) ที่เหลือกลายเป็น `blocked` พร้อมเหตุผล
3. ค่อยเช็ค `entry_blockers` แล้วเปิด

tag ที่ใส่ให้หุ้น 13 ตัว: `us_semi` (NVDA/AMD/TSM) · `us_bigtech` (MSFT/AAPL/GOOG/META/ORCL/NFLX) ·
`us_growth` (TSLA) · `us_fin` (JPM) · `us_health` (LLY) · `us_staples` (WMT)
crypto/metal ใช้ค่า `class` เป็นกลุ่มโดยปริยาย

---

## 4. ผลลัพธ์ที่ออกมา
`signals_<group>.json` เพิ่ม:
```json
"engine_version": "0.9",
"spec_version": "cngoal-6.0",
"rules_config": {
  "trail_ema": 50, "stop_mode_default": "chart",
  "stop_mode_by_class": {"stock":"atr2","metal":"atr2","crypto":"chart"},
  "require_ema_stack": true,
  "adx_min_by_class": {"stock": 25},
  "max_concurrent_positions": 5,
  "max_concurrent_portfolio": 8
}
```
`evidence.adx14` มีในทุกสัญญาณ · `portfolio.json` เพิ่ม `open_by_group`

---

## 5. เทสต์ — 63 → **74 ข้อ** ผ่านทั้งหมด

ของใหม่ 10 ข้อ เขียนตามบทเรียนเดิมของ repo (ต้อง assert ว่าตัวกรอง "กรองจริง"):
- `test_adx_basic_properties` — ADX อยู่ในช่วง 0-100 · median ตลาดมีเทรนด์ > ตลาดออกข้าง + 5
- `test_ema_stack_actually_blocks_entries` — **นับเคสที่ต่างกันจริงแล้ว assert > 0**
- `test_adx_gate_actually_blocks_entries` — จำนวนไม้ต้องลดลงเป็นลำดับตามเกณฑ์ที่เข้มขึ้น
- `test_adx_nan_is_not_a_free_pass`
- `test_portfolio_wide_cap_blocks_across_agents`
- `test_correlated_group_keeps_only_strongest`
- `test_run_loop_opens_one_per_correlated_group` — **เรียก `run.main()` ตรง ๆ** ไม่ใช่แค่ logic
- `test_watchlist_adx_only_for_stocks` — กันคนใส่ ADX ให้ทอง/crypto
- `test_spec_version_and_defaults_are_current`
- `test_main_runner_collects_every_test` — 🔑 กันบั๊กที่เพิ่งเจอ: block `__main__` ต้องอยู่ท้ายไฟล์
  ไม่งั้นเทสต์ที่นิยามหลังจากนั้นจะถูกข้ามเงียบ ๆ ตอนรัน `python tests/test_engine.py`
  (pytest ยังเห็นครบ — เจอเพราะรันทั้งสองทาง)

### smoke test เดินเวลา 424 วันทำการ (ม.ค. 2025 – ส.ค. 2026) ด้วยข้อมูลจริง
เข้า 43 ไม้ · ออก 41 ไม้ · ไม้พร้อมกันสูงสุด 5 · บล็อกเพราะเพดาน 15 ครั้ง
**ADX ตอนเข้าไม้: ต่ำสุด 25.1** (ไม่มีไม้ไหนหลุด gate) · median 29.4 · สูงสุด 47.8
equity 100 → 101.58 (peak 107.53) · pause ทำงาน · ไม่มี error

---

## หมายเหตุความต่างของตัวเลข
`indicators.wilder_smooth()` ของ repo seed ด้วย SMA (ตรงกับ TradingView)
ส่วน harness ที่ใช้ backtest ใช้ `ewm(alpha=1/n)` ล้วน ค่าต่างกันเฉพาะช่วง warm-up
บนข้อมูล 6,000 แท่งจึงไม่มีผลต่อข้อสรุป แต่ตัวเลข ADX รายวันอาจต่างกันทศนิยมเล็กน้อย

## 6. เก็บกวาดเลขเวอร์ชันค้าง

`render.py` แสดง footer ว่า "คำนวณตาม CNgoal v5.1 Machine Spec" มาตลอด 3 เวอร์ชัน
เพราะ hardcode ไว้ · `notify.py` ยังบอกความถี่เก่า (~0.7-1 เทรด/เดือน) ·
`watchlist.yml` มี `risk.spec_version: "cngoal-5.1"` ค้างอยู่

แก้แล้วทั้งหมด และกันไม่ให้เกิดซ้ำ:
- `render.py` อ่าน `spec_version` จากไฟล์สัญญาณแทน hardcode
  (ใช้ค่าจากไฟล์ ไม่ import rules เพราะ render ต้องรันเดี่ยว ๆ ได้โดยไม่มี pandas)
- เทสต์ `test_no_stale_spec_version_strings_in_code` สแกนหาข้อความที่อ้างว่าเป็นสเปกปัจจุบัน
- เทสต์ตรวจว่า `watchlist.risk.spec_version` == `rules.SPEC_VERSION`

เทสต์รวมเป็น **74 ข้อ**

## ยังไม่ได้ทำ
- ETH/BNB/SOL/USOIL ยังไม่เคย backtest ด้วยกฎชุดนี้
- การจำลอง equity ยังไม่ได้ใส่กฎ halt (พักหลังแพ้ 3 ไม้ / หยุดที่ DD 15%)
- README.md / SETUP.html / design.html ยังเป็นเนื้อหา v0.5
