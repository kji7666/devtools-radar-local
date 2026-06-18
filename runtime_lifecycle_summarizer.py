
import json
import re


TEXT_PREVIEW_LIMIT = 2000

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
