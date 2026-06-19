# ADOS for OpenCode

ADOS = Agent Development Operating Structure.

This project uses ADOS to control how OpenCode roles, plans, skills, and markdown files are created.

## Goals

1. Keep OpenCode role behavior stable.
2. Prevent random `.md` files from being created across the repo.
3. Separate planning, exploration, building, review, and verification.
4. Make runtime behavior observable in the Agent Console later.

## Allowed Markdown Locations

OpenCode agents may only create or modify markdown files in these locations unless the user explicitly asks otherwise:

```text
AGENTS.md
.opencode/ADOS.md
.opencode/agents/*.md
.opencode/plans/*.md
.opencode/skills/*/SKILL.md
docs/changes/**
docs/outcomes.md
```

## Forbidden Markdown Behavior

Do not randomly create markdown files such as:

```text
PLAN.md
TODO.md
NOTES.md
SUMMARY.md
IMPLEMENTATION.md
REPORT.md
handoff.md
```

If a plan is needed, create it under:

```text
.opencode/plans/
```

Use this naming pattern:

```text
.opencode/plans/YYYYMMDD-HHMM-task-slug.md
```

## ADOS Roles

Use these role templates:

| Role | Purpose |
|---|---|
| ados-planner | Create or refine implementation plans without editing source code |
| ados-explorer | Inspect repo structure and source files without editing |
| ados-builder | Implement code changes |
| ados-reviewer | Review diffs and identify risks without editing |
| ados-verifier | Run checks, tests, lint, and validation commands |

## Default Flow

For non-trivial coding tasks, prefer:

```text
ados-planner
  -> ados-explorer
  -> ados-builder
  -> ados-verifier
  -> ados-reviewer
```

For tiny tasks, `ados-builder` may act directly.

## Runtime Trace Expectations

When this system is later wired into runtime events, each OpenCode run should expose:

```text
ados_template_selected
opencode_agent_selected
opencode_agents_md_loaded
opencode_skill_loaded
opencode_file_read
opencode_file_edit
opencode_diff_generated
opencode_command_started
opencode_command_finished
opencode_final_response
```

## Current Project Notes

Project root:

```text
C:\project\devtools-radar-local
```

Local API:

```text
http://127.0.0.1:8788
```

Agent Console:

```text
http://127.0.0.1:5177
```
