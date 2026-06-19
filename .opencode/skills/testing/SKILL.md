---
name: testing
description: Use when validating code changes, choosing smoke checks, running tests, lint, or type checks.
---

# Testing Skill

Use this skill when the task requires validation.

## Rules

- Prefer the smallest meaningful validation command.
- Avoid long-running dev servers unless explicitly requested.
- Do not run install, migration, clean, deploy, or destructive commands without approval.
- If no automated test exists, perform a focused manual validation summary.

## Validation Priority

```text
1. Targeted test for changed area
2. Lint / type check for changed area
3. Project smoke check
4. Manual reasoning if no safe command exists
```

## Output

Report:

```text
Command
Exit code
Important output
Result
Remaining risk
```
