# /smoke-test

Use this command to run or describe the project smoke tests.

## Current Smoke Tests

1. API health check
2. `/v1/models`
3. `/v1/chat/completions` with `stream=false`
4. `/v1/chat/completions` with `stream=true`
5. OpenCode model call
6. Filesystem MCP read/write
7. Git MCP status/log/diff
8. Approval UI pending/approve/deny
9. Audit log inspection

## Required Output

- Tests run
- Passed tests
- Failed tests
- Relevant logs
- Next fix