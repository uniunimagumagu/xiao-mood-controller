# XIAO Mood Controller: one-shot bootstrap (Windows, PowerShell)
# Usage:  powershell -ExecutionPolicy Bypass -File tools\setup_env.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> creating venv (.venv)"
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --quiet -U pip
& ".\.venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

Write-Host "==> firmware bootstrap (board package + libraries + mbed patch + compile)"
& ".\.venv\Scripts\python.exe" tools\build_all.py --skip-deps

Write-Host "==> copy .env example"
if (-not (Test-Path ".env")) { Copy-Item "config\env.example" ".env" }

@'
Done.
Next steps:
  .venv\Scripts\python tools\build_all.py --upload      # flash if the device is plugged
  .venv\Scripts\python tools\supervisor.py              # run the daemon (restarts on crash)
'@ | Write-Host
