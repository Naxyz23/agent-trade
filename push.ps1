<#
================================================================
  push.ps1 — ส่งโค้ดขึ้น GitHub (repo: Naxyz23/agent-trade)

  วิธีใช้ที่ง่ายที่สุด: คลิกขวาที่ไฟล์นี้ -> Run with PowerShell
    (ไม่ต้อง cd เองแล้ว สคริปต์หาโฟลเดอร์ตัวเองได้)

  ใส่ข้อความ commit เองก็ได้:
    .\push.ps1 -Message "แก้ bug ตรงนั้น"

  ปรับปรุง 18 ส.ค. 2026:
    - ทำงานได้โดยไม่ต้อง cd ก่อน (ใช้ $PSScriptRoot)
    - ข้อความ commit อ่านเวอร์ชันจาก engine/run.py ให้เอง
      (ของเดิม hardcode ว่า "v0.5" ทุกครั้ง ทำให้ประวัติ commit อ่านไม่รู้เรื่อง)
    - สรุปตอนจบว่า push ไปกี่ไฟล์ commit ไหน
================================================================
#>

param(
    [string]$Message = ""
)

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = "Stop"

function Check-Step {
    param([string]$Description)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ล้มเหลว: $Description (exit code $LASTEXITCODE)" -ForegroundColor Red
        Read-Host "กด Enter เพื่อปิด"
        exit 1
    }
}

# ---------- ย้ายตัวเองไปโฟลเดอร์ที่สคริปต์อยู่ ----------
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

Write-Host ""
Write-Host "=== ตรวจโฟลเดอร์ ===" -ForegroundColor Cyan
Write-Host "ทำงานที่: $root"

if (-not (Test-Path (Join-Path $root "engine\run.py"))) {
    Write-Host "ผิดที่ — ไม่พบ engine\run.py ในโฟลเดอร์นี้" -ForegroundColor Red
    Write-Host "ไฟล์ push.ps1 ต้องวางอยู่ในโฟลเดอร์ agent-trade" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

# ---------- อ่านเวอร์ชัน engine มาใส่ข้อความ commit ----------
if (-not $Message) {
    $ver = "?"
    $runPy = Get-Content (Join-Path $root "engine\run.py") -Raw -Encoding UTF8
    if ($runPy -match '"engine_version"\s*:\s*"([^"]+)"') { $ver = $Matches[1] }
    $Message = "engine v$ver — $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}
Write-Host "ข้อความ commit: $Message" -ForegroundColor DarkGray

Write-Host ""
Write-Host "=== git init ===" -ForegroundColor Cyan
if (Test-Path ".git") { Write-Host "เป็น git repo อยู่แล้ว ข้าม" }
else { git init ; Check-Step "git init" }

Write-Host ""
Write-Host "=== ตัวตน git (เฉพาะ repo นี้) ===" -ForegroundColor Cyan
if (-not (git config user.email)) { git config user.email "naxyz23@users.noreply.github.com" }
if (-not (git config user.name))  { git config user.name  "Naxyz23" }
Write-Host ("{0} <{1}>" -f (git config user.name), (git config user.email))

Write-Host ""
Write-Host "=== ไฟล์ที่เปลี่ยน ===" -ForegroundColor Cyan
git add -A
Check-Step "git add"
$changes = git status --porcelain
if ($changes) {
    $changes | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host ("รวม {0} ไฟล์" -f ($changes | Measure-Object).Count) -ForegroundColor Gray
} else {
    Write-Host "  ไม่มีอะไรเปลี่ยน" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== git commit ===" -ForegroundColor Cyan
if ($changes) {
    git commit -m $Message
    Check-Step "git commit"
} else {
    Write-Host "ไม่มีอะไรให้ commit (อาจ commit ไปแล้วรอบก่อน)"
}

Write-Host ""
Write-Host "=== ตั้งชื่อ branch ===" -ForegroundColor Cyan
git branch -M main
Check-Step "git branch -M main"

Write-Host ""
Write-Host "=== ตั้ง remote ===" -ForegroundColor Cyan
if ((git remote) -contains "origin") { Write-Host "origin ตั้งไว้แล้ว ใช้ของเดิม" }
else { git remote add origin https://github.com/Naxyz23/agent-trade.git ; Check-Step "git remote add" }

Write-Host ""
Write-Host "=== ดึงของใหม่จาก GitHub มารวมก่อน ===" -ForegroundColor Cyan
Write-Host "(เผื่อมีการแก้ไฟล์บนหน้าเว็บ เช่น data/manual.json)" -ForegroundColor DarkGray
git fetch origin main
Check-Step "git fetch"
git pull --rebase --autostash origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ล้มเหลว: git pull --rebase (exit code $LASTEXITCODE)" -ForegroundColor Red
    Write-Host "มักแปลว่าบรรทัดเดียวกันถูกแก้ทั้งบนเครื่องและบน GitHub" -ForegroundColor Red
    Write-Host "อย่าเดา — หยุดตรงนี้แล้วบอก Claude ว่าเกิดอะไรขึ้น" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

Write-Host ""
Write-Host "=== push ขึ้น GitHub ===" -ForegroundColor Cyan
Write-Host "อาจมีหน้าต่างให้ล็อกอิน — ถ้าขึ้นมาให้ล็อกอินตามปกติ" -ForegroundColor DarkGray
git push -u origin main
Check-Step "git push"

Write-Host ""
Write-Host "=== เสร็จแล้ว push สำเร็จ ===" -ForegroundColor Green
$sha = (git rev-parse --short HEAD)
Write-Host "commit ล่าสุด: $sha  $Message" -ForegroundColor Green
Write-Host "ดูผลได้ที่ https://github.com/Naxyz23/agent-trade" -ForegroundColor Green
Write-Host ""
Write-Host "ขั้นต่อไป: ไปที่แท็บ Actions บน GitHub -> เลือก workflow ->" -ForegroundColor White
Write-Host "กด Run workflow เพื่อรันด้วยมือ 1 รอบ แล้วบอก Claude ให้เช็คผล" -ForegroundColor White
Write-Host ""
Read-Host "กด Enter เพื่อปิดหน้าต่างนี้"
