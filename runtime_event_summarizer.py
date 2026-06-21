
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


def summarize_structured_plan(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "parse_status": str(summary.get("parse_status") or "not_found"),
        "goal": str(summary.get("goal") or ""),
        "steps": list(summary.get("steps") or []),
        "steps_count": int(summary.get("steps_count") or 0),
        "allowed_files": list(summary.get("allowed_files") or []),
        "allowed_files_count": int(summary.get("allowed_files_count") or 0),
        "forbidden_files": list(summary.get("forbidden_files") or []),
        "forbidden_files_count": int(summary.get("forbidden_files_count") or 0),
        "validation_commands": list(summary.get("validation_commands") or []),
        "validation_commands_count": int(summary.get("validation_commands_count") or 0),
        "risks": list(summary.get("risks") or []),
        "risks_count": int(summary.get("risks_count") or 0),
        "raw_preview": str(summary.get("raw_preview") or ""),
    }


def summarize_build_result_capture(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "parse_status": str(summary.get("parse_status") or "not_found"),
        "completed_steps": list(summary.get("completed_steps") or []),
        "completed_steps_count": int(summary.get("completed_steps_count") or 0),
        "skipped_steps": list(summary.get("skipped_steps") or []),
        "skipped_steps_count": int(summary.get("skipped_steps_count") or 0),
        "failed_steps": list(summary.get("failed_steps") or []),
        "failed_steps_count": int(summary.get("failed_steps_count") or 0),
        "changed_files": list(summary.get("changed_files") or []),
        "changed_files_count": int(summary.get("changed_files_count") or 0),
        "notes_preview": str(summary.get("notes_preview") or ""),
    }


def summarize_build_steps_detected(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "planned_steps_count": int(summary.get("planned_steps_count") or 0),
        "completed_steps_count": int(summary.get("completed_steps_count") or 0),
        "missing_steps": list(summary.get("missing_steps") or []),
        "missing_steps_count": int(summary.get("missing_steps_count") or 0),
        "extra_steps": list(summary.get("extra_steps") or []),
        "extra_steps_count": int(summary.get("extra_steps_count") or 0),
    }


def summarize_verification_result(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "parse_status": str(summary.get("parse_status") or "not_found"),
        "plan_compliance_status": str(summary.get("plan_compliance_status") or "unknown"),
        "completed_steps_count": int(summary.get("completed_steps_count") or 0),
        "missing_steps_count": int(summary.get("missing_steps_count") or 0),
        "unexpected_files": list(summary.get("unexpected_files") or []),
        "unexpected_files_count": int(summary.get("unexpected_files_count") or 0),
        "validation_result": str(summary.get("validation_result") or "unknown"),
        "caveats": list(summary.get("caveats") or []),
    }


def summarize_plan_compliance_check(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "plan_compliance_status": str(summary.get("plan_compliance_status") or "unknown"),
        "planned_steps_count": int(summary.get("planned_steps_count") or 0),
        "completed_steps_count": int(summary.get("completed_steps_count") or 0),
        "missing_steps": list(summary.get("missing_steps") or []),
        "missing_steps_count": int(summary.get("missing_steps_count") or 0),
        "unexpected_files": list(summary.get("unexpected_files") or []),
        "unexpected_files_count": int(summary.get("unexpected_files_count") or 0),
        "forbidden_files_touched": list(summary.get("forbidden_files_touched") or []),
        "forbidden_files_touched_count": int(summary.get("forbidden_files_touched_count") or 0),
        "validation_result": str(summary.get("validation_result") or "unknown"),
        "caveats": list(summary.get("caveats") or []),
    }


def summarize_stage_handoff(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "handoff_id": str(summary.get("handoff_id") or ""),
        "from_stage": str(summary.get("from_stage") or ""),
        "to_stage": str(summary.get("to_stage") or ""),
        "from_agent": str(summary.get("from_agent") or ""),
        "to_agent": str(summary.get("to_agent") or ""),
        "from_status": str(summary.get("from_status") or ""),
        "to_status": str(summary.get("to_status") or ""),
        "included_plan": bool(summary.get("included_plan")),
        "plan_parse_status": str(summary.get("plan_parse_status") or ""),
        "plan_steps_count": int(summary.get("plan_steps_count") or 0),
        "included_build_result": bool(summary.get("included_build_result")),
        "build_result_parse_status": str(summary.get("build_result_parse_status") or ""),
        "completed_steps_count": int(summary.get("completed_steps_count") or 0),
        "skipped_steps_count": int(summary.get("skipped_steps_count") or 0),
        "output_preview_length": int(summary.get("output_preview_length") or 0),
        "output_length": int(summary.get("output_length") or 0),
        "changed_files": list(summary.get("changed_files") or []),
        "changed_files_count": int(summary.get("changed_files_count") or 0),
        "diff_preview_length": int(summary.get("diff_preview_length") or 0),
        "validation_result": str(summary.get("validation_result") or "unknown"),
    }


def summarize_stage_tool_policy(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "tool": str(summary.get("tool") or ""),
        "policy": str(summary.get("policy") or summary.get("mode") or "default"),
        "allow_mutating_tools": bool(summary.get("allow_mutating_tools")),
        "allow_validation_commands": bool(summary.get("allow_validation_commands")),
        "is_mutating_tool": bool(summary.get("is_mutating_tool")),
        "is_validation_command": bool(summary.get("is_validation_command")),
        "decision": str(summary.get("decision") or "allowed"),
        "reason": str(summary.get("reason") or ""),
    }


def summarize_validation_strategy(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "strategy": str(summary.get("strategy") or "unknown"),
        "auto_run": bool(summary.get("auto_run")),
        "commands": list(summary.get("commands") or []),
        "commands_count": int(summary.get("commands_count") or 0),
        "skipped_commands": list(summary.get("skipped_commands") or []),
        "changed_files_count": int(summary.get("changed_files_count") or 0),
    }


def summarize_review_result(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "stage_status": str(summary.get("stage_status") or ""),
        "parse_status": str(summary.get("parse_status") or "not_found"),
        "risk": str(summary.get("risk") or "unknown"),
        "commit_readiness": str(summary.get("commit_readiness") or "unknown"),
        "scope_control": str(summary.get("scope_control") or "unknown"),
        "validation_confidence": str(summary.get("validation_confidence") or "unknown"),
    }


def summarize_skill_request(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "workflow_mode": str(summary.get("workflow_mode") or ""),
        "stage": str(summary.get("stage") or ""),
        "agent": str(summary.get("agent") or ""),
        "requested_skills": list(summary.get("requested_skills") or []),
        "approved_skills": list(summary.get("approved_skills") or []),
        "denied_skills": list(summary.get("denied_skills") or []),
        "source": str(summary.get("source") or ""),
    }


def summarize_audit_artifact(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "run_id": str(summary.get("run_id") or ""),
        "directory": str(summary.get("directory") or ""),
        "files": list(summary.get("files") or []),
        "files_count": int(summary.get("files_count") or 0),
    }


def summarize_run_summary(summary: dict | None) -> dict:
    summary = summary or {}

    return {
        "run_id": summary.get("run_id"),
        "workflow_mode": summary.get("workflow_mode"),
        "selected_agent": summary.get("selected_agent"),
        "active_stage": summary.get("active_stage"),
        "workflow_status": summary.get("workflow_status"),
        "completed_stages": list(summary.get("completed_stages") or []),
        "failed_stages": list(summary.get("failed_stages") or []),
        "skipped_stages": list(summary.get("skipped_stages") or []),
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
        "status_reason": str(summary.get("status_reason") or ""),
        "warnings": list(summary.get("warnings") or []),
        "warnings_count": int(summary.get("warnings_count") or 0),
        "verifier_guard_result": str(summary.get("verifier_guard_result") or "not_applicable"),
        "validation_strategy": str(summary.get("validation_strategy") or ""),
        "suggested_validation_commands": list(summary.get("suggested_validation_commands") or []),
        "suggested_validation_commands_count": int(summary.get("suggested_validation_commands_count") or 0),
        "requested_skills": list(summary.get("requested_skills") or []),
        "approved_requested_skills": list(summary.get("approved_requested_skills") or []),
        "denied_requested_skills": list(summary.get("denied_requested_skills") or []),
        "review_result": summary.get("review_result") or {},
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


def build_plan_created_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"plan {payload.get('parse_status', 'not_found')} "
        f"steps={int(payload.get('steps_count') or 0)} "
        f"validation_commands={int(payload.get('validation_commands_count') or 0)}"
    )


def build_build_result_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"build result {payload.get('parse_status', 'not_found')} "
        f"completed={int(payload.get('completed_steps_count') or 0)} "
        f"skipped={int(payload.get('skipped_steps_count') or 0)} "
        f"failed={int(payload.get('failed_steps_count') or 0)} "
        f"changed_files={int(payload.get('changed_files_count') or 0)}"
    )


def build_plan_compliance_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"verification {payload.get('plan_compliance_status', 'unknown')} "
        f"planned={int(payload.get('planned_steps_count') or 0)} "
        f"completed={int(payload.get('completed_steps_count') or 0)} "
        f"missing={int(payload.get('missing_steps_count') or 0)} "
        f"unexpected_files={int(payload.get('unexpected_files_count') or 0)} "
        f"validation={payload.get('validation_result', 'unknown')}"
    )


def build_stage_handoff_preview(payload: dict | None) -> str:
    payload = payload or {}
    from_stage = payload.get("from_stage", "unknown")
    to_stage = payload.get("to_stage", "unknown")
    if to_stage == "builder":
        return (
            f"{from_stage}->{to_stage} "
            f"status={payload.get('to_status', 'ready')} "
            f"steps={int(payload.get('plan_steps_count') or 0)}"
        )
    return (
        f"{from_stage}->{to_stage} "
        f"status={payload.get('to_status', 'ready')} "
        f"changed_files={int(payload.get('changed_files_count') or 0)} "
        f"validation={payload.get('validation_result', 'unknown')}"
    )


def build_stage_tool_policy_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"tool_policy stage={payload.get('stage', 'unknown')} "
        f"policy={payload.get('policy', 'default')} "
        f"decision={payload.get('decision', 'allowed')}"
    )


def build_validation_strategy_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"validation_strategy={payload.get('strategy', 'unknown')} "
        f"commands={int(payload.get('commands_count') or 0)} "
        f"auto_run={str(bool(payload.get('auto_run'))).lower()}"
    )


def build_review_result_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"review result stage_status={payload.get('stage_status', 'unknown')} "
        f"parse={payload.get('parse_status', 'not_found')} "
        f"readiness={payload.get('commit_readiness', 'unknown')} "
        f"confidence={payload.get('validation_confidence', 'unknown')}"
    )


def build_skill_request_preview(payload: dict | None) -> str:
    payload = payload or {}
    skills = ",".join(payload.get("requested_skills") or []) or "none"
    return f"skill_requested stage={payload.get('stage', 'unknown')} skills={skills}"


def build_audit_artifact_preview(payload: dict | None) -> str:
    payload = payload or {}
    return (
        f"audit_artifacts files={int(payload.get('files_count') or 0)} "
        f"dir={payload.get('directory', '')}"
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
