
import json
import re


TEXT_PREVIEW_LIMIT = 2000

TEST_COMMAND_PATTERNS = [
    ("pytest", "pytest"),
    ("python -m py_compile", "py_compile"),
    ("npm run build", "npm_build"),
    ("npm test", "npm_test"),
    ("npm run test", "npm_run_test"),
    ("npm run lint", "npm_lint"),
    ("npm run typecheck", "npm_typecheck"),
]

CONTEXT_FILE_PATTERNS = [
    r"AGENTS\.md",
    r"opencode\.jsonc",
    r"\.opencode/[A-Za-z0-9_\-./]+\.md",
    r"\.opencode\\[A-Za-z0-9_\-\\.]+\.md",
    r"docs/[A-Za-z0-9_\-./]+\.md",
    r"docs\\[A-Za-z0-9_\-\\.]+\.md",
    r"README\.md",
]


def _safe_len(value):
    try:
        return len(value)
    except Exception:
        return len(str(value))


def _to_text(value) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _preview(value, limit: int = TEXT_PREVIEW_LIMIT) -> dict:
    text = _to_text(value)

    return {
        "preview": text[:limit],
        "length": len(text),
        "truncated": len(text) > limit,
        "full": text,
    }


def _extract_text_value(arguments, keys):
    if not isinstance(arguments, dict):
        return ""

    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    input_value = arguments.get("input")
    if isinstance(input_value, str) and input_value.strip():
        return input_value.strip()

    return ""


def detect_test_command_kind(command: str) -> str:
    lower = str(command or "").lower()

    for pattern, kind in TEST_COMMAND_PATTERNS:
        if pattern in lower:
            return kind

    return ""


def detect_command_trace(tool_name, arguments):
    lower_name = str(tool_name or "").lower()
    command = _extract_text_value(
        arguments,
        [
            "command",
            "cmd",
            "command_line",
            "commandLine",
            "script",
        ],
    )
    cwd = _extract_text_value(
        arguments,
        [
            "cwd",
            "workdir",
            "working_directory",
            "workingDirectory",
            "directory",
        ],
    )

    looks_like_command_tool = any(
        token in lower_name
        for token in ["shell", "exec", "run", "command", "process", "terminal", "bash", "powershell", "cmd"]
    )

    if not command and not looks_like_command_tool:
        return None

    test_command_kind = detect_test_command_kind(command)

    return {
        "tool_name": str(tool_name or ""),
        "command": command,
        "cwd": cwd,
        "is_test_command": bool(test_command_kind),
        "test_command_kind": test_command_kind,
    }


def extract_command_result_fields(result_text, error_text=""):
    exit_code = None
    stdout_value = ""
    stderr_value = str(error_text or "")

    parsed = None
    if isinstance(result_text, str):
        text = result_text.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
    elif isinstance(result_text, dict):
        parsed = result_text

    if isinstance(parsed, dict):
        raw_exit_code = parsed.get("exit_code", parsed.get("code"))
        try:
            if raw_exit_code is not None and str(raw_exit_code) != "":
                exit_code = int(raw_exit_code)
        except Exception:
            exit_code = None

        stdout_value = _to_text(parsed.get("stdout", parsed.get("output", parsed.get("text", ""))))
        parsed_stderr = parsed.get("stderr")
        if parsed_stderr not in (None, ""):
            stderr_value = _to_text(parsed_stderr)
    else:
        stdout_value = _to_text(result_text)

    stdout_preview = _preview(stdout_value)
    stderr_preview = _preview(stderr_value)

    return {
        "exit_code": exit_code,
        "stdout_preview": stdout_preview["preview"],
        "stdout_length": stdout_preview["length"],
        "stderr_preview": stderr_preview["preview"],
        "stderr_length": stderr_preview["length"],
    }


def build_command_trace_payload(
    command_info,
    *,
    started_at="",
    finished_at="",
    duration_ms=None,
    result_text="",
    error_text="",
):
    command_info = command_info or {}
    result_fields = extract_command_result_fields(result_text, error_text=error_text)

    return {
        "command": command_info.get("command", ""),
        "cwd": command_info.get("cwd", ""),
        "started_at": started_at or "",
        "finished_at": finished_at or "",
        "duration_ms": duration_ms,
        "exit_code": result_fields.get("exit_code"),
        "stdout_preview": result_fields.get("stdout_preview", ""),
        "stderr_preview": result_fields.get("stderr_preview", ""),
        "stdout_length": result_fields.get("stdout_length", 0),
        "stderr_length": result_fields.get("stderr_length", 0),
        "is_test_command": bool(command_info.get("is_test_command")),
        "test_command_kind": command_info.get("test_command_kind", ""),
        "tool_name": command_info.get("tool_name", ""),
    }


def detect_context_files_from_messages(messages):
    found = []

    for index, message in enumerate(messages or []):
        role = message.get("role")
        content = message.get("content", "")

        if not isinstance(content, str):
            content = str(content)

        for pattern in CONTEXT_FILE_PATTERNS:
            for match in re.findall(pattern, content):
                normalized = match.replace("\\", "/")
                found.append({
                    "message_index": index,
                    "role": role,
                    "path": normalized,
                })

    deduped = []
    seen = set()

    for item in found:
        key = item["path"]
        if key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    return deduped


def summarize_opencode_request(body):
    messages = body.get("messages") or []
    tools = body.get("tools") or []

    return {
        "model": body.get("model"),
        "stream": body.get("stream", False),
        "messages_count": len(messages),
        "tools_count": len(tools),
        "context_files": detect_context_files_from_messages(messages),
        "debug_mode": {
            "masked": False,
            "note": "OpenCode request summary is logged for local debugging.",
        },
    }


def summarize_mcp_tool_call(tool_call):
    if not isinstance(tool_call, dict):
        return {
            "type": type(tool_call).__name__,
            "preview": str(tool_call),
        }

    function = tool_call.get("function") or {}
    arguments = function.get("arguments", "")

    return {
        "id": tool_call.get("id"),
        "type": tool_call.get("type"),
        "name": function.get("name") or tool_call.get("name"),
        "arguments": _preview(arguments),
    }


def summarize_tool_result(result):
    if isinstance(result, dict):
        return {
            "type": "dict",
            "keys": list(result.keys())[:20],
            "result": result,
            "text": _preview(result),
        }

    if isinstance(result, str):
        return {
            "type": "str",
            "text": _preview(result),
        }

    return {
        "type": type(result).__name__,
        "text": _preview(result),
    }


def summarize_loop_result(result):
    if not isinstance(result, dict):
        return {
            "type": type(result).__name__,
            "preview": str(result),
        }

    choices = result.get("choices") or []

    return {
        "keys": list(result.keys()),
        "id": result.get("id"),
        "model": result.get("model"),
        "choices_count": len(choices),
        "has_error": "error" in result,
        "result": result,
    }
