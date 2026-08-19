$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
& ".\.venv\Scripts\pip.exe" install pyinstaller

& ".\.venv\Scripts\pyinstaller.exe" `
  --noconfirm `
  --clean `
  --windowed `
  --name "BookCaptureAI" `
  --collect-all pygetwindow `
  app_v6.py

Write-Host ""
Write-Host "Build complete: dist\BookCaptureAI\"
