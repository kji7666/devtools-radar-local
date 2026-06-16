# DevTools Radar Agent Console

Discord dark mode runtime observability UI scaffold for DevTools Radar Local, OpenCode, model messages, MCP tool calls, config editing, and diff-before-save UX.

## Current scope

- Mock UI only
- No real API connection
- No OpenCode execution
- No config writes
- No Electron packaging
- Does not modify existing app/

## Run

```powershell
cd C:\project\devtools-radar-local\app-agent-console
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5177
```

## Acceptance

- Workspace rail is visible
- Channel list is visible
- Runtime timeline shows mock API, OpenCode, model, MCP, config, and error events
- Inspector updates when an event is clicked
- Prompt, response, cookie, and API key examples are redacted
- Config editor mock shows diff preview before save
