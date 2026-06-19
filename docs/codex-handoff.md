# Codex Handoff

## Current State

Completed:
- Phase 4.1 ADOS agent templates
- Phase 4.2 ADOS / skill discovery trace
- Phase 4.3 selected ADOS template injection
- Phase 4.4 heuristic skill loading / injection

Verified events:
- opencode_ados_assets_detected
- opencode_ados_template_selected
- opencode_agents_md_detected
- opencode_skill_discovered
- opencode_trace_ready
- opencode_ados_template_loaded
- opencode_ados_template_injected
- opencode_skill_selection_completed
- opencode_skill_loaded
- opencode_skill_injected

## Next Task

Phase 4.5 - Backend File / Diff Trace only.

## Expected New Events

- opencode_changed_files_detected
- opencode_diff_generated

## Allowed Files

- api_server.py
- runtime_event_summarizer.py

## Do Not Modify

- app-agent-console/*
- .opencode/agents/*
- .opencode/skills/*
- unrelated docs

## Validation

Run:

```powershell
python -m py_compile api_server.py
python -m py_compile runtime_event_summarizer.py
```
Then send one /v1/chat/completions request and check /v1/debug/events.


這份文件會讓 Codex 不需要讀整段聊天紀錄。

---

## 3. 保留並整理 `.opencode/` 結構

你目前已經有：

```text
.opencode/ADOS.md
.opencode/agents/ados-planner.md
.opencode/agents/ados-explorer.md
.opencode/agents/ados-builder.md
.opencode/agents/ados-reviewer.md
.opencode/agents/ados-verifier.md
.opencode/skills/api-debug/SKILL.md
.opencode/skills/git-workflow/SKILL.md
.opencode/skills/testing/SKILL.md
.opencode/skills/ui-change/SKILL.md
```