# DevTools Radar Local - Agent Instructions

## Project Goal

This project wraps ChatGPT Web UI as a local OpenAI-compatible API and adds OpenCode / ADOS runtime observability.

The current engineering goal is:

```text
Strengthen OpenCode coding behavior while making each runtime step visible in the Agent Console.
```

This project is developed and tested inside a Windows VM.

## Current Project Path

```text
C:\project\devtools-radar-local
```

## Local Services

API:

```text
http://127.0.0.1:8788
```

Agent Console UI:

```text
http://127.0.0.1:5177
```

## Important Files

Backend:

```text
api_server.py
runtime_event_log.py
runtime_event_summarizer.py
runtime_lifecycle_summarizer.py
opencode_ados_trace.py
run_api.bat
```

Frontend:

```text
app-agent-console/src/main.js
app-agent-console/src/styles.css
app-agent-console/vite.config.js
```

ADOS / OpenCode:

```text
.opencode/ADOS.md
.opencode/agents/*.md
.opencode/skills/*/SKILL.md
.opencode/plans/
```

Project rules and outcomes:

```text
AGENTS.md
docs/changes/**
docs/outcomes.md
```

## Current Phase Status

Completed:

```text
Phase 4.1 - ADOS agent templates
Phase 4.2 - ADOS / skill discovery runtime trace
Phase 4.3 - Selected ADOS agent template injection
Phase 4.4 - Heuristic OpenCode skill loading / injection
```

Current verified runtime events include:

```text
opencode_ados_assets_detected
opencode_ados_template_selected
opencode_agents_md_detected
opencode_skill_discovered
opencode_trace_ready
opencode_ados_template_loaded
opencode_ados_template_injected
opencode_skill_selection_completed
opencode_skill_loaded
opencode_skill_injected
```

Next planned work:

```text
Phase 4.5 - File read / edit / diff trace
Phase 4.6 - Command / test / verify trace
Phase 4.7 - Run summary
Phase 4.8 - ADOS workflow trace
Phase 4.9 - Agent / skill regression prompts
```

## Recommended Next Task

The next recommended task is:

```text
Phase 4.5 - Backend File / Diff Trace only
```

Start with backend trace events before modifying the UI.

Expected new backend events:

```text
opencode_changed_files_detected
opencode_diff_generated
```

Later UI work may display:

```text
opencode_file_read
opencode_file_edit_started
opencode_file_edit_finished
opencode_changed_files_detected
opencode_diff_generated
```

## How To Run The API

Use PowerShell:

```powershell
cd C:\project\devtools-radar-local
.\run_api.bat
```

The API should listen on:

```text
http://127.0.0.1:8788
```

Useful checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8788/health
Invoke-RestMethod http://127.0.0.1:8788/v1/models
Invoke-RestMethod http://127.0.0.1:8788/v1/debug/runs
Invoke-RestMethod http://127.0.0.1:8788/v1/debug/events
```

## How To Run The UI

Use PowerShell:

```powershell
cd C:\project\devtools-radar-local\app-agent-console
npm run dev
```

Open:

```text
http://127.0.0.1:5177
```

## Validation Commands

Python syntax checks:

```powershell
python -m py_compile api_server.py
python -m py_compile opencode_ados_trace.py
python -m py_compile runtime_event_log.py
python -m py_compile runtime_event_summarizer.py
python -m py_compile runtime_lifecycle_summarizer.py
```

Frontend smoke check:

```powershell
cd app-agent-console
npm run dev
```

If a build script exists and is safe to run:

```powershell
cd app-agent-console
npm run build
```

## API Smoke Request

When sending Chinese or UTF-8 content from PowerShell, encode the request body as UTF-8 bytes.

Example:

```powershell
$BodyObj = @{
  model = "chatgpt-web-local"
  messages = @(
    @{
      role = "user"
      content = "Use ados-builder and answer only ok"
    }
  )
  stream = $false
}

$Json = $BodyObj | ConvertTo-Json -Depth 20
$Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)

Invoke-RestMethod `
  -Uri http://127.0.0.1:8788/v1/chat/completions `
  -Method POST `
  -ContentType "application/json; charset=utf-8" `
  -Body $Bytes
```

## ADOS Concept

ADOS means:

```text
Agent Development Operating Structure
```

In this project, ADOS is an OpenCode role and markdown governance layer.

ADOS is used to:

```text
1. Define stable OpenCode agent role templates.
2. Prevent random markdown files from being created across the repository.
3. Separate planning, exploration, implementation, verification, and review roles.
4. Make OpenCode runtime behavior observable in the Agent Console.
```

## ADOS Agents

Project-level ADOS agents live under:

```text
.opencode/agents/
```

Current ADOS agents:

```text
ados-planner
ados-explorer
ados-builder
ados-verifier
ados-reviewer
```

Role responsibilities:

```text
ados-planner
- Plans implementation.
- Should not edit source code.
- Should not run shell commands.

ados-explorer
- Inspects repository structure and source files.
- Read-only.
- Should not edit files.

ados-builder
- Implements focused code changes.
- May edit files.
- Should keep changes small and reviewable.

ados-verifier
- Runs or recommends validation commands.
- Should not edit files.

ados-reviewer
- Reviews diffs and risks.
- Should not edit files.
```

## ADOS Skills

Project-level OpenCode skills live under:

```text
.opencode/skills/<skill-name>/SKILL.md
```

Current skills:

```text
api-debug
git-workflow
testing
ui-change
```

Skill responsibilities:

```text
api-debug
- Use when working on FastAPI, runtime events, MCP/native tool loop, debug endpoints, or API behavior.

git-workflow
- Use when inspecting git status, diffs, commits, branches, or repository hygiene.

testing
- Use when selecting validation commands, tests, lint, type checks, or smoke checks.

ui-change
- Use when modifying the Agent Console UI, timeline, inspector, frontend API calls, or styles.
```

## Current Skill Behavior

Current implementation uses heuristic skill loading / injection.

This means:

```text
1. The wrapper scans the user prompt.
2. It selects matching skills using deterministic rules.
3. It loads matching SKILL.md files.
4. It injects selected skill instructions into the prompt.
5. It emits runtime trace events.
```

This is not yet model-driven skill selection.

Future work may add:

```text
opencode_skill_candidates_presented
opencode_skill_load_requested
opencode_skill_loaded_by_model
```

## Markdown Governance

Do not create random markdown files.

Allowed markdown locations:

```text
AGENTS.md
.opencode/ADOS.md
.opencode/agents/*.md
.opencode/plans/*.md
.opencode/skills/*/SKILL.md
docs/changes/**
docs/outcomes.md
```

Plans must go under:

```text
.opencode/plans/
```

Use this naming pattern for persistent plans:

```text
.opencode/plans/YYYYMMDD-HHMM-task-slug.md
```

Do not create files such as:

```text
PLAN.md
TODO.md
NOTES.md
SUMMARY.md
IMPLEMENTATION.md
REPORT.md
handoff.md
```

unless the user explicitly requests them.

## Coding Rules

Follow these rules when modifying code:

```text
1. Keep changes small and focused.
2. Preserve existing behavior unless the task requires changing it.
3. Prefer additive event types over event schema rewrites.
4. Preserve OpenAI-compatible API response shapes.
5. Preserve Windows compatibility.
6. Preserve UTF-8 Chinese input handling.
7. Avoid unrelated refactors.
8. Avoid large rewrites of api_server.py unless explicitly requested.
9. Do not modify unrelated docs.
10. Do not change API or UI ports unless explicitly requested.
```

## Runtime Event Rules

When adding observability:

```text
1. Add new event types rather than rewriting old ones.
2. Keep event payloads JSON-serializable.
3. Include a concise title and preview.
4. Include enough payload detail for the Agent Console Inspector.
5. Do not break existing /v1/debug/events behavior.
6. Do not break run history.
```

A good event should usually include:

```text
source
event_type
title
preview
payload
status
duration_ms when applicable
```

## UI Rules

When modifying Agent Console UI:

```text
1. Keep the Discord-like dark mode UI style.
2. Preserve timeline and Inspector behavior.
3. Preserve /api-local proxy behavior.
4. Preserve existing filters and run history.
5. Prefer small display improvements over redesign.
6. Avoid new heavy dependencies unless explicitly requested.
```

Important UI files:

```text
app-agent-console/src/main.js
app-agent-console/src/styles.css
```

## Git Rules

Do not run:

```text
git push
git reset --hard
git clean -fd
git rebase
git checkout -- .
```

unless the user explicitly requests it.

Before suggesting commits, inspect:

```powershell
git status
git diff --name-only
git diff
```

Prefer small commits.

Suggested commit message style:

```text
feat: add OpenCode file diff trace
fix: repair ADOS skill event payload
docs: update Codex handoff
```

## Recommended Codex Workflow

When using Codex or another coding agent:

```text
1. Inspect relevant files first.
2. Identify insertion points.
3. Make small changes.
4. Run syntax checks.
5. Run a smoke test if safe.
6. Report changed files, validation, and risks.
```

Do not ask Codex to complete an entire large phase in one step.

Prefer splitting work into:

```text
Task A - backend trace only
Task B - UI display only
Task C - end-to-end smoke
Task D - handoff summary
```

## Output Format For Coding Tasks

At the end of a coding task, report:

```text
Files changed
Key changes
Validation run
Validation result
Remaining risks
```

## Phase 4.5 Suggested Scope

For the first Codex handoff task, use this scope:

```text
Task:
Implement Phase 4.5 backend file / diff trace only.

Allowed files:
- api_server.py
- runtime_event_summarizer.py

Do not modify:
- UI files
- ADOS agent files
- ADOS skill files
- unrelated docs

Expected events:
- opencode_changed_files_detected
- opencode_diff_generated

Payload should include:
- changed_files
- additions
- deletions
- diff_preview
- duration_ms if available
```

Validation:

```powershell
python -m py_compile api_server.py
python -m py_compile runtime_event_summarizer.py
```

Then send a smoke request and verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8788/v1/debug/events
```

## Phase 4.6 Suggested Scope

After Phase 4.5, implement command / test / verify trace.

Expected events:

```text
opencode_command_started
opencode_command_finished
opencode_test_started
opencode_test_finished
opencode_validation_summary
```

The UI should eventually show:

```text
command
exit_code
stdout preview
stderr preview
duration_ms
result
```

## Phase 4.7 Suggested Scope

After file / diff trace and command trace, implement run summary.

Expected event:

```text
opencode_run_summary_generated
```

Summary should include:

```text
selected_agent
loaded_skills
files_read
files_changed
commands_run
validation_result
tool_calls_count
duration_ms
status
```

## Current Development Priority

The current priority is not permission enforcement.

This project runs inside a Windows VM, so permission governance is intentionally delayed.

Current priority:

```text
1. Make OpenCode coding behavior stronger.
2. Make runtime behavior observable.
3. Make UI show what OpenCode actually did.
4. Keep each phase small and verifiable.
```

## Do Not Prioritize Yet

Delay these topics unless explicitly requested:

```text
permission enforcement
tool approval
dangerous command blocking
model-driven skill selection
large repo map
automatic workflow orchestration
full UI redesign
```

## Final Reminder

This project is currently about strengthening OpenCode runtime behavior and observability.

Before making changes, always ask:

```text
Can this change help us see or improve what OpenCode does during a coding task?
```

If not, it is probably out of scope for the current phase.
