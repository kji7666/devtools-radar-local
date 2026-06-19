---
name: ui-change
description: Use when modifying app-agent-console UI, event timeline, inspector, filters, buttons, or frontend API calls.
---

# UI Change Skill

Use this skill when working on the Agent Console UI.

## Project Area

Frontend path:

```text
app-agent-console/
```

Important files usually include:

```text
app-agent-console/src/main.js
app-agent-console/src/styles.css
app-agent-console/vite.config.js
```

## Rules

- Keep UI changes focused.
- Preserve existing API proxy behavior through `/api-local`.
- Preserve UTF-8 Chinese prompt handling.
- Do not introduce heavy dependencies unless explicitly requested.
- Avoid large UI redesigns unless the task is specifically about UI redesign.

## Output

Report:

```text
Files changed
UI behavior changed
API calls affected
Manual test steps
Risks
```
