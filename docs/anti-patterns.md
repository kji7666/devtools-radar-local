# Anti-Patterns

Avoid these patterns.

## Current Anti-Patterns

- Do not let the orchestrator directly modify files.
- Do not introduce swarm or parallel workers before serial workflow is stable.
- Do not add many MCP tools at once.
- Do not run package installs automatically.
- Do not modify browser profiles or secrets.
- Do not rely on undocumented behavior without smoke tests.