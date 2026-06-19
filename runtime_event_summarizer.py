
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
