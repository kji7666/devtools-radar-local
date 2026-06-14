# Smoke Tests

## 1. API Health

Invoke-RestMethod http://127.0.0.1:8788/health

## 2. Models

Invoke-RestMethod http://127.0.0.1:8788/v1/models

## 3. OpenCode Local Model

cd C:\project\devtools-radar-local

& "$env:APPDATA\npm\opencode.cmd" run "Reply exactly: OPENCODE_LOCAL_OK" `
  -m devtools-radar/chatgpt-web-local `
  --print-logs `
  --log-level DEBUG

Expected:

OPENCODE_LOCAL_OK

## 4. AGENTS.md Read Test

& "$env:APPDATA\npm\opencode.cmd" run "請閱讀 AGENTS.md，並只回答 AGENTS_OK" `
  -m devtools-radar/chatgpt-web-local `
  --print-logs `
  --log-level DEBUG

Expected:

AGENTS_OK

## 5. Git Inspection

git status
git diff --stat