# Troubleshooting

## OpenCode does not use local model

Check:

& "$env:APPDATA\npm\opencode.cmd" models devtools-radar --print-logs --log-level DEBUG

Expected:

devtools-radar/chatgpt-web-local

## OpenCode stream error

If error says:

stream=true is not supported

Then the API pseudo-streaming layer is broken or not running.

## API health

Invoke-RestMethod http://127.0.0.1:8788/health
Invoke-RestMethod http://127.0.0.1:8788/v1/models

## Wrong project path

Use only:

C:\project\devtools-radar-local