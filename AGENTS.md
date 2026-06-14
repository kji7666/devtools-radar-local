# AGENTS.md

## Project Root

All work must stay inside:

C:\project\devtools-radar-local

Do not create, edit, or delete files outside this project root.

## Core Goal

This project builds a local controllable coding-agent system:

OpenCode
→ DevTools Radar Local API
→ ChatGPT Web Local Wrapper
→ MCP tools
→ Approval UI / Audit Log / Smoke Tests

## Safety Rules

- Do not run `git push`.
- Do not run destructive commands unless explicitly approved.
- Do not modify `.env`, `.git`, `node_modules`, `.venv`, `edge_debug_profile`, or `browser_profile`.
- Do not install packages unless the user explicitly approves.
- Do not modify unrelated files.
- Prefer small, reviewable changes.
- Always list changed files after editing.
- Always list tests or verification steps after editing.

## Agent Roles

### explorer

Read-only. Investigates project structure, files, logs, and current behavior. Must not edit files.

### planner

Planning-only. Converts user requests into implementation plans, risks, and test plans. Must not edit files.

### coder

Implementation agent. May edit files only when the requested task is clear. Must not commit or push.

### reviewer

Review-only. Reads git diff, identifies risks, checks whether implementation matches the request. Must not edit files.

## Standard Workflow

1. Explore current state.
2. Write a short plan.
3. Implement the smallest safe change.
4. Run smoke tests or provide manual verification steps.
5. Review diff.
6. Record outcome in `docs/outcomes.md` when appropriate.