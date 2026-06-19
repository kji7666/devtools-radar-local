---
name: api-debug
description: Use when debugging FastAPI, OpenAI-compatible endpoints, runtime events, runner lock, MCP/native tool loop, or logs.
---

# API Debug Skill

Use this skill when working on backend API or runtime event logging.

## Project Area

Common backend files:

```text
api_server.py
runtime_event_log.py
run_api.bat
```

Known local API:

```text
http://127.0.0.1:8788
```

Important endpoints:

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
GET  /v1/debug/runs
GET  /v1/debug/events
GET  /v1/debug/runner
```

## Rules

- Preserve OpenAI-compatible response shape.
- Preserve Windows UTF-8 behavior.
- Preserve per-run event logging.
- Do not break existing MCP/native tool loop.
- Prefer additive event types over schema rewrites.

## Useful Checks

```powershell
Invoke-RestMethod http://127.0.0.1:8788/health
Invoke-RestMethod http://127.0.0.1:8788/v1/models
Invoke-RestMethod http://127.0.0.1:8788/v1/debug/runs
```

## Output

Report:

```text
Endpoint tested
Result
New event types
Compatibility risk
Next check
```
