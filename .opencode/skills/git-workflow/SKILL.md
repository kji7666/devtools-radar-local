---
name: git-workflow
description: Use when inspecting git status, reviewing diffs, preparing commits, or checking whether changes are safe to commit.
---

# Git Workflow Skill

Use this skill when the task involves git state, changed files, diffs, commits, or handoff safety.

## Rules

- Always inspect current git state before suggesting commit commands.
- Prefer small commits.
- Do not rewrite history unless explicitly asked.
- Do not run destructive commands such as reset, clean, checkout, or rebase unless explicitly requested.
- If there are unrelated user changes, do not overwrite them.

## Useful Commands

```powershell
git status
git diff --name-only
git diff
git log --oneline -5
```

## Output

Report:

```text
Git status
Changed files
Diff summary
Suggested commit message
Risks
```
