
import json


TEXT_PREVIEW_LIMIT = 2000


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


def build_text_debug_fields(name: str, value, limit: int = TEXT_PREVIEW_LIMIT) -> dict:
    text = _to_text(value)

    return {
        f"{name}_preview": text[:limit],
        f"{name}_length": len(text),
        f"{name}_truncated": len(text) > limit,
        f"{name}_full": text,
    }


def summarize_changed_files_snapshot(snapshot: dict | None) -> dict:
    snapshot = snapshot or {}
    changed_files = list(snapshot.get("changed_files") or [])
    untracked_files = list(snapshot.get("untracked_files") or [])

    return {
        "changed_files": changed_files,
        "changed_files_count": len(changed_files),
        "additions": int(snapshot.get("additions") or 0),
        "deletions": int(snapshot.get("deletions") or 0),
        "untracked_files": untracked_files,
        "untracked_files_count": len(untracked_files),
    }


def summarize_diff_generated_snapshot(snapshot: dict | None) -> dict:
    snapshot = snapshot or {}
    diff_preview = _to_text(snapshot.get("diff_preview", ""))
    changed_files = list(snapshot.get("changed_files") or [])
    untracked_files = list(snapshot.get("untracked_files") or [])

    return {
        "diff_preview": diff_preview,
        "diff_preview_length": len(diff_preview),
        "additions": int(snapshot.get("additions") or 0),
        "deletions": int(snapshot.get("deletions") or 0),
        "changed_files": changed_files,
        "untracked_files": untracked_files,
        "untracked_files_count": len(untracked_files),
    }


def summarize_validation_summary(summary: dict | None) -> dict:
    summary = summary or {}
    validation_signals = list(summary.get("validation_signals") or [])

    return {
        "commands_run": int(summary.get("commands_run") or 0),
        "test_commands_run": int(summary.get("test_commands_run") or 0),
        "passed_commands": int(summary.get("passed_commands") or 0),
        "failed_commands": int(summary.get("failed_commands") or 0),
        "validation_result": str(summary.get("validation_result") or "unknown"),
        "validation_signals": validation_signals,
    }


def summarize_run_summary(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "run_id": summary.get("run_id"),
        "workflow_mode": summary.get("workflow_mode"),
        "selected_agent": summary.get("selected_agent"),
        "active_stage": summary.get("active_stage"),
        "workflow_status": summary.get("workflow_status"),
        "loaded_skills": list(summary.get("loaded_skills") or []),
        "tool_calls_count": int(summary.get("tool_calls_count") or 0),
        "mcp_internal_tool_calls": int(summary.get("mcp_internal_tool_calls") or 0),
        "mcp_external_tool_calls": int(summary.get("mcp_external_tool_calls") or 0),
        "files_changed_count": int(summary.get("files_changed_count") or 0),
        "changed_files": list(summary.get("changed_files") or []),
        "untracked_files_count": int(summary.get("untracked_files_count") or 0),
        "untracked_files": list(summary.get("untracked_files") or []),
        "diff_preview_length": int(summary.get("diff_preview_length") or 0),
        "commands_run": int(summary.get("commands_run") or 0),
        "test_commands_run": int(summary.get("test_commands_run") or 0),
        "validation_result": str(summary.get("validation_result") or "unknown"),
        "final_status": str(summary.get("final_status") or "unknown"),
        "duration_ms": summary.get("duration_ms"),
        "runner_error": summary.get("runner_error"),
        "model_response_sent": bool(summary.get("model_response_sent")),
    }


def build_command_event_preview(payload: dict | None) -> str:
    payload = payload or {}
    command = str(payload.get("command") or "")[:120]
    duration_ms = payload.get("duration_ms")
    exit_code = payload.get("exit_code")

    parts = []

    if exit_code is not None:
        parts.append(f"exit_code={exit_code}")

    if duration_ms not in (None, ""):
        parts.append(f"duration_ms={duration_ms}")

    if command:
        parts.append(f"command={command}")

    return " ".join(parts) if parts else "command event"


def build_validation_summary_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"validation={payload.get('validation_result', 'unknown')} "
        f"commands={int(payload.get('commands_run') or 0)} "
        f"tests={int(payload.get('test_commands_run') or 0)}"
    )


def build_run_summary_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"files_changed={int(payload.get('files_changed_count') or 0)} "
        f"commands={int(payload.get('commands_run') or 0)} "
        f"validation={payload.get('validation_result', 'unknown')} "
        f"status={payload.get('final_status', 'unknown')}"
    )


def summarize_chat_request(body: dict) -> dict:
    messages = body.get("messages") or []
    tools = body.get("tools") or []

    message_summary = []

    for index, message in enumerate(messages):
        content = message.get("content", "")

        message_summary.append({
            "index": index,
            "role": message.get("role"),
            "content_type": type(content).__name__,
            **build_text_debug_fields("content", content),
        })

    return {
        "model": body.get("model"),
        "stream": body.get("stream", False),
        "messages_count": len(messages),
        "tools_count": len(tools),
        "messages": message_summary,
        "debug_mode": {
            "masked": False,
            "note": "Prompt and response text are logged for local debugging.",
        },
    }


def summarize_tool_calls_from_response(response_payload) -> list[dict]:
    if not isinstance(response_payload, dict):
        return []

    choices = response_payload.get("choices") or []
    result = []

    for choice_index, choice in enumerate(choices):
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []

        for tool_index, tool_call in enumerate(tool_calls):
            function = tool_call.get("function") or {}
            arguments = function.get("arguments", "")

            result.append({
                "choice_index": choice_index,
                "tool_index": tool_index,
                "id": tool_call.get("id"),
                "type": tool_call.get("type"),
                "name": function.get("name"),
                **build_text_debug_fields("arguments", arguments),
            })

    return result


def summarize_chat_response(response_payload) -> dict:
    try:
        if not isinstance(response_payload, dict):
            return {
                "type": type(response_payload).__name__,
                "preview": str(response_payload),
            }

        choices = response_payload.get("choices") or []
        first_choice = choices[0] if choices else {}
        message = first_choice.get("message") or {}
        tool_calls = summarize_tool_calls_from_response(response_payload)

        content = message.get("content", "")

        return {
            "id": response_payload.get("id"),
            "model": response_payload.get("model"),
            "choices_count": len(choices),
            "finish_reason": first_choice.get("finish_reason"),
            "has_content": bool(content),
            **build_text_debug_fields("content", content),
            "has_tool_calls": len(tool_calls) > 0,
            "tool_calls_count": len(tool_calls),
            "tool_calls": tool_calls,
        }

    except Exception as error:
        return {
            "summary_error": str(error),
        }


def summarize_error(error) -> dict:
    return {
        "error": str(error),
        "error_type": type(error).__name__,
    }
