from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
import os
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from runtime_event_log import (
    append_event,
    read_recent_events,
    read_run_events,
    clear_events,
    export_events_text,
    get_event_stats,
    start_run,
    finish_run,
    set_current_run_id,
    reset_current_run_id,
    list_runs,
    delete_run,
)
from opencode_ados_trace import (
    build_ados_template_instruction,
    build_ados_workflow_info,
    build_selected_skill_instructions,
    discover_skills,
    emit_ados_workflow_completed_events,
    emit_ados_workflow_started_events,
    emit_ados_template_injected_event,
    emit_ados_template_loaded_event,
    emit_ados_trace_events,
    emit_opencode_skill_trace_events,
)
from runtime_event_summarizer import (
    summarize_chat_request,
    summarize_chat_response,
    summarize_tool_calls_from_response,
    summarize_error,
    build_text_debug_fields,
    summarize_changed_files_snapshot,
    summarize_diff_generated_snapshot,
    summarize_validation_summary,
    summarize_run_summary,
    summarize_structured_plan,
    summarize_build_result_capture,
    summarize_build_steps_detected,
    summarize_verification_result,
    summarize_plan_compliance_check,
    summarize_stage_handoff,
    summarize_stage_tool_policy,
    summarize_validation_strategy,
    summarize_review_result,
    summarize_skill_request,
    summarize_audit_artifact,
    build_command_event_preview,
    build_validation_summary_preview,
    build_plan_created_preview,
    build_build_result_preview,
    build_plan_compliance_preview,
    build_stage_handoff_preview,
    build_stage_tool_policy_preview,
    build_validation_strategy_preview,
    build_review_result_preview,
    build_skill_request_preview,
    build_audit_artifact_preview,
    build_run_summary_preview,
)
from runtime_lifecycle_summarizer import (
    summarize_opencode_request,
    summarize_mcp_tool_call,
    summarize_tool_result,
    summarize_loop_result,
    detect_command_trace,
    build_command_trace_payload,
)

BASE_DIR = Path(__file__).resolve().parent

MAIN_PY = BASE_DIR / "main.py"
OUTPUT_TXT = BASE_DIR / "output.txt"
RUNNER_LOCK_PATH = BASE_DIR / ".runner.lock"

API_TMP_DIR = BASE_DIR / "api_tmp"
API_LOG_DIR = BASE_DIR / "logs"

MCP_CONFIG_PATH = BASE_DIR / "mcp_servers.json"
MCP_SECURITY_PATH = BASE_DIR / "mcp_security.json"

MCP_AUDIT_LOG = API_LOG_DIR / "mcp_audit.log"
MCP_SECURITY_LOG = API_LOG_DIR / "mcp_security.log"
MCP_PENDING_LOG = API_LOG_DIR / "mcp_pending.log"
MCP_TOOL_SNAPSHOT_PATH = BASE_DIR / "mcp_tool_snapshots.json"

API_TMP_DIR.mkdir(exist_ok=True)
API_LOG_DIR.mkdir(exist_ok=True)

MAX_UNTRACKED_FILE_PREVIEW_BYTES = 4096
MAX_TOTAL_DIFF_PREVIEW_CHARS = 12000

DEFAULT_MODEL = "chatgpt-web-local"
SERVER_VERSION = "0.7.0"
MAX_TOOL_LOOPS = 5
PBV_REQUIRED_STAGES = ["planner", "builder", "verifier"]
MUTATING_TOOL_NAMES = {
    "filesystem__write_file",
    "filesystem__edit_file",
    "filesystem__move_file",
    "apply_patch",
}
PASS_LIKE_VALIDATION_PATTERNS = [
    "tests passed",
    "test passed",
    "all tests passed",
    "all tests pass",
    "validation passed",
    "verified successfully",
    "checks passed",
    "build passed",
    "lint passed",
    "pytest passed",
    "npm build passed",
    "tests are passing",
    "test suite passed",
    "驗證通過",
    "測試通過",
    "檢查通過",
    "建置通過",
]
NEGATIVE_VALIDATION_PATTERNS = [
    "not run",
    "not tested",
    "tests were not run",
    "validation was not run",
    "no validation command was executed",
    "no validation was run",
    "unverified",
    "not verified",
    "未驗證",
    "未執行測試",
    "沒有執行測試",
    "未執行驗證",
]

PLAN_BUILD_VERIFY_TRIGGER_PHRASES = [
    "ados-workflow plan-build-verify",
    "ados plan-build-verify",
    "使用 ados plan-build-verify",
    "使用 ados-workflow plan-build-verify",
]

PLAN_BUILD_VERIFY_REVIEW_TRIGGER_PHRASES = [
    "ados-workflow plan-build-verify-review",
    "ados plan-build-verify-review",
    "雿輻 ados plan-build-verify-review",
    "雿輻 ados-workflow plan-build-verify-review",
]
MUTATING_TOOL_HINTS = [
    "write_file",
    "edit_file",
    "move_file",
    "delete_file",
    "create_directory",
    "remove_directory",
    "apply_patch",
    "file_write",
    "file_edit",
]
STAGE_TOOL_POLICIES = {
    "planner": {
        "mode": "read_only",
        "allow_mutating_tools": False,
        "allow_validation_commands": False,
    },
    "explorer": {
        "mode": "read_only",
        "allow_mutating_tools": False,
        "allow_validation_commands": False,
    },
    "builder": {
        "mode": "write_allowed",
        "allow_mutating_tools": True,
        "allow_validation_commands": False,
    },
    "verifier": {
        "mode": "verify_only",
        "allow_mutating_tools": False,
        "allow_validation_commands": True,
    },
    "reviewer": {
        "mode": "review_only",
        "allow_mutating_tools": False,
        "allow_validation_commands": False,
    },
}

runner_lock = asyncio.Lock()
pending_mcp_calls: dict[str, dict[str, Any]] = {}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Optional[Union[str, list[Any]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = DEFAULT_MODEL
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    mcp: Optional[dict[str, Any]] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "local"


class McpServerConfig(BaseModel):
    enabled: bool = True

    transport: str = "stdio"

    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}

    url: str = ""
    headers: dict[str, str] = {}
    timeout_seconds: int = 30

    risk_level: str = "medium"
    trust: str = "unreviewed"
    network: str = "unknown"
    filesystem: str = "scoped"
    shell: str = "deny"
    auto_approve: bool = False
    notes: str = ""


class McpSecurityDecision(BaseModel):
    allowed: bool
    action: str
    reason: str
    requires_confirmation: bool = False


def now_ts() -> int:
    return int(time.time())


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)

def read_runner_lock_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(RUNNER_LOCK_PATH),
        "exists": RUNNER_LOCK_PATH.exists(),
        "pid": None,
        "started_at": "",
        "age_seconds": None,
        "pid_alive": None,
    }

    if not RUNNER_LOCK_PATH.exists():
        return info

    try:
        text = RUNNER_LOCK_PATH.read_text(encoding="utf-8", errors="replace")
        info["raw"] = text

        for line in text.splitlines():
            if line.startswith("pid="):
                try:
                    info["pid"] = int(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("started_at="):
                info["started_at"] = line.split("=", 1)[1].strip()

        info["age_seconds"] = round(time.time() - RUNNER_LOCK_PATH.stat().st_mtime, 3)

        pid = info.get("pid")
        if isinstance(pid, int):
            if os.name == "nt":
                import subprocess

                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                info["pid_alive"] = str(pid) in result.stdout
            else:
                try:
                    os.kill(pid, 0)
                    info["pid_alive"] = True
                except OSError:
                    info["pid_alive"] = False

    except Exception as error:
        info["error"] = f"{type(error).__name__}: {error}"

    return info


def cleanup_stale_runner_lock(max_age_seconds: int = 900) -> bool:
    if not RUNNER_LOCK_PATH.exists():
        return False

    info = read_runner_lock_info()

    pid_alive = info.get("pid_alive")
    age_seconds = info.get("age_seconds")

    should_remove = False

    if pid_alive is False:
        should_remove = True

    if isinstance(age_seconds, (int, float)) and age_seconds > max_age_seconds:
        should_remove = True

    if not should_remove:
        return False

    try:
        RUNNER_LOCK_PATH.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def read_output_text() -> str:
    if not OUTPUT_TXT.exists():
        return ""
    return OUTPUT_TXT.read_text(encoding="utf-8", errors="replace")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def stable_json_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = str(value)

    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def normalize_content(content: Optional[Union[str, list[Any]]]) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    parts: list[str] = []

    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif "text" in item:
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        else:
            parts.append(str(item))

    return "\n".join(x for x in parts if x)


def sanitize_tool_name(server_name: str, tool_name: str) -> str:
    raw = f"{server_name}__{tool_name}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    return cleaned[:64]


def resolve_command(command: str) -> str:
    if os.name == "nt" and command.lower() in {"npx", "npm", "node"}:
        resolved = shutil.which(f"{command}.cmd") or shutil.which(command)
    else:
        resolved = shutil.which(command)

    return resolved or command


def format_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name") or tool_call.get("name") or ""
    arguments = function.get("arguments") or tool_call.get("arguments") or ""

    return json_dumps(
        {
            "id": tool_call.get("id", ""),
            "name": name,
            "arguments": arguments,
        }
    )

def build_api_prompt(
    messages: list[ChatMessage],
    tools: Optional[list[dict[str, Any]]],
    tool_choice: Any,
    ados_instruction: str = "",
) -> str:
    tools_prompt = build_tools_prompt(tools, tool_choice)
    messages_prompt = build_messages_prompt(messages)

    parts = [
        """
你是透過本機 API 包裝的 ChatGPT Web UI。
請根據下列對話內容回答。
請保留 Markdown 格式。
""".strip()
    ]

    if ados_instruction:
        parts.append(
            """
以下是本次 OpenCode/ADOS 執行角色設定。
這段設定會約束本次 coding agent 的行為。
""".strip()
        )
        parts.append(ados_instruction)

    if tools_prompt:
        parts.append(tools_prompt)

    parts.append("以下是對話內容：")
    parts.append(messages_prompt)

    return "\n\n---\n\n".join(parts).strip()

def build_messages_prompt(messages: list[ChatMessage]) -> str:
    rendered: list[str] = []

    for msg in messages:
        role = msg.role
        content = normalize_content(msg.content)

        if role == "system":
            rendered.append(f"[system]\n{content}")

        elif role == "user":
            rendered.append(f"[user]\n{content}")

        elif role == "assistant":
            rendered.append(f"[assistant]\n{content}")

            if msg.tool_calls:
                rendered.append("[assistant_tool_calls]")
                for call in msg.tool_calls:
                    rendered.append(format_tool_call_for_prompt(call))

        elif role == "tool":
            rendered.append(
                f"[tool_result]\n"
                f"tool_call_id: {msg.tool_call_id or ''}\n"
                f"{content}"
            )

        else:
            rendered.append(f"[{role}]\n{content}")

    return "\n\n".join(x for x in rendered if x).strip()

def build_tools_prompt(tools: Optional[list[dict[str, Any]]], tool_choice: Any) -> str:
    if not tools:
        return ""

    return f"""
你現在正在扮演一個可被 OpenAI-compatible API 呼叫的 assistant。

你可以使用下列 tools。當你需要呼叫工具時，不要解釋，不要輸出 Markdown，不要輸出多餘文字，只能輸出以下格式：

<tool_call>
{{"name":"tool_name","arguments":{{...}}}}
</tool_call>

如果你需要一次呼叫多個工具，請輸出：

<tool_calls>
[
  {{"name":"tool_name_1","arguments":{{...}}}},
  {{"name":"tool_name_2","arguments":{{...}}}}
]
</tool_calls>

如果你不需要工具，請輸出一般最終回答。若你想明確標示最終回答，可以使用：

<final>
你的回答
</final>

可用 tools：

{json_dumps(tools)}

tool_choice:

{json_dumps(tool_choice)}

重要規則：
1. tool name 必須完全使用上面 tools 裡的 function.name。
2. arguments 必須是合法 JSON object。
3. 不要編造不存在的 tool name。
4. 如果需要讀檔、改檔、列目錄、跑指令，必須優先呼叫工具，不要假裝已讀取或已修改。
5. 如果使用者要求閱讀 AGENTS.md、docs/*.md、檢查 git diff、修改檔案，必須呼叫 OpenCode 提供的 read/edit/bash 等工具。
6. 如果使用工具，只能輸出 <tool_call> 或 <tool_calls>，不要加任何自然語言。
7. 不要把 tool call 包在 Markdown code block。
8. arguments 必須是合法 JSON；Windows path 請優先使用正斜線，例如 C:/project/devtools-radar-local/AGENTS.md。
9. 如果使用者已提供 tool_result，請根據 tool_result 繼續回答或決定下一個工具。
10. 如果工具因安全策略被拒絕，請向使用者說明原因，不要假裝已執行。
11. 如果工具回傳 pending_id，代表該工具需要人工確認，請清楚告訴使用者 pending_id。
12. 最終回答請盡量保留 Markdown。
""".strip()

def build_api_prompt(
    messages: list[ChatMessage],
    tools: Optional[list[dict[str, Any]]],
    tool_choice: Any,
    ados_instruction: str = "",
) -> str:
    tools_prompt = build_tools_prompt(tools, tool_choice)
    messages_prompt = build_messages_prompt(messages)

    parts = [
        """
你是透過本機 API 包裝的 ChatGPT Web UI。
請根據下列對話內容回答。
請保留 Markdown 格式。
""".strip()
    ]

    if ados_instruction:
        parts.append(
            """
以下是本次 OpenCode/ADOS 執行角色設定。
這段設定會約束本次 coding agent 的行為。
""".strip()
        )
        parts.append(ados_instruction)

    if tools_prompt:
        parts.append(tools_prompt)

    parts.append("以下是對話內容：")
    parts.append(messages_prompt)

    return "\n\n---\n\n".join(parts).strip()


def extract_tag(text: str, tag: str) -> Optional[str]:
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def escape_lone_backslashes_in_json_text(text: str) -> str:
    """
    Repair common ChatGPT Web tool-call JSON mistakes on Windows paths.

    Example broken JSON:
      {"filePath":"C:/project/devtools-radar-local/AGENTS.md"}

    JSON requires backslashes inside strings to be escaped. This function only
    doubles backslashes that are not valid JSON escapes while inside strings.
    """
    out: list[str] = []
    in_string = False
    escaped = False

    valid_json_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}

    i = 0
    while i < len(text):
        ch = text[i]

        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
                escaped = False
            i += 1
            continue

        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue

        if ch == "\\":
            next_ch = text[i + 1] if i + 1 < len(text) else ""

            if next_ch in valid_json_escapes:
                out.append(ch)
                escaped = True
            else:
                out.append("\\\\")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def try_parse_json(text: str) -> Any:
    text = text.strip()

    candidates: list[str] = [text]

    repaired = escape_lone_backslashes_in_json_text(text)
    if repaired != text:
        candidates.append(repaired)

    code_block = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if code_block:
        block = code_block.group(1).strip()
        candidates.append(block)
        repaired_block = escape_lone_backslashes_in_json_text(block)
        if repaired_block != block:
            candidates.append(repaired_block)

    first_array = re.search(r"(\[.*\])", text, flags=re.DOTALL)
    if first_array:
        arr = first_array.group(1).strip()
        candidates.append(arr)
        repaired_arr = escape_lone_backslashes_in_json_text(arr)
        if repaired_arr != arr:
            candidates.append(repaired_arr)

    first_obj = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if first_obj:
        obj = first_obj.group(1).strip()
        candidates.append(obj)
        repaired_obj = escape_lone_backslashes_in_json_text(obj)
        if repaired_obj != obj:
            candidates.append(repaired_obj)

    seen: set[str] = set()

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue

        seen.add(candidate)

        try:
            return json.loads(candidate)
        except Exception:
            continue

    return None

def normalize_tool_arguments_for_opencode(arguments_obj: Any) -> dict[str, Any]:
    if isinstance(arguments_obj, dict):
        normalized = dict(arguments_obj)
    else:
        normalized = {"input": arguments_obj}

    for key in ["filePath", "filepath", "path", "repo_path", "source", "destination", "target"]:
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = value.replace("\\", "/")

    return normalized


def normalize_tool_call(raw: dict[str, Any]) -> dict[str, Any]:
    name = raw.get("name")

    if not name and isinstance(raw.get("function"), dict):
        name = raw["function"].get("name")

    arguments = raw.get("arguments")

    if arguments is None and isinstance(raw.get("function"), dict):
        arguments = raw["function"].get("arguments")

    if arguments is None:
        arguments = {}

    if isinstance(arguments, str):
        parsed_args = try_parse_json(arguments)
        if isinstance(parsed_args, dict):
            arguments_obj = parsed_args
        else:
            arguments_obj = {"input": arguments}
    elif isinstance(arguments, dict):
        arguments_obj = arguments
    else:
        arguments_obj = {"input": arguments}

    arguments_obj = normalize_tool_arguments_for_opencode(arguments_obj)

    if not name:
        name = "unknown_tool"

    return {
        "id": raw.get("id") or f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": str(name),
            "arguments": json.dumps(arguments_obj, ensure_ascii=False),
        },
    }

def parse_assistant_output(text: str) -> tuple[str, Optional[list[dict[str, Any]]]]:
    if not text:
        return "", None

    tool_calls_block = extract_tag(text, "tool_calls")
    if tool_calls_block:
        parsed = try_parse_json(tool_calls_block)
        if isinstance(parsed, list):
            calls = [normalize_tool_call(x) for x in parsed if isinstance(x, dict)]
            if calls:
                return "", calls
        elif isinstance(parsed, dict):
            return "", [normalize_tool_call(parsed)]

    tool_call_block = extract_tag(text, "tool_call")
    if tool_call_block:
        parsed = try_parse_json(tool_call_block)
        if isinstance(parsed, dict):
            return "", [normalize_tool_call(parsed)]
        elif isinstance(parsed, list):
            calls = [normalize_tool_call(x) for x in parsed if isinstance(x, dict)]
            if calls:
                return "", calls

    # Fallback: sometimes the model outputs raw JSON without clean XML.
    stripped = text.strip()
    if '"name"' in stripped and '"arguments"' in stripped:
        parsed = try_parse_json(stripped)
        if isinstance(parsed, dict):
            return "", [normalize_tool_call(parsed)]
        if isinstance(parsed, list):
            calls = [normalize_tool_call(x) for x in parsed if isinstance(x, dict)]
            if calls:
                return "", calls

    final_block = extract_tag(text, "final")
    if final_block is not None:
        return final_block, None

    return text.strip(), None
    

async def run_main_with_prompt(prompt: str, timeout_seconds: int = 900) -> tuple[int, str, str, str]:
    cleanup_stale_runner_lock(max_age_seconds=timeout_seconds)
    if not MAIN_PY.exists():
        return 1, "", f"main.py not found: {MAIN_PY}", ""

    request_id = uuid.uuid4().hex
    prompt_file = API_TMP_DIR / f"prompt_{request_id}.txt"
    prompt_file.write_text(prompt, encoding="utf-8", errors="replace")

    python_exe = sys.executable

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        python_exe,
        str(MAIN_PY),
        "--prompt-file",
        str(prompt_file),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            cleanup_stale_runner_lock(max_age_seconds=0)
            return 124, "", f"Timeout after {timeout_seconds} seconds", read_output_text()

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return process.returncode or 0, stdout, stderr, read_output_text()

    finally:
        try:
            cleanup_stale_runner_lock(max_age_seconds=0)
        except Exception:
            pass


def load_pending_mcp_calls_from_log() -> None:
    pending_mcp_calls.clear()

    if not MCP_PENDING_LOG.exists():
        return

    try:
        lines = MCP_PENDING_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return

    latest: dict[str, dict[str, Any]] = {}

    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue

        pending_id = record.get("id")
        if not pending_id:
            continue

        latest[pending_id] = record

    pending_mcp_calls.update(latest)


def compact_pending_mcp_log() -> None:
    if not pending_mcp_calls:
        return

    with MCP_PENDING_LOG.open("w", encoding="utf-8") as f:
        for record in pending_mcp_calls.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class McpSecurityManager:
    def __init__(self):
        self.config: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not MCP_SECURITY_PATH.exists():
            self.config = {
                "enabled": True,
                "default_action": "deny",
                "tool_timeout_seconds": 20,
                "max_tool_output_chars": 12000,
                "audit_log_enabled": True,
                "allowed_roots": [str(BASE_DIR)],
                "argument_guards": self.default_argument_guards(),
                "tool_policies": [],
            }
            return

        try:
            self.config = json.loads(MCP_SECURITY_PATH.read_text(encoding="utf-8"))
            if "argument_guards" not in self.config:
                self.config["argument_guards"] = self.default_argument_guards()
        except Exception as error:
            self.config = {
                "enabled": True,
                "default_action": "deny",
                "tool_timeout_seconds": 20,
                "max_tool_output_chars": 12000,
                "audit_log_enabled": True,
                "allowed_roots": [str(BASE_DIR)],
                "argument_guards": self.default_argument_guards(),
                "tool_policies": [],
            }

            with MCP_SECURITY_LOG.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] failed to load mcp_security.json: {error}\n")

    def default_argument_guards(self) -> dict[str, Any]:
        return {
            "blocked_path_patterns": [
                "**/.env",
                "**/.git/**",
                "**/Cookies",
                "**/Local State",
                "**/edge_debug_profile/**",
                "**/browser_profile/**",
                "**/auth_state.json",
                "**/id_rsa",
                "**/id_ed25519",
                "**/*.pem",
                "**/*.key",
            ],
            "blocked_command_patterns": [
                "powershell*",
                "pwsh*",
                "cmd*",
                "bash*",
                "sh*",
                "curl*",
                "wget*",
                "iwr*",
                "irm*",
                "rm *",
                "del *",
                "rmdir *",
                "format *",
                "git push*",
                "git config*",
                "npm publish*",
            ],
            "blocked_env_keys": [
                "*TOKEN*",
                "*SECRET*",
                "*KEY*",
                "*PASSWORD*",
                "*COOKIE*",
            ],
        }

    def reload(self) -> None:
        self.load()

    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def timeout_seconds(self) -> int:
        return int(self.config.get("tool_timeout_seconds", 20))

    def max_output_chars(self) -> int:
        return int(self.config.get("max_tool_output_chars", 12000))

    def default_action(self) -> str:
        return str(self.config.get("default_action", "deny")).lower()

    def argument_guards(self) -> dict[str, Any]:
        return self.config.get("argument_guards", self.default_argument_guards())

    def allowed_roots(self) -> list[Path]:
        roots = []

        for root in self.config.get("allowed_roots", [str(BASE_DIR)]):
            try:
                roots.append(Path(root).resolve())
            except Exception:
                continue

        return roots

    def find_policy(self, tool_name: str) -> dict[str, Any] | None:
        policies = self.config.get("tool_policies", [])

        for policy in policies:
            pattern = str(policy.get("pattern", ""))
            if fnmatch.fnmatch(tool_name, pattern):
                return policy

        return None

    def decide_tool(self, tool_name: str, arguments: dict[str, Any]) -> McpSecurityDecision:
        if not self.enabled():
            return McpSecurityDecision(
                allowed=True,
                action="allow",
                reason="MCP security layer disabled",
            )

        policy = self.find_policy(tool_name)

        if policy:
            action = str(policy.get("action", "deny")).lower()
            reason = str(policy.get("reason", "matched policy"))
        else:
            action = self.default_action()
            reason = f"default_action={action}"

        if action == "allow":
            guard_decision = self.check_argument_guards(arguments)
            if not guard_decision.allowed:
                return guard_decision

            path_decision = self.check_paths(arguments)
            if not path_decision.allowed:
                return path_decision

            return McpSecurityDecision(
                allowed=True,
                action="allow",
                reason=reason,
            )

        if action == "confirm":
            guard_decision = self.check_argument_guards(arguments)
            if not guard_decision.allowed:
                return guard_decision

            path_decision = self.check_paths(arguments)
            if not path_decision.allowed:
                return path_decision

            return McpSecurityDecision(
                allowed=False,
                action="confirm",
                reason=reason,
                requires_confirmation=True,
            )

        return McpSecurityDecision(
            allowed=False,
            action="deny",
            reason=reason,
        )

    def check_paths(self, arguments: dict[str, Any]) -> McpSecurityDecision:
        candidate_paths = self.extract_paths(arguments)

        if not candidate_paths:
            return McpSecurityDecision(
                allowed=True,
                action="allow",
                reason="no path arguments",
            )

        allowed_roots = self.allowed_roots()

        for raw_path in candidate_paths:
            try:
                candidate = Path(raw_path)

                if not candidate.is_absolute():
                    candidate = BASE_DIR / candidate

                resolved = candidate.resolve()

                if not any(self.is_relative_to(resolved, root) for root in allowed_roots):
                    return McpSecurityDecision(
                        allowed=False,
                        action="deny",
                        reason=f"path outside allowed roots: {resolved}",
                    )

            except Exception as error:
                return McpSecurityDecision(
                    allowed=False,
                    action="deny",
                    reason=f"invalid path argument {raw_path}: {error}",
                )

        return McpSecurityDecision(
            allowed=True,
            action="allow",
            reason="all paths inside allowed roots",
        )

    def check_argument_guards(self, arguments: dict[str, Any]) -> McpSecurityDecision:
        guards = self.argument_guards()

        blocked_path_patterns = guards.get("blocked_path_patterns", [])
        blocked_command_patterns = guards.get("blocked_command_patterns", [])
        blocked_env_keys = guards.get("blocked_env_keys", [])

        violations: list[str] = []

        paths = self.extract_paths(arguments)
        for raw_path in paths:
            normalized = str(raw_path).replace("\\", "/")

            for pattern in blocked_path_patterns:
                if fnmatch.fnmatch(normalized, pattern.replace("\\", "/")):
                    violations.append(f"blocked path pattern {pattern}: {raw_path}")

        command_values = self.extract_values_by_keys(
            arguments,
            {
                "command",
                "cmd",
                "shell",
                "script",
                "args",
                "arguments",
                "input",
            },
        )

        for raw_value in command_values:
            if isinstance(raw_value, list):
                candidate = " ".join(str(x) for x in raw_value)
            else:
                candidate = str(raw_value)

            candidate_lower = candidate.strip().lower()

            for pattern in blocked_command_patterns:
                if fnmatch.fnmatch(candidate_lower, pattern.lower()):
                    violations.append(f"blocked command pattern {pattern}: {candidate[:200]}")

        env_values = self.extract_values_by_keys(arguments, {"env", "environment"})
        for env_obj in env_values:
            if isinstance(env_obj, dict):
                for key in env_obj.keys():
                    key_text = str(key)
                    for pattern in blocked_env_keys:
                        if fnmatch.fnmatch(key_text.upper(), pattern.upper()):
                            violations.append(f"blocked env key pattern {pattern}: {key_text}")

        if violations:
            return McpSecurityDecision(
                allowed=False,
                action="deny",
                reason="; ".join(violations[:8]),
            )

        return McpSecurityDecision(
            allowed=True,
            action="allow",
            reason="argument guards passed",
        )

    def extract_paths(self, data: Any) -> list[str]:
        result: list[str] = []

        path_like_keys = {
            "path",
            "paths",
            "file",
            "files",
            "filepath",
            "filePath",
            "source",
            "destination",
            "target",
            "root",
            "directory",
        }

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(v, k)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)
            elif isinstance(value, str):
                if key in path_like_keys:
                    result.append(value)

        walk(data)
        return result

    def extract_values_by_keys(self, data: Any, keys: set[str]) -> list[Any]:
        result: list[Any] = []

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items():
                    if k in keys:
                        result.append(v)
                    walk(v, k)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)

        walk(data)
        return result

    @staticmethod
    def is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def truncate_output(self, text: str) -> str:
        limit = self.max_output_chars()

        if len(text) <= limit:
            return text

        return (
            text[:limit]
            + f"\n\n[TRUNCATED: MCP tool output exceeded {limit} characters]"
        )

    def audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision: McpSecurityDecision,
        status: str,
        result_preview: str = "",
        error: str = "",
    ) -> None:
        if not self.config.get("audit_log_enabled", True):
            return

        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "tool": tool_name,
            "arguments": arguments,
            "decision": decision.model_dump(),
            "status": status,
            "resultPreview": result_preview[:1000],
            "error": error,
        }

        with MCP_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class McpToolSnapshotManager:
    def __init__(self):
        self.snapshots: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not MCP_TOOL_SNAPSHOT_PATH.exists():
            self.snapshots = {}
            return

        try:
            self.snapshots = json.loads(
                MCP_TOOL_SNAPSHOT_PATH.read_text(encoding="utf-8", errors="replace")
            )
        except Exception:
            self.snapshots = {}

    def save(self) -> None:
        MCP_TOOL_SNAPSHOT_PATH.write_text(
            json.dumps(self.snapshots, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def observe_tool(
        self,
        api_tool_name: str,
        server_name: str,
        original_name: str,
        tool: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")

        description = tool.get("description", "")
        input_schema = tool.get("inputSchema") or {}

        description_hash = stable_json_hash(description)
        schema_hash = stable_json_hash(input_schema)

        current = {
            "api_tool_name": api_tool_name,
            "server": server_name,
            "name": original_name,
            "description": description,
            "description_hash": description_hash,
            "schema_hash": schema_hash,
            "schema": input_schema,
            "last_seen": now,
        }

        existing = self.snapshots.get(api_tool_name)

        if not existing:
            record = {
                **current,
                "status": "new",
                "first_seen": now,
                "approved": False,
                "approved_at": "",
                "approved_description_hash": "",
                "approved_schema_hash": "",
                "changes": [],
            }
            self.snapshots[api_tool_name] = record
            self.save()
            return record

        changed = (
            existing.get("description_hash") != description_hash
            or existing.get("schema_hash") != schema_hash
        )

        if changed:
            changes = existing.get("changes", [])
            changes.append(
                {
                    "time": now,
                    "old_description_hash": existing.get("description_hash", ""),
                    "new_description_hash": description_hash,
                    "old_schema_hash": existing.get("schema_hash", ""),
                    "new_schema_hash": schema_hash,
                }
            )

            existing.update(current)
            existing["status"] = "changed"
            existing["approved"] = False
            existing["changes"] = changes[-20:]
            self.save()
            return existing

        existing.update(current)

        if existing.get("approved"):
            existing["status"] = "approved"
        elif existing.get("status") not in {"changed", "new"}:
            existing["status"] = "seen"

        self.save()
        return existing

    def approve_tool(self, api_tool_name: str) -> dict[str, Any]:
        if api_tool_name not in self.snapshots:
            raise KeyError(f"Tool snapshot not found: {api_tool_name}")

        now = datetime.now().isoformat(timespec="seconds")
        record = self.snapshots[api_tool_name]

        record["approved"] = True
        record["approved_at"] = now
        record["approved_description_hash"] = record.get("description_hash", "")
        record["approved_schema_hash"] = record.get("schema_hash", "")
        record["status"] = "approved"

        self.save()
        return record

    def mark_missing_tools(self, current_tool_names: set[str]) -> None:
        changed = False
        now = datetime.now().isoformat(timespec="seconds")

        for name, record in self.snapshots.items():
            if name not in current_tool_names and record.get("status") != "missing":
                record["status"] = "missing"
                record["missing_since"] = now
                changed = True

        if changed:
            self.save()

    def list_snapshots(self) -> list[dict[str, Any]]:
        return sorted(
            self.snapshots.values(),
            key=lambda x: (x.get("server", ""), x.get("api_tool_name", "")),
        )

    def get(self, api_tool_name: str) -> dict[str, Any] | None:
        return self.snapshots.get(api_tool_name)


class McpStdioClient:
    def __init__(self, name: str, config: McpServerConfig):
        self.name = name
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self._id = 0
        self._lock = asyncio.Lock()
        self.initialized = False
        self.tools_cache: list[dict[str, Any]] = []

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return

        env = os.environ.copy()
        env.update(self.config.env or {})

        command = resolve_command(self.config.command)
        args = [command, *self.config.args]

        self.process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(BASE_DIR),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return

        while True:
            line = await self.process.stderr.readline()
            if not line:
                break

            try:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log_path = API_LOG_DIR / "mcp_stderr.log"
                    with log_path.open("a", encoding="utf-8") as f:
                        f.write(f"[{self.name}] {text}\n")
            except Exception:
                pass

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        self.process = None
        self.initialized = False
        self.tools_cache = []

    async def ensure_initialized(self) -> None:
        await self.start()

        if self.initialized:
            return

        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "sampling": {},
                    },
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION,
                    },
                },
            )
        except Exception:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION,
                    },
                },
            )

        await self.notify("notifications/initialized", {})
        self.initialized = True

    async def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }

        if params is not None:
            payload["params"] = params

        await self._write_message(payload)

    async def request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        async with self._lock:
            await self.start()

            self._id += 1
            request_id = self._id

            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }

            if params is not None:
                payload["params"] = params

            await self._write_message(payload)

            while True:
                message = await self._read_message()

                if "method" in message and "id" in message:
                    await self._handle_server_request(message)
                    continue

                if message.get("id") != request_id:
                    continue

                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))

                return message.get("result")

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        if "id" not in message or "method" not in message:
            return

        request_id = message.get("id")
        method = message.get("method")

        if method == "roots/list":
            roots = []

            try:
                for root in mcp_security.allowed_roots():
                    roots.append(
                        {
                            "uri": root.as_uri(),
                            "name": root.name or str(root),
                        }
                    )
            except Exception:
                roots = [
                    {
                        "uri": BASE_DIR.as_uri(),
                        "name": BASE_DIR.name,
                    }
                ]

            await self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "roots": roots,
                    },
                }
            )
            return

        await self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        )

    async def _write_message(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP server {self.name} is not running")

        raw = json.dumps(payload, ensure_ascii=False) + "\n"

        self.process.stdin.write(raw.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise RuntimeError(f"MCP server {self.name} is not running")

        while True:
            line = await self.process.stdout.readline()

            if not line:
                raise RuntimeError(f"MCP server {self.name} closed stdout")

            text = line.decode("utf-8", errors="replace").strip()

            if not text:
                continue

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                log_path = API_LOG_DIR / "mcp_stdout_invalid.log"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{self.name}] invalid stdout line: {text}\n")
                continue

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure_initialized()

        result = await self.request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []

        self.tools_cache = tools
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_initialized()

        result = await self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments or {},
            },
        )

        if not isinstance(result, dict):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": str(result),
                    }
                ],
                "isError": False,
            }

        return result


class McpStreamableHttpClient:
    def __init__(self, name: str, config: McpServerConfig):
        self.name = name
        self.config = config
        self._id = 0
        self._lock = asyncio.Lock()
        self.initialized = False
        self.tools_cache: list[dict[str, Any]] = []
        self.session_id: str = ""
        self.http: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        if self.http:
            return

        if not self.config.url:
            raise RuntimeError(f"MCP HTTP server {self.name} missing url")

        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout_seconds),
            follow_redirects=True,
        )

    async def stop(self) -> None:
        if self.http:
            await self.http.aclose()

        self.http = None
        self.initialized = False
        self.tools_cache = []
        self.session_id = ""

    async def ensure_initialized(self) -> None:
        await self.start()

        if self.initialized:
            return

        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "sampling": {},
                    },
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION,
                    },
                },
            )
        except Exception:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION,
                    },
                },
            )

        await self.notify("notifications/initialized", {})
        self.initialized = True

    async def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }

        if params is not None:
            payload["params"] = params

        await self._post_json_rpc(payload, expect_response=False)

    async def request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        async with self._lock:
            await self.start()

            self._id += 1
            request_id = self._id

            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }

            if params is not None:
                payload["params"] = params

            message = await self._post_json_rpc(payload, expect_response=True)

            if message.get("id") != request_id:
                raise RuntimeError(
                    f"Unexpected MCP response id. Expected {request_id}, got {message.get('id')}"
                )

            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))

            return message.get("result")

    async def _post_json_rpc(self, payload: dict[str, Any], expect_response: bool) -> dict[str, Any]:
        if not self.http:
            raise RuntimeError(f"MCP HTTP server {self.name} is not running")

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **(self.config.headers or {}),
        }

        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        response = await self.http.post(
            self.config.url,
            headers=headers,
            json=payload,
        )

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

        if not expect_response:
            if response.status_code in {200, 202, 204}:
                return {}
            response.raise_for_status()
            return {}

        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        text = response.text.strip()

        if not text:
            return {}

        if "text/event-stream" in content_type:
            parsed = self._parse_sse_response(text)
            if parsed is None:
                raise RuntimeError(f"Empty SSE response from MCP HTTP server {self.name}")
            return parsed

        try:
            return response.json()
        except Exception:
            parsed = try_parse_json(text)
            if isinstance(parsed, dict):
                return parsed

            raise RuntimeError(
                f"Invalid MCP HTTP response from {self.name}: {text[:1000]}"
            )

    def _parse_sse_response(self, text: str) -> Optional[dict[str, Any]]:
        data_lines: list[str] = []

        for raw_line in text.splitlines():
            line = raw_line.strip()

            if not line:
                if data_lines:
                    joined = "\n".join(data_lines).strip()
                    data_lines = []

                    try:
                        parsed = json.loads(joined)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        continue

            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())

        if data_lines:
            joined = "\n".join(data_lines).strip()
            try:
                parsed = json.loads(joined)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None

        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure_initialized()

        result = await self.request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []

        self.tools_cache = tools
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_initialized()

        result = await self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments or {},
            },
        )

        if not isinstance(result, dict):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": str(result),
                    }
                ],
                "isError": False,
            }

        return result


def mcp_result_to_text(result: dict[str, Any]) -> str:
    is_error = bool(result.get("isError", False))
    content = result.get("content", [])

    parts: list[str] = []

    if is_error:
        parts.append("[MCP_TOOL_ERROR]")

    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue

            item_type = item.get("type")

            if item_type == "text":
                parts.append(str(item.get("text", "")))

            elif item_type == "image":
                parts.append(
                    json.dumps(
                        {
                            "type": "image",
                            "mimeType": item.get("mimeType"),
                            "data": "[base64 image omitted]",
                        },
                        ensure_ascii=False,
                    )
                )

            elif item_type == "resource":
                parts.append(json.dumps(item, ensure_ascii=False, indent=2))

            else:
                parts.append(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        parts.append(json.dumps(result, ensure_ascii=False, indent=2))

    return "\n\n".join(x for x in parts if x).strip()


def infer_tool_risk(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    lower = tool_name.lower()

    risk = "medium"
    capabilities: list[str] = []

    if any(x in lower for x in ["read", "list", "search", "get", "tree"]):
        risk = "low"
        capabilities.append("read")

    if any(x in lower for x in ["write", "edit", "create", "move", "update"]):
        risk = "high"
        capabilities.append("write")

    if any(x in lower for x in ["delete", "remove", "rm"]):
        risk = "critical"
        capabilities.append("delete")

    if any(x in lower for x in ["shell", "exec", "run", "command", "process"]):
        risk = "critical"
        capabilities.append("shell")

    if any(x in lower for x in ["http", "web", "fetch", "browser", "search"]):
        capabilities.append("network")

    affected_paths = []
    try:
        affected_paths = mcp_security.extract_paths(arguments)
    except Exception:
        affected_paths = []

    return {
        "risk": risk,
        "capabilities": sorted(set(capabilities)),
        "affected_paths": affected_paths,
    }


def build_tool_review_info(api_tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    info = mcp_manager.tool_map.get(api_tool_name, {})
    server_name = info.get("server", "")
    client = info.get("client")
    server_config = getattr(client, "config", None)

    snapshot = mcp_tool_snapshots.get(api_tool_name)
    inferred = infer_tool_risk(api_tool_name, arguments)

    server_profile = {}

    if server_config:
        server_profile = {
            "transport": server_config.transport,
            "risk_level": server_config.risk_level,
            "trust": server_config.trust,
            "network": server_config.network,
            "filesystem": server_config.filesystem,
            "shell": server_config.shell,
            "auto_approve": server_config.auto_approve,
            "notes": server_config.notes,
        }

    return {
        "server": server_name,
        "tool": api_tool_name,
        "original_tool": info.get("tool", ""),
        "description": info.get("description", ""),
        "schema": info.get("schema", {}),
        "server_profile": server_profile,
        "tool_snapshot": snapshot or {},
        "risk": inferred,
        "matched_policy": mcp_security.find_policy(api_tool_name) or {},
    }


def create_pending_mcp_call(
    tool_name: str,
    arguments: dict[str, Any],
    decision: McpSecurityDecision,
) -> dict[str, Any]:
    pending_id = f"mcp_pending_{uuid.uuid4().hex[:16]}"
    now = datetime.now().isoformat(timespec="seconds")

    review = build_tool_review_info(tool_name, arguments)

    record = {
        "id": pending_id,
        "tool": tool_name,
        "arguments": arguments,
        "decision": decision.model_dump(),
        "review": review,
        "status": "pending",
        "createdAt": now,
        "updatedAt": now,
        "result": "",
        "error": "",
    }

    pending_mcp_calls[pending_id] = record

    with MCP_PENDING_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def update_pending_mcp_call(
    pending_id: str,
    status: str,
    result: str = "",
    error: str = "",
) -> dict[str, Any]:
    if pending_id not in pending_mcp_calls:
        raise KeyError(f"Pending MCP call not found: {pending_id}")

    record = pending_mcp_calls[pending_id]
    record["status"] = status
    record["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    record["result"] = result
    record["error"] = error

    with MCP_PENDING_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


class McpManager:
    def __init__(self):
        self.clients: dict[str, Any] = {}
        self.tool_map: dict[str, dict[str, Any]] = {}
        self.loaded = False

    def load_config(self) -> dict[str, McpServerConfig]:
        if not MCP_CONFIG_PATH.exists():
            return {}

        raw = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
        servers = raw.get("servers", {})
        result: dict[str, McpServerConfig] = {}

        for name, conf in servers.items():
            try:
                parsed = McpServerConfig(**conf)
                if parsed.enabled:
                    result[name] = parsed
            except Exception as error:
                log_path = API_LOG_DIR / "mcp_errors.log"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{name}] invalid server config: {error}\n")

        return result

    async def reload(self) -> None:
        for client in self.clients.values():
            await client.stop()

        self.clients = {}
        self.tool_map = {}
        self.loaded = False
        await self.ensure_loaded()

    async def ensure_loaded(self) -> None:
        if self.loaded:
            return

        configs = self.load_config()

        for name, config in configs.items():
            transport = (config.transport or "stdio").lower()

            if transport == "streamable_http":
                self.clients[name] = McpStreamableHttpClient(name, config)
            else:
                self.clients[name] = McpStdioClient(name, config)

        self.loaded = True

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure_loaded()

        openai_tools: list[dict[str, Any]] = []
        self.tool_map = {}

        for server_name, client in self.clients.items():
            try:
                tools = await client.list_tools()
            except Exception as error:
                log_path = API_LOG_DIR / "mcp_errors.log"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{server_name}] tools/list failed: {error}\n")
                continue

            for tool in tools:
                original_name = str(tool.get("name", ""))
                if not original_name:
                    continue

                api_name = sanitize_tool_name(server_name, original_name)

                input_schema = tool.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                }

                description = tool.get("description") or (
                    f"MCP tool {original_name} from server {server_name}"
                )

                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": api_name,
                        "description": f"[MCP server: {server_name}] {description}",
                        "parameters": input_schema,
                    },
                }

                snapshot = mcp_tool_snapshots.observe_tool(
                    api_tool_name=api_name,
                    server_name=server_name,
                    original_name=original_name,
                    tool=tool,
                )

                openai_tool["function"]["x_mcp_snapshot_status"] = snapshot.get("status", "unknown")

                openai_tools.append(openai_tool)
                self.tool_map[api_name] = {
                    "server": server_name,
                    "tool": original_name,
                    "client": client,
                    "schema": input_schema,
                    "description": description,
                    "snapshot": snapshot,
                }

        mcp_tool_snapshots.mark_missing_tools(set(self.tool_map.keys()))

        return openai_tools

    def is_mcp_tool(self, api_tool_name: str) -> bool:
        return api_tool_name in self.tool_map

    async def call_tool(self, api_tool_name: str, arguments: dict[str, Any]) -> str:
        if api_tool_name not in self.tool_map:
            raise RuntimeError(f"Unknown MCP tool: {api_tool_name}")

        decision = mcp_security.decide_tool(api_tool_name, arguments)

        if not decision.allowed:
            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="blocked",
                error=decision.reason,
            )

            if decision.requires_confirmation:
                pending = create_pending_mcp_call(
                    tool_name=api_tool_name,
                    arguments=arguments,
                    decision=decision,
                )

                raise PermissionError(
                    f"MCP tool requires confirmation and was not auto-executed.\n"
                    f"pending_id: {pending['id']}\n"
                    f"tool: {api_tool_name}\n"
                    f"reason: {decision.reason}"
                )

            raise PermissionError(
                f"MCP tool denied: {api_tool_name}. Reason: {decision.reason}"
            )

        info = self.tool_map[api_tool_name]
        client = info["client"]
        original_tool_name = info["tool"]

        try:
            result = await asyncio.wait_for(
                client.call_tool(original_tool_name, arguments),
                timeout=mcp_security.timeout_seconds(),
            )

            text = mcp_result_to_text(result)
            text = mcp_security.truncate_output(text)

            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="success",
                result_preview=text,
            )

            return text

        except asyncio.TimeoutError:
            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="timeout",
                error=f"timeout after {mcp_security.timeout_seconds()} seconds",
            )

            raise TimeoutError(
                f"MCP tool timeout after {mcp_security.timeout_seconds()} seconds: {api_tool_name}"
            )

        except Exception as error:
            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="error",
                error=f"{type(error).__name__}: {error}",
            )
            raise

    async def execute_approved_pending_call(self, pending_id: str) -> dict[str, Any]:
        if pending_id not in pending_mcp_calls:
            raise KeyError(f"Pending MCP call not found: {pending_id}")

        pending = pending_mcp_calls[pending_id]

        if pending["status"] != "pending":
            raise RuntimeError(
                f"Pending MCP call is not pending. Current status: {pending['status']}"
            )

        api_tool_name = pending["tool"]
        arguments = pending["arguments"]

        if api_tool_name not in self.tool_map:
            await self.list_tools()

        if api_tool_name not in self.tool_map:
            raise RuntimeError(f"Unknown MCP tool: {api_tool_name}")

        info = self.tool_map[api_tool_name]
        client = info["client"]
        original_tool_name = info["tool"]

        decision = McpSecurityDecision(
            allowed=True,
            action="approved",
            reason=f"Manually approved pending call: {pending_id}",
            requires_confirmation=False,
        )

        try:
            result = await asyncio.wait_for(
                client.call_tool(original_tool_name, arguments),
                timeout=mcp_security.timeout_seconds(),
            )

            text = mcp_result_to_text(result)
            text = mcp_security.truncate_output(text)

            update_pending_mcp_call(
                pending_id=pending_id,
                status="approved_executed",
                result=text,
            )

            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="approved_success",
                result_preview=text,
            )

            return {
                "id": pending_id,
                "status": "approved_executed",
                "tool": api_tool_name,
                "arguments": arguments,
                "result": text,
            }

        except asyncio.TimeoutError:
            error = f"timeout after {mcp_security.timeout_seconds()} seconds"

            update_pending_mcp_call(
                pending_id=pending_id,
                status="approved_timeout",
                error=error,
            )

            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="approved_timeout",
                error=error,
            )

            raise TimeoutError(error)

        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"

            update_pending_mcp_call(
                pending_id=pending_id,
                status="approved_error",
                error=error_text,
            )

            mcp_security.audit(
                tool_name=api_tool_name,
                arguments=arguments,
                decision=decision,
                status="approved_error",
                error=error_text,
            )

            raise


mcp_security = McpSecurityManager()
mcp_tool_snapshots = McpToolSnapshotManager()
mcp_manager = McpManager()

load_pending_mcp_calls_from_log()
compact_pending_mcp_log()

def coerce_response_tool_calls(response: dict[str, Any]) -> dict[str, Any]:
    """
    Final safety bridge:

    If ChatGPT Web returned textual:
      <tool_call>...</tool_call>
      <tool_calls>...</tool_calls>

    but earlier parser missed it, convert it into OpenAI-compatible
    assistant.tool_calls before returning to OpenCode / AI SDK.
    """
    try:
        choices = response.get("choices") or []
        if not choices:
            return response

        choice = choices[0]
        if not isinstance(choice, dict):
            return response

        message = choice.get("message") or {}
        if not isinstance(message, dict):
            return response

        # Already correct.
        if message.get("tool_calls"):
            choice["finish_reason"] = "tool_calls"
            return response

        content = message.get("content")
        if not isinstance(content, str):
            return response

        parsed_content, parsed_tool_calls = parse_assistant_output(content)

        if parsed_tool_calls:
            message["content"] = None
            message["tool_calls"] = parsed_tool_calls
            choice["finish_reason"] = "tool_calls"

        return response

    except Exception:
        return response


def make_chat_response(
    req: ChatCompletionRequest,
    content: str,
    tool_calls: Optional[list[dict[str, Any]]],
    prompt_text: str,
    raw_output: str,
) -> dict[str, Any]:
    response_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = now_ts()

    message: dict[str, Any] = {
        "role": "assistant",
        "content": content if not tool_calls else None,
    }

    finish_reason = "stop"

    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": req.model or DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": estimate_tokens(prompt_text),
            "completion_tokens": estimate_tokens(raw_output),
            "total_tokens": estimate_tokens(prompt_text) + estimate_tokens(raw_output),
        },
    }



def _openai_sse_line(data: dict[str, Any]) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


async def _openai_chat_completion_sse_from_response(
    response: dict[str, Any],
    *,
    chunk_size: int = 800,
) -> AsyncIterator[str]:
    """Return OpenAI-compatible chat.completion.chunk SSE.

    This is pseudo-streaming: the ChatGPT Web UI runner and MCP loop still run to
    completion first. Then this function emits the final assistant message as SSE
    chunks so clients such as OpenCode / AI SDK, which send stream=true, can read
    the response without receiving a 400 error.
    """

    stream_id = str(response.get("id") or f"chatcmpl_{uuid.uuid4().hex}")
    created = int(response.get("created") or now_ts())
    model = str(response.get("model") or DEFAULT_MODEL)

    choices = response.get("choices") or []
    first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first_choice.get("message") or {}
    finish_reason = first_choice.get("finish_reason") or "stop"

    if not isinstance(message, dict):
        message = {}

    # 1) Initial role chunk.
    yield _openai_sse_line(
        {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
    )

    tool_calls = message.get("tool_calls") or []

    # 2A) Tool-call chunks, for clients that expect streamed tool calls.
    if isinstance(tool_calls, list) and tool_calls:
        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue

            function = call.get("function") or {}
            if not isinstance(function, dict):
                function = {}

            yield _openai_sse_line(
                {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                                        "type": call.get("type") or "function",
                                        "function": {
                                            "name": str(function.get("name") or ""),
                                            "arguments": str(function.get("arguments") or ""),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            await asyncio.sleep(0)

    # 2B) Normal content chunks.
    else:
        content = message.get("content")
        text = "" if content is None else str(content)

        for i in range(0, len(text), chunk_size):
            piece = text[i : i + chunk_size]

            yield _openai_sse_line(
                {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": piece},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            await asyncio.sleep(0)

    # 3) Finish chunk.
    yield _openai_sse_line(
        {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": finish_reason,
                }
            ],
        }
    )

    # 4) Done marker.
    yield "data: [DONE]\n\n"


def build_streaming_response_from_chat_response(response: dict[str, Any]) -> StreamingResponse:
    response = coerce_response_tool_calls(response)

    return StreamingResponse(
        _openai_chat_completion_sse_from_response(response),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def verify_auth(request: Request) -> None:
    expected_key = os.environ.get("DEVTOOLS_RADAR_API_KEY", "").strip()

    if not expected_key:
        return

    auth = request.headers.get("authorization", "")
    prefix = "Bearer "

    if not auth.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth[len(prefix):].strip()

    if token != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def tool_call_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    fn = tool_call.get("function") or {}
    raw = fn.get("arguments") or "{}"

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {"input": parsed}
        except Exception:
            return {"input": raw}

    return {"input": raw}

def safe_append_event(**kwargs):
    try:
        return append_event(**kwargs)
    except Exception:
        return None


def _safe_rel_git_path(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/").strip()


def _bounded_text(text: str, limit: int) -> str:
    return text[: max(0, limit)]


def _read_untracked_file_preview(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""

        size_bytes = path.stat().st_size
        if size_bytes > MAX_UNTRACKED_FILE_PREVIEW_BYTES:
            return ""

        raw = path.read_bytes()
        if b"\x00" in raw:
            return ""

        text = raw.decode("utf-8", errors="replace")
        return _bounded_text(text, MAX_UNTRACKED_FILE_PREVIEW_BYTES)
    except Exception:
        return ""


def _collect_untracked_files() -> dict[str, dict[str, Any]]:
    try:
        status_result = subprocess.run(
            ["git", "-c", "core.quotepath=off", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return {}

    if status_result.returncode != 0:
        return {}

    untracked_files: dict[str, dict[str, Any]] = {}

    for raw_line in (status_result.stdout or "").splitlines():
        if not raw_line.startswith("?? "):
            continue

        raw_path = raw_line[3:].strip()
        path = _safe_rel_git_path(raw_path)
        if not path:
            continue

        absolute_path = BASE_DIR / Path(path.replace("/", os.sep))

        try:
            stat_info = absolute_path.stat()
            size_bytes = int(stat_info.st_size)
            mtime_ns = int(getattr(stat_info, "st_mtime_ns", int(stat_info.st_mtime * 1_000_000_000)))
        except Exception:
            size_bytes = 0
            mtime_ns = 0

        untracked_files[path] = {
            "path": path,
            "size_bytes": size_bytes,
            "mtime_ns": mtime_ns,
            "preview": _read_untracked_file_preview(absolute_path),
        }

    return untracked_files


def capture_git_diff_snapshot() -> dict[str, Any]:
    empty_snapshot = {
        "changed_files": [],
        "additions": 0,
        "deletions": 0,
        "diff_preview": "",
        "files": {},
        "untracked_files": [],
        "untracked": {},
    }

    try:
        numstat_result = subprocess.run(
            ["git", "-c", "core.quotepath=off", "diff", "--numstat", "--no-ext-diff", "--"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return dict(empty_snapshot)

    if numstat_result.returncode != 0:
        return dict(empty_snapshot)

    files: dict[str, dict[str, Any]] = {}
    changed_files: list[str] = []
    additions = 0
    deletions = 0

    for raw_line in (numstat_result.stdout or "").splitlines():
        parts = raw_line.split("\t", 2)
        if len(parts) != 3:
            continue

        added_text, deleted_text, raw_path = parts
        path = _safe_rel_git_path(raw_path)
        if not path:
            continue

        file_additions = int(added_text) if added_text.isdigit() else 0
        file_deletions = int(deleted_text) if deleted_text.isdigit() else 0

        try:
            patch_result = subprocess.run(
                ["git", "-c", "core.quotepath=off", "diff", "--no-ext-diff", "--", raw_path],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            patch_text = patch_result.stdout if patch_result.returncode == 0 else ""
        except Exception:
            patch_text = ""

        changed_files.append(path)
        additions += file_additions
        deletions += file_deletions
        files[path] = {
            "path": path,
            "additions": file_additions,
            "deletions": file_deletions,
            "patch": patch_text,
        }

    untracked_files = _collect_untracked_files()

    return {
        "changed_files": changed_files,
        "additions": additions,
        "deletions": deletions,
        "diff_preview": "\n".join(
            file_info.get("patch", "")
            for file_info in files.values()
            if file_info.get("patch")
        ),
        "files": files,
        "untracked_files": list(untracked_files.keys()),
        "untracked": untracked_files,
    }


def build_git_diff_delta(
    baseline_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline_files = (baseline_snapshot or {}).get("files") or {}
    current_files = (current_snapshot or {}).get("files") or {}
    baseline_untracked = (baseline_snapshot or {}).get("untracked") or {}
    current_untracked = (current_snapshot or {}).get("untracked") or {}

    changed_files: list[str] = []
    untracked_files: list[str] = []
    additions = 0
    deletions = 0
    patch_parts: list[str] = []

    for path in current_snapshot.get("changed_files") or [] if isinstance(current_snapshot, dict) else []:
        current_file = current_files.get(path) or {}
        baseline_file = baseline_files.get(path) or {}

        current_patch = str(current_file.get("patch") or "")
        baseline_patch = str(baseline_file.get("patch") or "")
        current_additions = int(current_file.get("additions") or 0)
        current_deletions = int(current_file.get("deletions") or 0)
        baseline_additions = int(baseline_file.get("additions") or 0)
        baseline_deletions = int(baseline_file.get("deletions") or 0)

        if (
            current_patch == baseline_patch
            and current_additions == baseline_additions
            and current_deletions == baseline_deletions
        ):
            continue

        changed_files.append(path)
        additions += max(0, current_additions - baseline_additions)
        deletions += max(0, current_deletions - baseline_deletions)

        if current_patch:
            patch_parts.append(current_patch)

    for path in current_snapshot.get("untracked_files") or [] if isinstance(current_snapshot, dict) else []:
        current_file = current_untracked.get(path) or {}
        baseline_file = baseline_untracked.get(path) or {}

        current_size = int(current_file.get("size_bytes") or 0)
        baseline_size = int(baseline_file.get("size_bytes") or 0)
        current_mtime = int(current_file.get("mtime_ns") or 0)
        baseline_mtime = int(baseline_file.get("mtime_ns") or 0)
        current_preview = str(current_file.get("preview") or "")
        baseline_preview = str(baseline_file.get("preview") or "")

        if (
            baseline_file
            and current_size == baseline_size
            and current_mtime == baseline_mtime
            and current_preview == baseline_preview
        ):
            continue

        untracked_files.append(path)
        if path not in changed_files:
            changed_files.append(path)

        if current_preview:
            patch_parts.append(
                "\n".join(
                    [
                        f"--- untracked: {path} ---",
                        current_preview,
                    ]
                ).strip()
            )

    diff_preview = "\n\n".join(x for x in patch_parts if x).strip()

    return {
        "changed_files": changed_files,
        "additions": additions,
        "deletions": deletions,
        "diff_preview": _bounded_text(diff_preview, MAX_TOTAL_DIFF_PREVIEW_CHARS),
        "untracked_files": untracked_files,
    }


def build_validation_summary_payload(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime_state = runtime_state or {}
    commands = list(runtime_state.get("commands") or [])
    test_commands = [item for item in commands if item.get("is_test_command")]
    passed_commands = sum(1 for item in commands if item.get("exit_code") == 0)
    failed_commands = sum(1 for item in commands if item.get("exit_code") not in (None, 0))

    if not test_commands:
        validation_result = "not_run"
    elif any(item.get("exit_code") not in (None, 0) for item in test_commands):
        validation_result = "fail"
    elif all(item.get("exit_code") == 0 for item in test_commands):
        validation_result = "pass"
    else:
        validation_result = "unknown"

    validation_signals: list[str] = []
    if test_commands:
        validation_signals.extend(
            sorted(
                {
                    item.get("test_command_kind") or "test_command"
                    for item in test_commands
                }
            )
        )

        if any(item.get("exit_code") is None for item in test_commands):
            validation_signals.append("missing_exit_code")

    return {
        "commands_run": len(commands),
        "test_commands_run": len(test_commands),
        "passed_commands": passed_commands,
        "failed_commands": failed_commands,
        "validation_result": validation_result,
        "validation_signals": validation_signals,
    }


def has_real_validation_evidence(validation_summary: dict[str, Any] | None) -> bool:
    validation_summary = validation_summary or {}
    return int(validation_summary.get("test_commands_run") or 0) > 0


def detect_negative_validation_context(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(pattern in lowered for pattern in NEGATIVE_VALIDATION_PATTERNS)


def detect_pass_like_validation_claim(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    if detect_negative_validation_context(lowered):
        return False
    return any(pattern in lowered for pattern in PASS_LIKE_VALIDATION_PATTERNS)


def evaluate_verifier_guard(
    *,
    verifier_output: str,
    final_output: str,
    validation_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    validation_summary = validation_summary or {}
    validation_result = str(validation_summary.get("validation_result") or "unknown")
    has_real_validation = has_real_validation_evidence(validation_summary)
    pass_like_claim_detected = False

    if validation_result == "not_run" and not has_real_validation:
        pass_like_claim_detected = (
            detect_pass_like_validation_claim(verifier_output)
            or detect_pass_like_validation_claim(final_output)
        )

    guard_result = "warning" if pass_like_claim_detected else "ok"
    warnings: list[str] = []

    if pass_like_claim_detected:
        warnings.append(
            "Verifier output used pass-like validation language while validation_result=not_run"
        )

    return {
        "validation_result": validation_result,
        "has_real_validation": has_real_validation,
        "pass_like_claim_detected": pass_like_claim_detected,
        "guard_result": guard_result,
        "warning": (
            "Verifier or final response used pass-like language without real validation evidence."
            if pass_like_claim_detected
            else ""
        ),
        "warnings": warnings,
    }


def detect_successful_mutating_tools(tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
    tool_events = list(tool_events or [])
    successful = [
        item for item in tool_events
        if item.get("success") and item.get("tool_name") in MUTATING_TOOL_NAMES
    ]
    return {
        "successful_mutating_tools_count": len(successful),
        "successful_mutating_tools": [str(item.get("tool_name") or "") for item in successful],
    }


def decide_pbv_stage_failure_policy(
    *,
    stage: str,
    stage_status: str,
    output_text: str = "",
    changed_files_count: int = 0,
    successful_mutating_tools_count: int = 0,
) -> dict[str, Any]:
    usable_output = bool(str(output_text or "").strip())

    if stage == "planner":
        if stage_status == "partial" and usable_output:
            return {
                "decision": "continue_to_builder",
                "reason": "planner_partial_with_output",
                "continue_to_next_stage": True,
                "workflow_status": "partial",
            }
        if stage_status in {"partial", "failed", "error"}:
            return {
                "decision": "stop_workflow",
                "reason": "planner_unusable_or_failed",
                "continue_to_next_stage": False,
                "workflow_status": "error",
            }

    if stage == "builder":
        if stage_status == "partial" and (
            changed_files_count > 0 or successful_mutating_tools_count > 0
        ):
            return {
                "decision": "continue_to_verifier",
                "reason": "builder_partial_with_changes",
                "continue_to_next_stage": True,
                "workflow_status": "partial",
            }
        if stage_status == "partial":
            return {
                "decision": "skip_verifier",
                "reason": "builder_partial_without_changes",
                "continue_to_next_stage": False,
                "workflow_status": "partial",
            }
        if stage_status in {"failed", "error"} and (
            changed_files_count > 0 or successful_mutating_tools_count > 0
        ):
            return {
                "decision": "continue_to_verifier",
                "reason": "builder_failed_with_changes",
                "continue_to_next_stage": True,
                "workflow_status": "partial",
            }
        if stage_status in {"failed", "error"}:
            return {
                "decision": "stop_workflow",
                "reason": "builder_failed_without_changes",
                "continue_to_next_stage": False,
                "workflow_status": "error",
            }

    if stage == "verifier" and stage_status in {"partial", "failed"}:
        return {
            "decision": "mark_partial",
            "reason": "verifier_partial",
            "continue_to_next_stage": False,
            "workflow_status": "partial",
        }

    if stage == "verifier" and stage_status == "error":
        return {
            "decision": "mark_error",
            "reason": "verifier_error",
            "continue_to_next_stage": False,
            "workflow_status": "error",
        }

    return {
        "decision": "continue",
        "reason": f"{stage}_completed",
        "continue_to_next_stage": True,
        "workflow_status": "unknown",
    }


def derive_workflow_status(
    *,
    workflow_mode: str,
    stage_statuses: dict[str, str] | None,
    completed_stages: list[str] | None,
    failed_stages: list[str] | None,
    skipped_stages: list[str] | None,
    changed_files_count: int,
    validation_summary: dict[str, Any] | None,
    warnings: list[str] | None,
    runner_error: Any,
    model_response_sent: bool,
) -> dict[str, str]:
    stage_statuses = dict(stage_statuses or {})
    completed_stages = list(completed_stages or [])
    failed_stages = list(failed_stages or [])
    skipped_stages = list(skipped_stages or [])
    validation_summary = validation_summary or {}
    warnings = list(warnings or [])

    validation_result = str(validation_summary.get("validation_result") or "unknown")
    failed_commands = int(validation_summary.get("failed_commands") or 0)
    all_required_completed = all(
        stage_statuses.get(stage) == "completed"
        for stage in (PBV_REQUIRED_STAGES if workflow_mode == "plan_build_verify" else [])
    ) if workflow_mode == "plan_build_verify" else False
    any_partial = any(status == "partial" for status in stage_statuses.values())
    any_error = any(status == "error" for status in stage_statuses.values())

    if runner_error and not completed_stages:
        return {
            "workflow_status": "error",
            "final_status": "error",
            "status_reason": "Runner or backend error prevented a usable workflow result.",
        }

    if validation_result == "fail" or failed_commands > 0:
        return {
            "workflow_status": "failed",
            "final_status": "failed",
            "status_reason": "Real validation or command evidence reported failure.",
        }

    if workflow_mode == "plan_build_verify":
        if all_required_completed:
            if warnings:
                return {
                    "workflow_status": "completed_with_warnings",
                    "final_status": "completed_with_warnings",
                    "status_reason": "All PBV stages completed but warnings were recorded.",
                }
            if validation_result == "pass":
                return {
                    "workflow_status": "completed",
                    "final_status": "completed",
                    "status_reason": "All PBV stages completed and real validation passed.",
                }
            if validation_result == "not_run":
                return {
                    "workflow_status": "completed_unverified",
                    "final_status": "completed_unverified",
                    "status_reason": "All PBV stages completed but no validation command was run.",
                }
            return {
                "workflow_status": "completed_with_warnings",
                "final_status": "completed_with_warnings",
                "status_reason": "All PBV stages completed but validation evidence is incomplete.",
            }

        if completed_stages or any_partial or failed_stages or skipped_stages:
            if any_error and not changed_files_count and not model_response_sent:
                return {
                    "workflow_status": "error",
                    "final_status": "error",
                    "status_reason": "A PBV stage failed before a useful result was produced.",
                }
            return {
                "workflow_status": "partial",
                "final_status": "partial",
                "status_reason": "At least one PBV stage completed, but the full workflow did not complete cleanly.",
            }

    if warnings:
        return {
            "workflow_status": "completed_with_warnings",
            "final_status": "completed_with_warnings",
            "status_reason": "Warnings were recorded for this run.",
        }

    if any_error:
        return {
            "workflow_status": "error",
            "final_status": "error",
            "status_reason": "A workflow stage reported an unrecoverable error.",
        }

    if model_response_sent and changed_files_count and validation_result == "not_run":
        return {
            "workflow_status": "completed_unverified",
            "final_status": "completed_unverified",
            "status_reason": "The response completed, but changed files were not validated.",
        }

    if model_response_sent:
        return {
            "workflow_status": "completed",
            "final_status": "completed",
            "status_reason": "The run completed successfully.",
        }

    return {
        "workflow_status": "unknown",
        "final_status": "unknown",
        "status_reason": "The workflow ended without enough evidence to classify the result.",
    }


def build_run_summary_payload(
    *,
    run_id: str,
    runtime_state: dict[str, Any] | None,
    git_diff_delta: dict[str, Any] | None,
    validation_summary: dict[str, Any] | None,
    result: Any,
    duration_ms: int,
) -> dict[str, Any]:
    runtime_state = runtime_state or {}
    git_diff_delta = git_diff_delta or {}
    validation_summary = validation_summary or {}

    changed_files = list(git_diff_delta.get("changed_files") or [])
    untracked_files = list(git_diff_delta.get("untracked_files") or [])
    runner_error = runtime_state.get("runner_error")
    warnings = list(runtime_state.get("warnings") or [])

    model_response_sent = bool(runtime_state.get("model_response_sent"))
    status_info = derive_workflow_status(
        workflow_mode=str(runtime_state.get("workflow_mode") or ""),
        stage_statuses=runtime_state.get("stage_statuses"),
        completed_stages=list(runtime_state.get("completed_stages") or []),
        failed_stages=list(runtime_state.get("failed_stages") or []),
        skipped_stages=list(runtime_state.get("skipped_stages") or []),
        changed_files_count=len(changed_files),
        validation_summary=validation_summary,
        warnings=warnings,
        runner_error=runner_error,
        model_response_sent=model_response_sent,
    )
    final_status = status_info.get("final_status", "unknown")
    workflow_status = status_info.get("workflow_status", final_status)

    return {
        "run_id": run_id,
        "workflow_mode": runtime_state.get("workflow_mode", ""),
        "selected_agent": runtime_state.get("selected_agent", ""),
        "active_stage": runtime_state.get("active_stage", ""),
        "workflow_status": workflow_status or final_status,
        "completed_stages": list(runtime_state.get("completed_stages") or []),
        "failed_stages": list(runtime_state.get("failed_stages") or []),
        "skipped_stages": list(runtime_state.get("skipped_stages") or []),
        "loaded_skills": list(runtime_state.get("loaded_skills") or []),
        "tool_calls_count": int(runtime_state.get("tool_calls_count") or 0),
        "mcp_internal_tool_calls": int(runtime_state.get("mcp_internal_tool_calls") or 0),
        "mcp_external_tool_calls": int(runtime_state.get("mcp_external_tool_calls") or 0),
        "files_changed_count": len(changed_files),
        "changed_files": changed_files,
        "untracked_files_count": len(untracked_files),
        "untracked_files": untracked_files,
        "diff_preview_length": len(str(git_diff_delta.get("diff_preview") or "")),
        "commands_run": int(validation_summary.get("commands_run") or 0),
        "test_commands_run": int(validation_summary.get("test_commands_run") or 0),
        "validation_result": str(validation_summary.get("validation_result") or "unknown"),
        "final_status": final_status,
        "status_reason": status_info.get("status_reason", ""),
        "warnings": warnings,
        "warnings_count": len(warnings),
        "verifier_guard_result": str(runtime_state.get("verifier_guard_result") or "not_applicable"),
        "validation_strategy": str(runtime_state.get("validation_strategy") or ""),
        "suggested_validation_commands": list(runtime_state.get("suggested_validation_commands") or []),
        "suggested_validation_commands_count": len(list(runtime_state.get("suggested_validation_commands") or [])),
        "requested_skills": list(runtime_state.get("requested_skills") or []),
        "approved_requested_skills": list(runtime_state.get("approved_requested_skills") or []),
        "denied_requested_skills": list(runtime_state.get("denied_requested_skills") or []),
        "review_result": runtime_state.get("review_result") or {},
        "duration_ms": duration_ms,
        "runner_error": runner_error,
        "model_response_sent": model_response_sent,
    }


def detect_workflow_mode(messages: list[dict[str, Any]] | list[ChatMessage] | None) -> str:
    joined = ""

    for message in messages or []:
        if isinstance(message, ChatMessage):
            content = normalize_content(message.content)
        elif isinstance(message, dict):
            content = normalize_content(message.get("content"))
        else:
            content = str(message)

        joined += "\n" + content

    lowered = joined.lower()
    if any(phrase in lowered for phrase in PLAN_BUILD_VERIFY_REVIEW_TRIGGER_PHRASES):
        return "plan_build_verify_review"
    if any(phrase in lowered for phrase in PLAN_BUILD_VERIFY_TRIGGER_PHRASES):
        return "plan_build_verify"
    return ""


def detect_plan_build_verify_trigger(messages: list[dict[str, Any]] | list[ChatMessage] | None) -> bool:
    return detect_workflow_mode(messages) in {"plan_build_verify", "plan_build_verify_review"}


def build_plan_build_verify_workflow_info(workflow_mode: str = "plan_build_verify") -> dict[str, Any]:
    stages = ["planner", "builder", "verifier"]
    if workflow_mode == "plan_build_verify_review":
        stages.append("reviewer")

    return {
        "workflow_id": f"ados-workflow-{uuid.uuid4().hex[:12]}",
        "workflow_mode": workflow_mode,
        "selected_agent": "ados-workflow",
        "active_stage": "planner",
        "stages": stages,
    }


def build_stage_request(
    req: ChatCompletionRequest,
    *,
    stage_agent: str,
    stage_instruction: str,
    context_blocks: Optional[list[str]] = None,
) -> ChatCompletionRequest:
    body = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    stage_messages = list(body.get("messages") or [])

    stage_messages.append(
        {
            "role": "system",
            "content": f"Use {stage_agent}.\n{stage_instruction}".strip(),
        }
    )

    for block in context_blocks or []:
        if not block:
            continue
        stage_messages.append(
            {
                "role": "user",
                "content": block,
            }
        )

    body["messages"] = stage_messages
    return ChatCompletionRequest(**body)


def extract_response_content_and_tool_calls(result: dict[str, Any]) -> tuple[str, int]:
    if not isinstance(result, dict):
        return str(result), 0

    choices = result.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return "", 0

    message = choices[0].get("message") or {}
    content = normalize_content(message.get("content")) if isinstance(message, dict) else ""
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else []

    return content.strip(), len(tool_calls or [])


def truncate_preview(text: Any, limit: int = 600) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit]


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()

    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)

    return normalized


def normalize_step_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    steps: list[str] = []
    seen: set[str] = set()

    for item in value:
        text = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(
                item.get("id")
                or item.get("title")
                or item.get("name")
                or item.get("step")
                or ""
            ).strip()
            if not text:
                text = truncate_preview(json.dumps(item, ensure_ascii=False), limit=200)
        else:
            text = str(item).strip()

        if not text or text in seen:
            continue

        seen.add(text)
        steps.append(text)

    return steps


def extract_heading_json_block(text: str, heading: str) -> Any:
    match = re.search(
        rf"##\s*{re.escape(heading)}.*?```(?:json)?\s*(.*?)```",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return try_parse_json(match.group(1).strip())


def extract_structured_plan(text: str) -> dict[str, Any]:
    result = {
        "parse_status": "not_found",
        "goal": "",
        "steps": [],
        "steps_count": 0,
        "allowed_files": [],
        "allowed_files_count": 0,
        "forbidden_files": [],
        "forbidden_files_count": 0,
        "validation_commands": [],
        "validation_commands_count": 0,
        "risks": [],
        "risks_count": 0,
        "raw_preview": truncate_preview(text, limit=800),
    }

    parsed = extract_heading_json_block(text, "Structured Plan")
    if not isinstance(parsed, dict):
        if str(text or "").strip():
            result["parse_status"] = "partial"
        return result

    scope = parsed.get("scope") if isinstance(parsed.get("scope"), dict) else {}
    validation_value = parsed.get("validation")

    if isinstance(validation_value, dict):
        validation_commands = normalize_string_list(
            validation_value.get("commands") or validation_value.get("steps")
        )
    else:
        validation_commands = normalize_string_list(validation_value)

    result.update(
        {
            "parse_status": "parsed",
            "goal": str(parsed.get("goal") or "").strip(),
            "steps": normalize_step_list(parsed.get("steps")),
            "allowed_files": normalize_string_list(
                scope.get("allowed_files") if isinstance(scope, dict) else parsed.get("allowed_files")
            ),
            "forbidden_files": normalize_string_list(
                scope.get("forbidden_files") if isinstance(scope, dict) else parsed.get("forbidden_files")
            ),
            "validation_commands": validation_commands,
            "risks": normalize_string_list(parsed.get("risks")),
        }
    )

    result["steps_count"] = len(result["steps"])
    result["allowed_files_count"] = len(result["allowed_files"])
    result["forbidden_files_count"] = len(result["forbidden_files"])
    result["validation_commands_count"] = len(result["validation_commands"])
    result["risks_count"] = len(result["risks"])

    if not any(
        [
            result["goal"],
            result["steps_count"],
            result["allowed_files_count"],
            result["validation_commands_count"],
            result["risks_count"],
        ]
    ):
        result["parse_status"] = "partial"

    return result


def extract_build_result(text: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "parse_status": "not_found",
        "completed_steps": [],
        "completed_steps_count": 0,
        "skipped_steps": [],
        "skipped_steps_count": 0,
        "failed_steps": [],
        "failed_steps_count": 0,
        "changed_files": [],
        "changed_files_count": 0,
        "notes_preview": "",
    }

    parsed = extract_heading_json_block(text, "Build Result")
    if isinstance(parsed, dict):
        result.update(
            {
                "parse_status": "parsed",
                "completed_steps": normalize_step_list(parsed.get("completed_steps")),
                "skipped_steps": normalize_step_list(parsed.get("skipped_steps")),
                "failed_steps": normalize_step_list(parsed.get("failed_steps")),
                "changed_files": normalize_string_list(parsed.get("changed_files")),
                "notes_preview": truncate_preview(parsed.get("notes"), limit=400),
            }
        )
    else:
        step_results_match = re.search(
            r"##\s*Step Results(.*?)(?:\n##\s|\Z)",
            str(text or ""),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if step_results_match:
            completed_steps: list[str] = []
            skipped_steps: list[str] = []
            failed_steps: list[str] = []

            for raw_line in step_results_match.group(1).splitlines():
                line = raw_line.strip()
                bullet_match = re.match(r"[-*]\s*(.+?)\s*:\s*(completed|skipped|failed)\b", line, flags=re.IGNORECASE)
                if not bullet_match:
                    continue
                step_name = bullet_match.group(1).strip()
                step_status = bullet_match.group(2).lower()
                if step_status == "completed":
                    completed_steps.append(step_name)
                elif step_status == "skipped":
                    skipped_steps.append(step_name)
                else:
                    failed_steps.append(step_name)

            if completed_steps or skipped_steps or failed_steps:
                result.update(
                    {
                        "parse_status": "partial",
                        "completed_steps": normalize_step_list(completed_steps),
                        "skipped_steps": normalize_step_list(skipped_steps),
                        "failed_steps": normalize_step_list(failed_steps),
                    }
                )
            elif str(text or "").strip():
                result["parse_status"] = "partial"
        elif str(text or "").strip():
            result["parse_status"] = "partial"

    result["completed_steps_count"] = len(result["completed_steps"])
    result["skipped_steps_count"] = len(result["skipped_steps"])
    result["failed_steps_count"] = len(result["failed_steps"])
    result["changed_files_count"] = len(result["changed_files"])

    if plan and not result["completed_steps"] and str(text or "").strip():
        result["notes_preview"] = truncate_preview(text, limit=400)

    return result


def compare_build_to_plan(plan: dict[str, Any] | None, build_result: dict[str, Any] | None) -> dict[str, Any]:
    planned_steps = normalize_step_list((plan or {}).get("steps"))
    completed_steps = normalize_step_list((build_result or {}).get("completed_steps"))

    missing_steps = [step for step in planned_steps if step not in completed_steps]
    extra_steps = [step for step in completed_steps if step not in planned_steps]

    return {
        "planned_steps": planned_steps,
        "planned_steps_count": len(planned_steps),
        "completed_steps": completed_steps,
        "completed_steps_count": len(completed_steps),
        "missing_steps": missing_steps,
        "missing_steps_count": len(missing_steps),
        "extra_steps": extra_steps,
        "extra_steps_count": len(extra_steps),
    }


def path_matches_scope_entry(path: str, scope_entry: str) -> bool:
    normalized_path = str(path or "").replace("\\", "/").strip().lower()
    normalized_entry = str(scope_entry or "").replace("\\", "/").strip().lower()

    if not normalized_path or not normalized_entry:
        return False
    if normalized_path == normalized_entry:
        return True
    if "/" not in normalized_entry and Path(normalized_path).name.lower() == normalized_entry:
        return True
    return normalized_path.endswith("/" + normalized_entry)


def check_file_scope(plan: dict[str, Any] | None, changed_files: list[str] | None) -> dict[str, Any]:
    plan = plan or {}
    changed_files = normalize_string_list(changed_files)
    allowed_files = normalize_string_list(plan.get("allowed_files"))
    forbidden_files = normalize_string_list(plan.get("forbidden_files"))

    unexpected_files = []
    if allowed_files:
        unexpected_files = [
            path for path in changed_files
            if not any(path_matches_scope_entry(path, allowed) for allowed in allowed_files)
        ]

    forbidden_files_touched = [
        path for path in changed_files
        if any(path_matches_scope_entry(path, forbidden) for forbidden in forbidden_files)
    ]

    return {
        "allowed_files": allowed_files,
        "allowed_files_count": len(allowed_files),
        "forbidden_files": forbidden_files,
        "forbidden_files_count": len(forbidden_files),
        "unexpected_files": unexpected_files,
        "unexpected_files_count": len(unexpected_files),
        "forbidden_files_touched": forbidden_files_touched,
        "forbidden_files_touched_count": len(forbidden_files_touched),
    }


def extract_verification_result(
    text: str,
    *,
    plan: dict[str, Any] | None = None,
    build_result: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    validation_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_compare = compare_build_to_plan(plan, build_result)
    file_scope = check_file_scope(plan, changed_files)
    validation_summary = validation_summary or {}

    headings_found = all(
        marker.lower() in str(text or "").lower()
        for marker in ["## plan compliance", "## file scope", "## validation"]
    )

    caveats: list[str] = []
    if int(plan_compare.get("missing_steps_count") or 0) > 0:
        caveats.append("Builder did not mark all planned steps as completed.")
    if int(file_scope.get("unexpected_files_count") or 0) > 0:
        caveats.append("Changed files include paths outside the allowed file scope.")
    if int(file_scope.get("forbidden_files_touched_count") or 0) > 0:
        caveats.append("Changed files include forbidden paths.")
    if str(validation_summary.get("validation_result") or "unknown") == "not_run":
        caveats.append("No validation command was run.")

    if file_scope.get("forbidden_files_touched_count"):
        plan_compliance_status = "forbidden_files"
    elif file_scope.get("unexpected_files_count"):
        plan_compliance_status = "scope_mismatch"
    elif plan_compare.get("missing_steps_count"):
        plan_compliance_status = "partial"
    elif str(validation_summary.get("validation_result") or "unknown") == "fail":
        plan_compliance_status = "validation_failed"
    elif headings_found:
        plan_compliance_status = "compliant"
    else:
        plan_compliance_status = "unknown"

    return {
        "parse_status": "parsed" if headings_found else ("partial" if str(text or "").strip() else "not_found"),
        "plan_compliance_status": plan_compliance_status,
        "completed_steps_count": int(plan_compare.get("completed_steps_count") or 0),
        "missing_steps_count": int(plan_compare.get("missing_steps_count") or 0),
        "unexpected_files": list(file_scope.get("unexpected_files") or []),
        "unexpected_files_count": int(file_scope.get("unexpected_files_count") or 0),
        "validation_result": str(validation_summary.get("validation_result") or "unknown"),
        "caveats": caveats,
    }


def build_handoff_payload(
    *,
    workflow_info: dict[str, Any],
    from_stage: str,
    to_stage: str,
    from_agent: str,
    to_agent: str,
    from_status: str,
    to_status: str,
    output_text: str = "",
    plan: dict[str, Any] | None = None,
    build_result: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    diff_preview: str = "",
    validation_result: str = "unknown",
) -> dict[str, Any]:
    plan = plan or {}
    build_result = build_result or {}
    changed_files = normalize_string_list(changed_files)
    output_value = str(output_text or "")

    return {
        "workflow_mode": workflow_info.get("workflow_mode"),
        "handoff_id": f"{workflow_info.get('workflow_id')}-{from_stage}-to-{to_stage}",
        "from_stage": from_stage,
        "to_stage": to_stage,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "from_status": from_status,
        "to_status": to_status,
        "included_plan": bool(plan),
        "plan_parse_status": str(plan.get("parse_status") or ""),
        "plan_steps_count": int(plan.get("steps_count") or 0),
        "included_build_result": bool(build_result),
        "build_result_parse_status": str(build_result.get("parse_status") or ""),
        "completed_steps_count": int(build_result.get("completed_steps_count") or 0),
        "skipped_steps_count": int(build_result.get("skipped_steps_count") or 0),
        "output_preview_length": len(truncate_preview(output_value, limit=400)),
        "output_length": len(output_value),
        "changed_files": changed_files,
        "changed_files_count": len(changed_files),
        "diff_preview_length": len(str(diff_preview or "")),
        "validation_result": validation_result,
    }


def get_stage_tool_policy(stage: str) -> dict[str, Any]:
    policy = dict(STAGE_TOOL_POLICIES.get(str(stage or "").strip(), {}))
    if not policy:
        policy = {
            "mode": "default",
            "allow_mutating_tools": True,
            "allow_validation_commands": True,
        }
    return policy


def is_mutating_tool_name(tool_name: str) -> bool:
    normalized = str(tool_name or "").strip().lower()
    if not normalized:
        return False
    if normalized in {name.lower() for name in MUTATING_TOOL_NAMES}:
        return True
    return any(hint in normalized for hint in MUTATING_TOOL_HINTS)


def apply_stage_tool_policy(
    *,
    workflow_mode: str,
    stage: str,
    agent: str,
    tool_name: str,
    command_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = get_stage_tool_policy(stage)
    mutating = is_mutating_tool_name(tool_name)
    is_validation_command = bool((command_info or {}).get("is_test_command"))

    reason = ""
    decision = "allowed"
    status = "success"

    if mutating and not policy.get("allow_mutating_tools", True):
        if stage in {"planner", "reviewer"}:
            decision = "blocked"
            reason = f"{stage} stage is read-only"
            status = "error"
        else:
            decision = "blocked"
            reason = f"{stage} stage is read-only except validation commands"
            status = "error"
    elif is_validation_command and not policy.get("allow_validation_commands", True):
        decision = "warning" if stage == "builder" else "blocked"
        reason = f"{stage} stage does not run validation commands"
        status = "warning" if decision == "warning" else "error"

    return {
        "workflow_mode": workflow_mode,
        "stage": stage,
        "agent": agent,
        "tool": str(tool_name or ""),
        "policy": policy.get("mode", "default"),
        "allow_mutating_tools": bool(policy.get("allow_mutating_tools", True)),
        "allow_validation_commands": bool(policy.get("allow_validation_commands", True)),
        "is_mutating_tool": mutating,
        "is_validation_command": is_validation_command,
        "decision": decision,
        "reason": reason,
        "status": status,
    }


def emit_stage_tool_policy_selected(*, workflow_mode: str, stage: str, agent: str) -> None:
    payload = summarize_stage_tool_policy(
        {
            "workflow_mode": workflow_mode,
            "stage": stage,
            "agent": agent,
            **get_stage_tool_policy(stage),
            "decision": "selected",
            "reason": "",
            "tool": "",
        }
    )
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_stage_tool_policy_selected",
        title="Stage tool policy selected",
        preview=build_stage_tool_policy_preview(payload),
        payload=payload,
    )


def detect_skill_requests(text: str, available_skills: list[str]) -> dict[str, Any]:
    available_lookup = {skill.lower(): skill for skill in available_skills}
    requested: list[str] = []

    for match in re.findall(r"REQUEST_SKILL:\s*([A-Za-z0-9_-]+)", str(text or ""), flags=re.IGNORECASE):
        requested.append(match.strip())

    requested_section = re.search(
        r"##\s*Requested Skills(.*?)(?:\n##\s|\Z)",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if requested_section:
        for raw_line in requested_section.group(1).splitlines():
            line = raw_line.strip()
            match = re.match(r"[-*]\s*([A-Za-z0-9_-]+)", line)
            if match:
                requested.append(match.group(1).strip())

    deduped: list[str] = []
    for skill in requested:
        normalized = skill.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)

    approved = [available_lookup[item.lower()] for item in deduped if item.lower() in available_lookup]
    denied = [item for item in deduped if item.lower() not in available_lookup]

    return {
        "requested_skills": deduped,
        "approved_skills": approved,
        "denied_skills": denied,
    }


def load_requested_skill_instructions(skill_names: list[str]) -> str:
    available = {item["name"]: item for item in discover_skills() if item.get("name")}
    parts: list[str] = []

    for skill_name in normalize_string_list(skill_names):
        skill = available.get(skill_name)
        if not skill:
            continue
        skill_path = BASE_DIR / str(skill.get("path") or "").replace("/", "\\")
        try:
            content = skill_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not content.strip():
            continue
        parts.append(
            (
                f"[REQUESTED SKILL ACTIVE]\n"
                f"Skill name: {skill_name}\n"
                f"Skill path: {skill.get('path')}\n\n"
                f"{content}"
            ).strip()
        )

    return "\n\n---\n\n".join(parts).strip()


def emit_skill_request_events(
    *,
    workflow_mode: str,
    stage: str,
    agent: str,
    output_text: str,
    available_skills: list[str],
    runtime_state: dict[str, Any],
) -> None:
    skill_request = detect_skill_requests(output_text, available_skills)
    if not skill_request.get("requested_skills"):
        return

    request_payload = summarize_skill_request(
        {
            "workflow_mode": workflow_mode,
            "stage": stage,
            "agent": agent,
            "requested_skills": skill_request.get("requested_skills"),
            "approved_skills": skill_request.get("approved_skills"),
            "denied_skills": skill_request.get("denied_skills"),
            "source": "model_output",
        }
    )
    runtime_state["requested_skills"] = normalize_string_list(
        list(runtime_state.get("requested_skills") or []) + list(request_payload.get("requested_skills") or [])
    )
    runtime_state["approved_requested_skills"] = normalize_string_list(
        list(runtime_state.get("approved_requested_skills") or []) + list(request_payload.get("approved_skills") or [])
    )
    runtime_state["denied_requested_skills"] = normalize_string_list(
        list(runtime_state.get("denied_requested_skills") or []) + list(request_payload.get("denied_skills") or [])
    )
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_skill_requested",
        title="Skill requested",
        preview=build_skill_request_preview(request_payload),
        payload=request_payload,
    )
    for skill_name in request_payload.get("approved_skills") or []:
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_skill_request_approved",
            title="Skill request approved",
            preview=f"skill={skill_name} decision=approved",
            payload={"stage": stage, "skill": skill_name, "decision": "approved", "reason": "known skill"},
        )
    for skill_name in request_payload.get("denied_skills") or []:
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_skill_request_denied",
            title="Skill request denied",
            preview=f"skill={skill_name} decision=denied",
            payload={"stage": stage, "skill": skill_name, "decision": "denied", "reason": "skill not found"},
            status="warning",
        )


def suggest_validation_strategy(changed_files: list[str], project_root: Path) -> dict[str, Any]:
    normalized = normalize_string_list(changed_files)
    python_files = [path for path in normalized if path.lower().endswith(".py")]
    frontend_files = [
        path for path in normalized
        if path.replace("\\", "/").startswith("app-agent-console/")
        and any(path.lower().endswith(ext) for ext in [".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html"])
    ]
    review_only_files = bool(normalized) and all(
        path.lower().endswith(".md") or path.replace("\\", "/").startswith(".opencode/")
        for path in normalized
    )

    commands: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    strategy = "no_changes"

    if python_files:
        strategy = "python_backend"
        for path in python_files:
            commands.append(
                {
                    "command": f"python -m py_compile {path}",
                    "reason": "Python backend file changed",
                }
            )

    if frontend_files:
        strategy = "mixed_backend_frontend" if commands else "frontend"
        if (project_root / "app-agent-console" / "package.json").exists():
            commands.append(
                {
                    "command": "cd app-agent-console && cmd /c npm run build",
                    "reason": "Frontend files changed",
                }
            )
        else:
            skipped.append(
                {
                    "command": "cd app-agent-console && cmd /c npm run build",
                    "reason": "package.json not found",
                }
            )

    if review_only_files and not commands:
        strategy = "review_only"
        skipped.append(
            {
                "command": "",
                "reason": "Only docs or ADOS prompt files changed",
            }
        )

    if not normalized and not commands:
        strategy = "no_changes"
        skipped.append(
            {
                "command": "",
                "reason": "No changed files detected",
            }
        )

    seen_commands: set[str] = set()
    unique_commands: list[dict[str, str]] = []
    for item in commands:
        command = item["command"]
        if command in seen_commands:
            continue
        seen_commands.add(command)
        unique_commands.append(item)

    if commands and strategy == "python_backend" and frontend_files:
        strategy = "mixed_backend_frontend"

    return {
        "strategy": strategy,
        "auto_run": False,
        "commands": unique_commands,
        "commands_count": len(unique_commands),
        "skipped_commands": skipped,
        "changed_files_count": len(normalized),
    }


def extract_review_result(text: str, validation_result: str = "unknown") -> dict[str, Any]:
    lowered = str(text or "").lower()

    def detect_value(label: str, allowed: list[str], default: str = "unknown") -> str:
        match = re.search(rf"{re.escape(label.lower())}\s*:\s*([a-z_]+)", lowered)
        if match and match.group(1) in allowed:
            return match.group(1)
        return default

    parse_status = "parsed" if "## review" in lowered else ("partial" if lowered.strip() else "not_found")
    return {
        "parse_status": parse_status,
        "risk": detect_value("risk", ["low", "medium", "high"]),
        "commit_readiness": detect_value("commit readiness", ["ready", "not_ready", "needs_human_review"]),
        "scope_control": detect_value("scope control", ["ok", "warning", "failed"]),
        "validation_confidence": detect_value(
            "validation confidence",
            ["verified", "unverified", "failed"],
            default="unverified" if validation_result == "not_run" else "unknown",
        ),
    }


def build_artifact_summary_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# ADOS Run Summary",
        "",
        f"Run: {manifest.get('run_id', '')}",
        f"Workflow: {manifest.get('workflow_mode', '')}",
        f"Status: {manifest.get('status', '')}",
        f"Validation: {manifest.get('validation_result', '')}",
        "",
        "## Stages",
        "",
        "| Stage | Agent | Status | Tools | Output |",
        "|---|---|---|---|---|",
    ]

    for stage in manifest.get("stages") or []:
        tool_names = ", ".join(stage.get("tools") or []) or "-"
        lines.append(
            f"| {stage.get('stage', '')} | {stage.get('agent', '')} | {stage.get('status', '')} | {tool_names} | {stage.get('output_length', 0)} |"
        )

    lines.extend(["", "## Changed Files", ""])
    changed_files = manifest.get("changed_files") or []
    if changed_files:
        lines.extend([f"- {path}" for path in changed_files])
    else:
        lines.append("- (none)")

    lines.extend(["", "## Suggested Validation", ""])
    suggested_commands = manifest.get("suggested_validation_commands") or []
    if suggested_commands:
        lines.extend(["```powershell", *suggested_commands, "```"])
    else:
        lines.append("No command suggested.")

    lines.extend(["", "## Warnings", ""])
    warnings = manifest.get("warnings") or []
    if warnings:
        lines.extend([f"- {warning}" for warning in warnings])
    else:
        lines.append("- (none)")

    return "\n".join(lines).strip() + "\n"


def write_ados_run_artifacts(
    run_id: str,
    workflow_state: dict[str, Any],
    outputs: dict[str, str],
    root: Path,
) -> dict[str, Any]:
    runs_root = root / ".opencode" / "runs"
    run_dir = runs_root / run_id
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    stage_statuses = dict(workflow_state.get("stage_statuses") or {})
    stage_agents = dict(workflow_state.get("stage_agents") or {})
    stage_tools = dict(workflow_state.get("stage_tool_names") or {})
    changed_files = normalize_string_list(workflow_state.get("changed_files") or [])
    suggested_validation_commands = normalize_string_list(workflow_state.get("suggested_validation_commands") or [])
    artifacts: dict[str, str] = {}
    files_written: list[str] = []

    for stage_name, filename in [("planner", "plan.md"), ("builder", "build.md"), ("verifier", "verify.md"), ("reviewer", "review.md")]:
        output_text = str(outputs.get(stage_name) or "").strip()
        if not output_text:
            continue
        path = run_dir / filename
        path.write_text(output_text + "\n", encoding="utf-8")
        artifacts[stage_name] = filename
        files_written.append(filename)

    diff_preview = str(workflow_state.get("diff_preview") or "")
    if diff_preview:
        diff_name = "diff_preview.txt"
        (run_dir / diff_name).write_text(diff_preview, encoding="utf-8")
        artifacts["diff_preview"] = diff_name
        files_written.append(diff_name)

    manifest = {
        "run_id": run_id,
        "workflow_mode": workflow_state.get("workflow_mode", ""),
        "started_at": workflow_state.get("started_at", ""),
        "finished_at": workflow_state.get("finished_at", ""),
        "status": workflow_state.get("workflow_status") or workflow_state.get("final_status") or "unknown",
        "stages": [
            {
                "stage": stage,
                "agent": stage_agents.get(stage, ""),
                "status": stage_statuses.get(stage, ""),
                "output_length": len(str(outputs.get(stage) or "")),
                "tools": stage_tools.get(stage, []),
            }
            for stage in workflow_state.get("workflow_info", {}).get("stages", [])
            if stage in outputs or stage in stage_statuses
        ],
        "changed_files": changed_files,
        "validation_result": workflow_state.get("validation_result", "unknown"),
        "suggested_validation_commands": suggested_validation_commands,
        "warnings": list(workflow_state.get("warnings") or []),
        "artifacts": artifacts,
    }

    manifest_name = "run_manifest.json"
    (run_dir / manifest_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    files_written.append(manifest_name)
    artifacts["manifest"] = manifest_name

    summary_name = "summary.md"
    (run_dir / summary_name).write_text(build_artifact_summary_markdown(manifest), encoding="utf-8")
    files_written.append(summary_name)
    artifacts["summary"] = summary_name

    manifest["artifacts"] = artifacts
    (run_dir / manifest_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_id": run_id,
        "directory": str(run_dir.relative_to(root)).replace("\\", "/"),
        "files": files_written,
        "files_count": len(files_written),
    }


def merge_runtime_state(shared_state: dict[str, Any], stage_state: dict[str, Any]) -> None:
    shared_state.setdefault("commands", []).extend(list(stage_state.get("commands") or []))
    shared_state.setdefault("tool_events", []).extend(list(stage_state.get("tool_events") or []))
    shared_state["tool_calls_count"] = int(shared_state.get("tool_calls_count") or 0) + int(stage_state.get("tool_calls_count") or 0)
    shared_state["mcp_internal_tool_calls"] = int(shared_state.get("mcp_internal_tool_calls") or 0) + int(stage_state.get("mcp_internal_tool_calls") or 0)
    shared_state["mcp_external_tool_calls"] = int(shared_state.get("mcp_external_tool_calls") or 0) + int(stage_state.get("mcp_external_tool_calls") or 0)

    existing_skills = list(shared_state.get("loaded_skills") or [])
    for skill in list(stage_state.get("loaded_skills") or []):
        if skill not in existing_skills:
            existing_skills.append(skill)
    shared_state["loaded_skills"] = existing_skills

    existing_warnings = list(shared_state.get("warnings") or [])
    for warning in list(stage_state.get("warnings") or []):
        if warning not in existing_warnings:
            existing_warnings.append(warning)
    shared_state["warnings"] = existing_warnings


def emit_plan_build_verify_workflow_started(workflow_info: dict[str, Any]) -> None:
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_workflow_started",
        title="ADOS workflow started",
        preview="workflow=plan_build_verify agent=ados-workflow stage=planner",
        payload=workflow_info,
    )


def emit_pbv_stage_failure_policy_applied(
    *,
    workflow_info: dict[str, Any],
    stage: str,
    stage_status: str,
    changed_files_count: int,
    successful_write_tools_count: int,
    decision: str,
    reason: str,
) -> None:
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_stage_failure_policy_applied",
        title="ADOS stage failure policy applied",
        preview=f"stage={stage} decision={decision} reason={reason}",
        payload={
            "workflow_mode": workflow_info.get("workflow_mode"),
            "workflow_id": workflow_info.get("workflow_id"),
            "stage": stage,
            "stage_status": stage_status,
            "changed_files_count": changed_files_count,
            "successful_write_tools_count": successful_write_tools_count,
            "decision": decision,
            "reason": reason,
        },
    )


def emit_plan_build_verify_stage_started(workflow_info: dict[str, Any], stage: str, agent: str) -> None:
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_stage_started",
        title="ADOS stage started",
        preview=f"stage={stage} agent={agent}",
        payload={
            "workflow_id": workflow_info.get("workflow_id"),
            "workflow_mode": workflow_info.get("workflow_mode"),
            "selected_agent": agent,
            "workflow_selected_agent": workflow_info.get("selected_agent"),
            "stage": stage,
            "agent": agent,
        },
    )
    emit_stage_tool_policy_selected(
        workflow_mode=str(workflow_info.get("workflow_mode") or ""),
        stage=stage,
        agent=agent,
    )


def emit_plan_build_verify_stage_finished(
    workflow_info: dict[str, Any],
    *,
    stage: str,
    agent: str,
    status: str,
    output_text: str = "",
) -> None:
    payload = {
        "workflow_id": workflow_info.get("workflow_id"),
        "workflow_mode": workflow_info.get("workflow_mode"),
        "selected_agent": agent,
        "workflow_selected_agent": workflow_info.get("selected_agent"),
        "stage": stage,
        "agent": agent,
        "status": status,
    }
    if output_text:
        payload.update(build_text_debug_fields(f"{stage}_output", output_text, limit=1000))

    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_stage_finished",
        title="ADOS stage finished",
        preview=f"stage={stage} status={status}",
        payload=payload,
        status="error" if status == "error" else "success",
    )


def emit_pbv_stage_handoff_created(payload: dict[str, Any]) -> None:
    payload = summarize_stage_handoff(payload)
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_stage_handoff_created",
        title="ADOS stage handoff created",
        preview=build_stage_handoff_preview(payload),
        payload=payload,
    )


def emit_plan_build_verify_workflow_completed(
    workflow_info: dict[str, Any],
    workflow_summary: dict[str, Any],
    *,
    duration_ms: int,
) -> None:
    completed_stages = list(workflow_summary.get("completed_stages") or [])
    failed_stages = list(workflow_summary.get("failed_stages") or [])
    skipped_stages = list(workflow_summary.get("skipped_stages") or [])
    status = str(workflow_summary.get("workflow_status") or workflow_summary.get("final_status") or "unknown")

    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_workflow_completed",
        title="ADOS workflow completed",
        preview=(
            f"workflow={workflow_info.get('workflow_mode')} "
            f"status={status} "
            f"completed={','.join(completed_stages) or 'none'} "
            f"skipped={len(skipped_stages)}"
        ),
        payload={
            "workflow_id": workflow_info.get("workflow_id"),
            "workflow_mode": workflow_info.get("workflow_mode"),
            "selected_agent": workflow_info.get("selected_agent"),
            "active_stage": workflow_summary.get("active_stage", ""),
            "completed_stages": completed_stages,
            "failed_stages": failed_stages,
            "skipped_stages": skipped_stages,
            "status": status,
            "duration_ms": duration_ms,
        },
        status="error" if status == "error" else "success",
        duration_ms=duration_ms,
    )


async def run_plan_build_verify_workflow(
    req: ChatCompletionRequest,
    runtime_state: dict[str, Any],
    git_diff_baseline: dict[str, Any],
    workflow_mode: str = "plan_build_verify",
) -> dict[str, Any]:
    workflow_info = build_plan_build_verify_workflow_info(workflow_mode)
    runtime_state["workflow_mode"] = workflow_info["workflow_mode"]
    runtime_state["selected_agent"] = workflow_info["selected_agent"]
    runtime_state["active_stage"] = "planner"
    runtime_state["workflow_info"] = workflow_info
    runtime_state["completed_stages"] = []
    runtime_state["failed_stages"] = []
    runtime_state["skipped_stages"] = []
    runtime_state["workflow_status"] = "unknown"
    runtime_state["warnings"] = []
    runtime_state["verifier_guard_result"] = "not_applicable"
    runtime_state["stage_statuses"] = {}
    runtime_state.setdefault("tool_events", [])
    runtime_state["structured_plan"] = {}
    runtime_state["build_result"] = {}
    runtime_state["build_plan_compare"] = {}
    runtime_state["verification_result"] = {}
    runtime_state["file_scope_check"] = {}
    runtime_state["review_result"] = {}
    runtime_state["requested_skills"] = []
    runtime_state["approved_requested_skills"] = []
    runtime_state["denied_requested_skills"] = []
    runtime_state["suggested_validation_commands"] = []
    runtime_state["validation_strategy"] = ""
    runtime_state["stage_outputs"] = {}
    runtime_state["stage_agents"] = {}
    runtime_state["stage_tool_names"] = {}

    emit_plan_build_verify_workflow_started(workflow_info)

    planner_instruction = (
        "You are the planner stage.\n"
        "Do not edit files.\n"
        "Do not run destructive actions.\n"
        "Return a concise plan and include a section titled '## Structured Plan'.\n"
        "Inside that section, include one fenced JSON block with keys:\n"
        "- goal\n"
        "- scope.allowed_files\n"
        "- scope.forbidden_files\n"
        "- steps\n"
        "- validation\n"
        "- risks\n"
        "Keep the JSON valid and concise."
    )
    builder_instruction = (
        "You are the builder stage.\n"
        "Use the planner output as guidance.\n"
        "Implement only the required file changes.\n"
        "Keep changes small and reviewable.\n"
        "Do not claim validation passed unless a real validation command ran.\n"
        "At the end, include '## Build Result' with one fenced JSON block containing:\n"
        "- completed_steps\n"
        "- skipped_steps\n"
        "- failed_steps\n"
        "- changed_files\n"
        "- notes\n"
        "If JSON is not practical, include '## Step Results' bullet lines like '- step-id: completed'."
    )
    verifier_instruction = (
        "You are the verifier stage.\n"
        "Review the planner output, builder output, structured plan summary, build result summary, changed files, diff summary, and validation summary.\n"
        "Do not modify files unless explicitly necessary to fix a verification-only issue.\n"
        "Never claim tests passed unless an actual command/test event exists with exit_code=0.\n"
        "If no validation command ran, report validation_result=not_run.\n"
        "Return sections titled:\n"
        "- ## Plan Compliance\n"
        "- ## File Scope\n"
        "- ## Validation\n"
        "State whether planned steps were completed, whether only allowed files were touched, and what remains unverified."
    )
    reviewer_instruction = (
        "You are the reviewer stage.\n"
        "You are read-only and must not modify files.\n"
        "Review planner, builder, verifier, changed files, diff preview, warnings, and suggested validation commands.\n"
        "Return a section titled '## Review' containing:\n"
        "- Scope control: ok | warning | failed\n"
        "- Risk: low | medium | high\n"
        "- Validation confidence: verified | unverified | failed\n"
        "- Commit readiness: ready | not_ready | needs_human_review\n"
        "- Follow-up suggestions"
    )

    planner_output = ""
    builder_output = ""
    verifier_output = ""
    reviewer_output = ""
    available_skills = [item.get("name") for item in discover_skills() if item.get("name")]

    emit_plan_build_verify_stage_started(workflow_info, "planner", "ados-planner")
    runtime_state["stage_agents"]["planner"] = "ados-planner"
    planner_state = {"commands": [], "loaded_skills": [], "tool_calls_count": 0, "mcp_internal_tool_calls": 0, "mcp_external_tool_calls": 0, "tool_events": [], "warnings": []}
    planner_result = await run_chat_with_mcp_loop(
        build_stage_request(req, stage_agent="ados-planner", stage_instruction=planner_instruction),
        runtime_state=planner_state,
        emit_workflow_events=False,
        selected_agent_override="ados-planner",
    )
    merge_runtime_state(runtime_state, planner_state)

    if isinstance(planner_result, dict) and "error" in planner_result:
        runtime_state["failed_stages"] = ["planner"]
        runtime_state["skipped_stages"] = ["builder", "verifier"]
        runtime_state["active_stage"] = "planner"
        runtime_state["stage_statuses"]["planner"] = "error"
        runtime_state["workflow_status"] = "error"
        planner_output = ""
        emit_plan_build_verify_stage_finished(
            workflow_info,
            stage="planner",
            agent="ados-planner",
            status="error",
            output_text=str(planner_result.get("error")),
        )
        runtime_state["stage_outputs"]["planner"] = planner_output
        runtime_state["stage_tool_names"]["planner"] = normalize_string_list(
            [item.get("tool_name") for item in planner_state.get("tool_events") or []]
        )
        structured_plan = summarize_structured_plan(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "planner",
                "agent": "ados-planner",
                "parse_status": "error",
                "goal": "",
                "steps": [],
                "steps_count": 0,
                "allowed_files": [],
                "allowed_files_count": 0,
                "forbidden_files": [],
                "forbidden_files_count": 0,
                "validation_commands": [],
                "validation_commands_count": 0,
                "risks": [],
                "risks_count": 0,
                "raw_preview": "",
            }
        )
        runtime_state["structured_plan"] = structured_plan
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_plan_created",
            title="ADOS structured plan created",
            preview=build_plan_created_preview(structured_plan),
            payload=structured_plan,
        )
        return planner_result

    planner_output, planner_tool_calls = extract_response_content_and_tool_calls(planner_result)
    planner_status = "partial" if planner_tool_calls else "completed"
    runtime_state["stage_statuses"]["planner"] = planner_status
    if planner_status == "completed":
        runtime_state["completed_stages"].append("planner")
    emit_plan_build_verify_stage_finished(
        workflow_info,
        stage="planner",
        agent="ados-planner",
        status=planner_status,
        output_text=planner_output,
    )
    runtime_state["stage_outputs"]["planner"] = planner_output
    runtime_state["stage_tool_names"]["planner"] = normalize_string_list(
        [item.get("tool_name") for item in planner_state.get("tool_events") or []]
    )
    structured_plan = summarize_structured_plan(
        {
            "workflow_mode": workflow_info.get("workflow_mode"),
            "stage": "planner",
            "agent": "ados-planner",
            **extract_structured_plan(planner_output),
        }
    )
    runtime_state["structured_plan"] = structured_plan
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_plan_created",
        title="ADOS structured plan created",
        preview=build_plan_created_preview(structured_plan),
        payload=structured_plan,
    )
    emit_skill_request_events(
        workflow_mode=str(workflow_info.get("workflow_mode") or ""),
        stage="planner",
        agent="ados-planner",
        output_text=planner_output,
        available_skills=available_skills,
        runtime_state=runtime_state,
    )
    if planner_status == "completed":
        emit_pbv_stage_handoff_created(
            build_handoff_payload(
                workflow_info=workflow_info,
                from_stage="planner",
                to_stage="builder",
                from_agent="ados-planner",
                to_agent="ados-builder",
                from_status=planner_status,
                to_status="ready",
                output_text=planner_output,
                plan=structured_plan,
                validation_result="not_run",
            )
        )

    if planner_status != "completed":
        planner_policy = decide_pbv_stage_failure_policy(
            stage="planner",
            stage_status=planner_status,
            output_text=planner_output,
        )
        runtime_state["workflow_status"] = planner_policy.get("workflow_status", runtime_state.get("workflow_status", "unknown"))
        emit_pbv_stage_failure_policy_applied(
            workflow_info=workflow_info,
            stage="planner",
            stage_status=planner_status,
            changed_files_count=0,
            successful_write_tools_count=0,
            decision=planner_policy["decision"],
            reason=planner_policy["reason"],
        )
        if planner_policy.get("continue_to_next_stage"):
            emit_pbv_stage_handoff_created(
                build_handoff_payload(
                    workflow_info=workflow_info,
                    from_stage="planner",
                    to_stage="builder",
                    from_agent="ados-planner",
                    to_agent="ados-builder",
                    from_status=planner_status,
                    to_status="ready",
                    output_text=planner_output,
                    plan=structured_plan,
                    validation_result="not_run",
                )
            )
        if not planner_policy.get("continue_to_next_stage"):
            runtime_state["skipped_stages"] = ["builder", "verifier"]
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_ados_stage_skipped",
                title="ADOS stage skipped",
                preview="stage=builder reason=planner_unusable_or_failed",
                payload={
                    "workflow_id": workflow_info.get("workflow_id"),
                    "workflow_mode": workflow_info.get("workflow_mode"),
                    "selected_agent": workflow_info.get("selected_agent"),
                    "stage": "builder",
                    "reason": planner_policy["reason"],
                },
            )
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_ados_stage_skipped",
                title="ADOS stage skipped",
                preview="stage=verifier reason=planner_unusable_or_failed",
                payload={
                    "workflow_id": workflow_info.get("workflow_id"),
                    "workflow_mode": workflow_info.get("workflow_mode"),
                    "selected_agent": workflow_info.get("selected_agent"),
                    "stage": "verifier",
                    "reason": planner_policy["reason"],
                },
            )
            final_content = (
                "## Plan\n"
                f"{planner_output or '(no planner output)'}\n\n"
                "## Build\n"
                "(skipped)\n\n"
                "## Verify\n"
                "(skipped)\n\n"
                "## Final Status\n"
                "error\n\n"
                "Planner did not produce enough usable output to continue safely."
            )
            return make_chat_response(
                req=req,
                content=final_content,
                tool_calls=None,
                prompt_text=final_content,
                raw_output=final_content,
            )

    runtime_state["active_stage"] = "builder"
    emit_plan_build_verify_stage_started(workflow_info, "builder", "ados-builder")
    runtime_state["stage_agents"]["builder"] = "ados-builder"
    builder_state = {"commands": [], "loaded_skills": [], "tool_calls_count": 0, "mcp_internal_tool_calls": 0, "mcp_external_tool_calls": 0, "tool_events": [], "warnings": []}
    builder_result = await run_chat_with_mcp_loop(
        build_stage_request(
            req,
            stage_agent="ados-builder",
            stage_instruction=builder_instruction,
            context_blocks=[
                f"Planner output:\n{planner_output}",
                "Structured plan summary:\n"
                + json.dumps(
                    {
                        "goal": structured_plan.get("goal"),
                        "steps": structured_plan.get("steps") or [],
                        "allowed_files": structured_plan.get("allowed_files") or [],
                        "forbidden_files": structured_plan.get("forbidden_files") or [],
                        "validation_commands": structured_plan.get("validation_commands") or [],
                        "parse_status": structured_plan.get("parse_status"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                load_requested_skill_instructions(runtime_state.get("approved_requested_skills") or []),
            ],
        ),
        runtime_state=builder_state,
        emit_workflow_events=False,
        selected_agent_override="ados-builder",
    )
    merge_runtime_state(runtime_state, builder_state)

    git_diff_after_builder = capture_git_diff_snapshot()
    builder_delta = build_git_diff_delta(git_diff_baseline, git_diff_after_builder)
    builder_mutating_tools = detect_successful_mutating_tools(builder_state.get("tool_events"))
    successful_mutating_tools_count = int(builder_mutating_tools.get("successful_mutating_tools_count") or 0)
    build_result_summary = summarize_build_result_capture(
        {
            "workflow_mode": workflow_info.get("workflow_mode"),
            "stage": "builder",
            "agent": "ados-builder",
            **extract_build_result("", structured_plan),
            "changed_files": builder_delta.get("changed_files") or [],
            "changed_files_count": len(builder_delta.get("changed_files") or []),
        }
    )
    build_steps_summary = summarize_build_steps_detected(
        {
            "workflow_mode": workflow_info.get("workflow_mode"),
            "stage": "builder",
            "agent": "ados-builder",
            **compare_build_to_plan(structured_plan, build_result_summary),
        }
    )

    if isinstance(builder_result, dict) and "error" in builder_result:
        builder_status = "failed" if builder_delta.get("changed_files") else "error"
        runtime_state["failed_stages"].append("builder")
        runtime_state["active_stage"] = "builder"
        runtime_state["stage_statuses"]["builder"] = builder_status
        emit_plan_build_verify_stage_finished(
            workflow_info,
            stage="builder",
            agent="ados-builder",
            status=builder_status,
            output_text=str(builder_result.get("error")),
        )
        build_result_summary = summarize_build_result_capture(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "builder",
                "agent": "ados-builder",
                **extract_build_result(str(builder_result.get("error") or ""), structured_plan),
                "changed_files": builder_delta.get("changed_files") or [],
                "changed_files_count": len(builder_delta.get("changed_files") or []),
            }
        )
        build_steps_summary = summarize_build_steps_detected(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "builder",
                "agent": "ados-builder",
                **compare_build_to_plan(structured_plan, build_result_summary),
            }
        )
        runtime_state["build_result"] = build_result_summary
        runtime_state["build_plan_compare"] = build_steps_summary
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_build_result_captured",
            title="ADOS build result captured",
            preview=build_build_result_preview(build_result_summary),
            payload=build_result_summary,
        )
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_build_steps_detected",
            title="ADOS build steps detected",
            preview=(
                f"build steps planned={build_steps_summary.get('planned_steps_count', 0)} "
                f"completed={build_steps_summary.get('completed_steps_count', 0)} "
                f"missing={build_steps_summary.get('missing_steps_count', 0)}"
            ),
            payload=build_steps_summary,
        )
        builder_validation_snapshot = build_validation_summary_payload(runtime_state)
        builder_policy = decide_pbv_stage_failure_policy(
            stage="builder",
            stage_status=builder_status,
            output_text="",
            changed_files_count=len(builder_delta.get("changed_files") or []),
            successful_mutating_tools_count=successful_mutating_tools_count,
        )
        runtime_state["workflow_status"] = builder_policy.get("workflow_status", builder_status)
        emit_pbv_stage_failure_policy_applied(
            workflow_info=workflow_info,
            stage="builder",
            stage_status=builder_status,
            changed_files_count=len(builder_delta.get("changed_files") or []),
            successful_write_tools_count=successful_mutating_tools_count,
            decision=builder_policy["decision"],
            reason=builder_policy["reason"],
        )
        if builder_policy.get("continue_to_next_stage"):
            emit_pbv_stage_handoff_created(
                build_handoff_payload(
                    workflow_info=workflow_info,
                    from_stage="builder",
                    to_stage="verifier",
                    from_agent="ados-builder",
                    to_agent="ados-verifier",
                    from_status=builder_status,
                    to_status="ready",
                    output_text=str(builder_result.get("error") or ""),
                    plan=structured_plan,
                    build_result=build_result_summary,
                    changed_files=builder_delta.get("changed_files") or [],
                    diff_preview=str(builder_delta.get("diff_preview") or ""),
                    validation_result=str(builder_validation_snapshot.get("validation_result") or "not_run"),
                )
            )
        if not builder_policy.get("continue_to_next_stage"):
            runtime_state["skipped_stages"] = ["verifier"]
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_ados_stage_skipped",
                title="ADOS stage skipped",
                preview=f"stage=verifier reason={builder_policy['reason']}",
                payload={
                    "workflow_id": workflow_info.get("workflow_id"),
                    "workflow_mode": workflow_info.get("workflow_mode"),
                    "selected_agent": workflow_info.get("selected_agent"),
                    "stage": "verifier",
                    "reason": builder_policy["reason"],
                },
            )
            final_content = (
                "## Plan\n"
                f"{planner_output or '(no planner output)'}\n\n"
                "## Build\n"
                f"{str(builder_result.get('error') or '(builder failed)')}\n\n"
                "## Verify\n"
                "(skipped)\n\n"
                "## Final Status\n"
                f"{runtime_state.get('workflow_status') or 'error'}"
            )
            return make_chat_response(
                req=req,
                content=final_content,
                tool_calls=None,
                prompt_text=final_content,
                raw_output=final_content,
            )

    if isinstance(builder_result, dict) and "error" in builder_result:
        builder_output = str(builder_result.get("error") or "(builder failed)")
        builder_validation_snapshot = build_validation_summary_payload(runtime_state)
    else:
        builder_output, builder_tool_calls = extract_response_content_and_tool_calls(builder_result)
        builder_status = "partial" if builder_tool_calls else "completed"
        runtime_state["stage_statuses"]["builder"] = builder_status
        if builder_status == "completed":
            runtime_state["completed_stages"].append("builder")
        emit_plan_build_verify_stage_finished(
            workflow_info,
            stage="builder",
            agent="ados-builder",
            status=builder_status,
            output_text=builder_output,
        )
        build_result_summary = summarize_build_result_capture(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "builder",
                "agent": "ados-builder",
                **extract_build_result(builder_output, structured_plan),
                "changed_files": builder_delta.get("changed_files") or [],
                "changed_files_count": len(builder_delta.get("changed_files") or []),
            }
        )
        build_steps_summary = summarize_build_steps_detected(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "builder",
                "agent": "ados-builder",
                **compare_build_to_plan(structured_plan, build_result_summary),
            }
        )
        runtime_state["build_result"] = build_result_summary
        runtime_state["build_plan_compare"] = build_steps_summary
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_build_result_captured",
            title="ADOS build result captured",
            preview=build_build_result_preview(build_result_summary),
            payload=build_result_summary,
        )
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_build_steps_detected",
            title="ADOS build steps detected",
            preview=(
                f"build steps planned={build_steps_summary.get('planned_steps_count', 0)} "
                f"completed={build_steps_summary.get('completed_steps_count', 0)} "
                f"missing={build_steps_summary.get('missing_steps_count', 0)}"
            ),
            payload=build_steps_summary,
        )
        builder_validation_snapshot = build_validation_summary_payload(runtime_state)

        if builder_status != "completed":
            builder_policy = decide_pbv_stage_failure_policy(
                stage="builder",
                stage_status=builder_status,
                output_text=builder_output,
                changed_files_count=len(builder_delta.get("changed_files") or []),
                successful_mutating_tools_count=successful_mutating_tools_count,
            )
            runtime_state["workflow_status"] = builder_policy.get("workflow_status", runtime_state.get("workflow_status", "unknown"))
            emit_pbv_stage_failure_policy_applied(
                workflow_info=workflow_info,
                stage="builder",
                stage_status=builder_status,
                changed_files_count=len(builder_delta.get("changed_files") or []),
                successful_write_tools_count=successful_mutating_tools_count,
                decision=builder_policy["decision"],
                reason=builder_policy["reason"],
            )
            if builder_policy.get("continue_to_next_stage"):
                emit_pbv_stage_handoff_created(
                    build_handoff_payload(
                        workflow_info=workflow_info,
                        from_stage="builder",
                        to_stage="verifier",
                        from_agent="ados-builder",
                        to_agent="ados-verifier",
                        from_status=builder_status,
                        to_status="ready",
                        output_text=builder_output,
                        plan=structured_plan,
                        build_result=build_result_summary,
                        changed_files=builder_delta.get("changed_files") or [],
                        diff_preview=str(builder_delta.get("diff_preview") or ""),
                        validation_result=str(builder_validation_snapshot.get("validation_result") or "not_run"),
                    )
                )
            if not builder_policy.get("continue_to_next_stage"):
                runtime_state["skipped_stages"] = ["verifier"]
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_ados_stage_skipped",
                    title="ADOS stage skipped",
                    preview=f"stage=verifier reason={builder_policy['reason']}",
                    payload={
                        "workflow_id": workflow_info.get("workflow_id"),
                        "workflow_mode": workflow_info.get("workflow_mode"),
                        "selected_agent": workflow_info.get("selected_agent"),
                        "stage": "verifier",
                        "reason": builder_policy["reason"],
                    },
                )
                final_content = (
                    "## Plan\n"
                    f"{planner_output or '(no planner output)'}\n\n"
                    "## Build\n"
                    f"{builder_output or '(no builder output)'}\n\n"
                    "## Verify\n"
                    "(skipped)\n\n"
                    "## Final Status\n"
                    f"{runtime_state.get('workflow_status') or 'partial'}"
                )
                return make_chat_response(
                    req=req,
                    content=final_content,
                    tool_calls=None,
                    prompt_text=final_content,
                    raw_output=final_content,
                )

    runtime_state["stage_outputs"]["builder"] = builder_output
    runtime_state["stage_tool_names"]["builder"] = normalize_string_list(
        [item.get("tool_name") for item in builder_state.get("tool_events") or []]
    )
    emit_skill_request_events(
        workflow_mode=str(workflow_info.get("workflow_mode") or ""),
        stage="builder",
        agent="ados-builder",
        output_text=builder_output,
        available_skills=available_skills,
        runtime_state=runtime_state,
    )

    validation_strategy = summarize_validation_strategy(
        suggest_validation_strategy(
            list(builder_delta.get("changed_files") or []) + list(builder_delta.get("untracked_files") or []),
            BASE_DIR,
        )
    )
    runtime_state["validation_strategy"] = validation_strategy.get("strategy", "")
    runtime_state["suggested_validation_commands"] = [
        item.get("command", "")
        for item in validation_strategy.get("commands") or []
        if item.get("command")
    ]
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_validation_strategy_selected",
        title="Validation strategy selected",
        preview=build_validation_strategy_preview(validation_strategy),
        payload=validation_strategy,
    )
    for command in validation_strategy.get("commands") or []:
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_validation_command_suggested",
            title="Validation command suggested",
            preview=f"command={command.get('command', '')[:120]}",
            payload={
                "command": command.get("command", ""),
                "reason": command.get("reason", ""),
                "auto_run": False,
            },
        )
    for skipped in validation_strategy.get("skipped_commands") or []:
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_validation_command_skipped",
            title="Validation command skipped",
            preview=f"reason={skipped.get('reason', '')[:120]}",
            payload={
                "command": skipped.get("command", ""),
                "reason": skipped.get("reason", ""),
                "auto_run": False,
                "strategy": validation_strategy.get("strategy", ""),
            },
        )

    if builder_status == "completed":
        emit_pbv_stage_handoff_created(
            build_handoff_payload(
                workflow_info=workflow_info,
                from_stage="builder",
                to_stage="verifier",
                from_agent="ados-builder",
                to_agent="ados-verifier",
                from_status=builder_status,
                to_status="ready",
                output_text=builder_output,
                plan=structured_plan,
                build_result=build_result_summary,
                changed_files=builder_delta.get("changed_files") or [],
                diff_preview=str(builder_delta.get("diff_preview") or ""),
                validation_result=str(builder_validation_snapshot.get("validation_result") or "not_run"),
            )
        )
    validation_snapshot = builder_validation_snapshot
    runtime_state["active_stage"] = "verifier"
    emit_plan_build_verify_stage_started(workflow_info, "verifier", "ados-verifier")
    runtime_state["stage_agents"]["verifier"] = "ados-verifier"
    verifier_state = {"commands": [], "loaded_skills": [], "tool_calls_count": 0, "mcp_internal_tool_calls": 0, "mcp_external_tool_calls": 0, "tool_events": [], "warnings": []}
    verifier_context = [
        f"Planner output:\n{planner_output}",
        f"Builder output:\n{builder_output or '(no textual builder output)'}",
        load_requested_skill_instructions(runtime_state.get("approved_requested_skills") or []),
        "Structured plan summary:\n"
        + json.dumps(
            {
                "goal": structured_plan.get("goal"),
                "steps": structured_plan.get("steps") or [],
                "allowed_files": structured_plan.get("allowed_files") or [],
                "forbidden_files": structured_plan.get("forbidden_files") or [],
                "validation_commands": structured_plan.get("validation_commands") or [],
                "parse_status": structured_plan.get("parse_status"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "Build result summary:\n"
        + json.dumps(
            {
                "parse_status": build_result_summary.get("parse_status"),
                "completed_steps": build_result_summary.get("completed_steps") or [],
                "skipped_steps": build_result_summary.get("skipped_steps") or [],
                "failed_steps": build_result_summary.get("failed_steps") or [],
                "changed_files": build_result_summary.get("changed_files") or [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "Changed files summary:\n"
        + json.dumps(
            {
                "changed_files": builder_delta.get("changed_files") or [],
                "untracked_files": builder_delta.get("untracked_files") or [],
                "diff_preview": str(builder_delta.get('diff_preview') or '')[:2000],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "Validation summary:\n" + json.dumps(validation_snapshot, ensure_ascii=False, indent=2),
        "Suggested validation commands:\n"
        + json.dumps(runtime_state.get("suggested_validation_commands") or [], ensure_ascii=False, indent=2),
    ]
    verifier_result = await run_chat_with_mcp_loop(
        build_stage_request(
            req,
            stage_agent="ados-verifier",
            stage_instruction=verifier_instruction,
            context_blocks=verifier_context,
        ),
        runtime_state=verifier_state,
        emit_workflow_events=False,
        selected_agent_override="ados-verifier",
    )
    merge_runtime_state(runtime_state, verifier_state)

    if isinstance(verifier_result, dict) and "error" in verifier_result:
        verifier_status = "partial" if builder_delta.get("changed_files") else "error"
        runtime_state["failed_stages"].append("verifier")
        runtime_state["active_stage"] = "verifier"
        runtime_state["stage_statuses"]["verifier"] = verifier_status
        runtime_state["workflow_status"] = verifier_status
        verifier_output = ""
        emit_plan_build_verify_stage_finished(
            workflow_info,
            stage="verifier",
            agent="ados-verifier",
            status=verifier_status,
            output_text=str(verifier_result.get("error")),
        )
        runtime_state["stage_outputs"]["verifier"] = verifier_output
        runtime_state["stage_tool_names"]["verifier"] = normalize_string_list(
            [item.get("tool_name") for item in verifier_state.get("tool_events") or []]
        )
        verification_result_summary = summarize_verification_result(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "verifier",
                "agent": "ados-verifier",
                "parse_status": "error",
                "plan_compliance_status": "unknown",
                "completed_steps_count": int((build_steps_summary or {}).get("completed_steps_count") or 0),
                "missing_steps_count": int((build_steps_summary or {}).get("missing_steps_count") or 0),
                "unexpected_files": [],
                "unexpected_files_count": 0,
                "validation_result": str(validation_snapshot.get("validation_result") or "unknown"),
                "caveats": ["Verifier stage failed before producing a structured result."],
            }
        )
        plan_compliance_summary = summarize_plan_compliance_check(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "verifier",
                "agent": "ados-verifier",
                "plan_compliance_status": "unknown",
                "planned_steps_count": int((build_steps_summary or {}).get("planned_steps_count") or 0),
                "completed_steps_count": int((build_steps_summary or {}).get("completed_steps_count") or 0),
                "missing_steps": list((build_steps_summary or {}).get("missing_steps") or []),
                "missing_steps_count": int((build_steps_summary or {}).get("missing_steps_count") or 0),
                "unexpected_files": [],
                "unexpected_files_count": 0,
                "forbidden_files_touched": [],
                "forbidden_files_touched_count": 0,
                "validation_result": str(validation_snapshot.get("validation_result") or "unknown"),
                "caveats": ["Verifier stage failed before producing a structured result."],
            }
        )
        runtime_state["verification_result"] = verification_result_summary
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_verification_result_captured",
            title="ADOS verification result captured",
            preview=build_plan_compliance_preview(
                {
                    **verification_result_summary,
                    "planned_steps_count": plan_compliance_summary.get("planned_steps_count"),
                }
            ),
            payload=verification_result_summary,
        )
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_plan_compliance_checked",
            title="ADOS plan compliance checked",
            preview=build_plan_compliance_preview(plan_compliance_summary),
            payload=plan_compliance_summary,
        )
        return verifier_result

    verifier_output, verifier_tool_calls = extract_response_content_and_tool_calls(verifier_result)
    verifier_status = "partial" if verifier_tool_calls else "completed"
    runtime_state["stage_statuses"]["verifier"] = verifier_status
    if verifier_status == "completed":
        runtime_state["completed_stages"].append("verifier")
    runtime_state["active_stage"] = "verifier"
    emit_plan_build_verify_stage_finished(
        workflow_info,
        stage="verifier",
        agent="ados-verifier",
        status=verifier_status,
        output_text=verifier_output,
    )
    runtime_state["stage_outputs"]["verifier"] = verifier_output
    runtime_state["stage_tool_names"]["verifier"] = normalize_string_list(
        [item.get("tool_name") for item in verifier_state.get("tool_events") or []]
    )
    verifier_guard_placeholder = "## Verify\n" + (verifier_output or "(no verifier output)")
    guard_result = evaluate_verifier_guard(
        verifier_output=verifier_output,
        final_output=verifier_guard_placeholder,
        validation_summary=validation_snapshot,
    )
    runtime_state["verifier_guard_result"] = guard_result.get("guard_result", "not_applicable")
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_verifier_guard_checked",
        title="Verifier guard checked",
        preview=(
            f"validation={guard_result.get('validation_result', 'unknown')} "
            f"guard={guard_result.get('guard_result', 'ok')}"
        ),
        payload=guard_result,
    )
    if guard_result.get("guard_result") == "warning":
        runtime_state.setdefault("warnings", []).extend(
            [warning for warning in guard_result.get("warnings", []) if warning not in runtime_state.get("warnings", [])]
        )
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_verifier_guard_warning",
            title="Verifier guard warning",
            preview=(
                f"validation={guard_result.get('validation_result', 'unknown')} "
                f"pass_like_claim={guard_result.get('pass_like_claim_detected', False)}"
            ),
            payload=guard_result,
            status="warning",
        )

    verification_result_summary = summarize_verification_result(
        {
            "workflow_mode": workflow_info.get("workflow_mode"),
            "stage": "verifier",
            "agent": "ados-verifier",
            **extract_verification_result(
                verifier_output,
                plan=structured_plan,
                build_result=build_result_summary,
                changed_files=builder_delta.get("changed_files") or [],
                validation_summary=validation_snapshot,
            ),
        }
    )
    file_scope_check = check_file_scope(structured_plan, builder_delta.get("changed_files") or [])
    plan_compliance_summary = summarize_plan_compliance_check(
        {
            "workflow_mode": workflow_info.get("workflow_mode"),
            "stage": "verifier",
            "agent": "ados-verifier",
            "plan_compliance_status": verification_result_summary.get("plan_compliance_status"),
            "planned_steps_count": build_steps_summary.get("planned_steps_count"),
            "completed_steps_count": build_steps_summary.get("completed_steps_count"),
            "missing_steps": build_steps_summary.get("missing_steps") or [],
            "missing_steps_count": build_steps_summary.get("missing_steps_count"),
            "unexpected_files": file_scope_check.get("unexpected_files") or [],
            "unexpected_files_count": file_scope_check.get("unexpected_files_count"),
            "forbidden_files_touched": file_scope_check.get("forbidden_files_touched") or [],
            "forbidden_files_touched_count": file_scope_check.get("forbidden_files_touched_count"),
            "validation_result": validation_snapshot.get("validation_result"),
            "caveats": verification_result_summary.get("caveats") or [],
        }
    )
    runtime_state["verification_result"] = verification_result_summary
    runtime_state["file_scope_check"] = file_scope_check
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_verification_result_captured",
        title="ADOS verification result captured",
        preview=build_plan_compliance_preview(
            {
                **verification_result_summary,
                "planned_steps_count": build_steps_summary.get("planned_steps_count"),
            }
        ),
        payload=verification_result_summary,
    )
    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_ados_plan_compliance_checked",
        title="ADOS plan compliance checked",
        preview=build_plan_compliance_preview(plan_compliance_summary),
        payload=plan_compliance_summary,
    )
    emit_skill_request_events(
        workflow_mode=str(workflow_info.get("workflow_mode") or ""),
        stage="verifier",
        agent="ados-verifier",
        output_text=verifier_output,
        available_skills=available_skills,
        runtime_state=runtime_state,
    )

    review_result_summary = {}
    if workflow_info.get("workflow_mode") == "plan_build_verify_review":
        emit_pbv_stage_handoff_created(
            build_handoff_payload(
                workflow_info=workflow_info,
                from_stage="verifier",
                to_stage="reviewer",
                from_agent="ados-verifier",
                to_agent="ados-reviewer",
                from_status=verifier_status,
                to_status="ready",
                output_text=verifier_output,
                plan=structured_plan,
                build_result=build_result_summary,
                changed_files=builder_delta.get("changed_files") or [],
                diff_preview=str(builder_delta.get("diff_preview") or ""),
                validation_result=str(validation_snapshot.get("validation_result") or "unknown"),
            )
        )
        runtime_state["active_stage"] = "reviewer"
        emit_plan_build_verify_stage_started(workflow_info, "reviewer", "ados-reviewer")
        runtime_state["stage_agents"]["reviewer"] = "ados-reviewer"
        reviewer_state = {"commands": [], "loaded_skills": [], "tool_calls_count": 0, "mcp_internal_tool_calls": 0, "mcp_external_tool_calls": 0, "tool_events": [], "warnings": []}
        reviewer_context = [
            f"Planner output:\n{planner_output}",
            f"Builder output:\n{builder_output or '(no builder output)'}",
            f"Verifier output:\n{verifier_output or '(no verifier output)'}",
            "Build result summary:\n" + json.dumps(build_result_summary, ensure_ascii=False, indent=2),
            "Verification result summary:\n" + json.dumps(verification_result_summary, ensure_ascii=False, indent=2),
            "Changed files summary:\n"
            + json.dumps(
                {
                    "changed_files": builder_delta.get("changed_files") or [],
                    "untracked_files": builder_delta.get("untracked_files") or [],
                    "diff_preview": str(builder_delta.get("diff_preview") or "")[:2000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "Validation summary:\n" + json.dumps(validation_snapshot, ensure_ascii=False, indent=2),
            "Suggested validation commands:\n" + json.dumps(runtime_state.get("suggested_validation_commands") or [], ensure_ascii=False, indent=2),
            "Warnings:\n" + json.dumps(runtime_state.get("warnings") or [], ensure_ascii=False, indent=2),
            load_requested_skill_instructions(runtime_state.get("approved_requested_skills") or []),
        ]
        reviewer_result = await run_chat_with_mcp_loop(
            build_stage_request(
                req,
                stage_agent="ados-reviewer",
                stage_instruction=reviewer_instruction,
                context_blocks=reviewer_context,
            ),
            runtime_state=reviewer_state,
            emit_workflow_events=False,
            selected_agent_override="ados-reviewer",
        )
        merge_runtime_state(runtime_state, reviewer_state)

        if isinstance(reviewer_result, dict) and "error" in reviewer_result:
            reviewer_status = "partial"
            runtime_state["failed_stages"].append("reviewer")
            runtime_state["stage_statuses"]["reviewer"] = reviewer_status
            reviewer_output = str(reviewer_result.get("error") or "(reviewer error)")
        else:
            reviewer_output, reviewer_tool_calls = extract_response_content_and_tool_calls(reviewer_result)
            reviewer_status = "partial" if reviewer_tool_calls else "completed"
            runtime_state["stage_statuses"]["reviewer"] = reviewer_status
            if reviewer_status == "completed":
                runtime_state["completed_stages"].append("reviewer")

        emit_plan_build_verify_stage_finished(
            workflow_info,
            stage="reviewer",
            agent="ados-reviewer",
            status=runtime_state["stage_statuses"]["reviewer"],
            output_text=reviewer_output,
        )
        runtime_state["stage_outputs"]["reviewer"] = reviewer_output
        runtime_state["stage_tool_names"]["reviewer"] = normalize_string_list(
            [item.get("tool_name") for item in reviewer_state.get("tool_events") or []]
        )
        reviewer_parse = extract_review_result(
            reviewer_output,
            validation_result=str(validation_snapshot.get("validation_result") or "unknown"),
        )
        if isinstance(reviewer_result, dict) and "error" in reviewer_result:
            reviewer_parse = {
                "parse_status": "not_found" if not str(reviewer_output or "").strip() else "error",
                "risk": "unknown",
                "commit_readiness": "needs_human_review",
                "scope_control": "unknown",
                "validation_confidence": "unverified"
                if str(validation_snapshot.get("validation_result") or "unknown") == "not_run"
                else "unknown",
            }
        review_result_summary = summarize_review_result(
            {
                "workflow_mode": workflow_info.get("workflow_mode"),
                "stage": "reviewer",
                "agent": "ados-reviewer",
                "stage_status": runtime_state["stage_statuses"]["reviewer"],
                **reviewer_parse,
            }
        )
        runtime_state["review_result"] = review_result_summary
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_ados_review_result_captured",
            title="ADOS review result captured",
            preview=build_review_result_preview(review_result_summary),
            payload=review_result_summary,
        )
        emit_skill_request_events(
            workflow_mode=str(workflow_info.get("workflow_mode") or ""),
            stage="reviewer",
            agent="ados-reviewer",
            output_text=reviewer_output,
            available_skills=available_skills,
            runtime_state=runtime_state,
        )
        if review_result_summary.get("risk") == "high":
            runtime_state.setdefault("warnings", []).append("Reviewer marked risk=high.")
        if review_result_summary.get("commit_readiness") in {"not_ready", "needs_human_review"}:
            runtime_state.setdefault("warnings", []).append(
                f"Reviewer marked commit_readiness={review_result_summary.get('commit_readiness')}."
            )

    status_info = derive_workflow_status(
        workflow_mode=workflow_info["workflow_mode"],
        stage_statuses=runtime_state.get("stage_statuses"),
        completed_stages=runtime_state.get("completed_stages"),
        failed_stages=runtime_state.get("failed_stages"),
        skipped_stages=runtime_state.get("skipped_stages"),
        changed_files_count=len(builder_delta.get("changed_files") or []),
        validation_summary=validation_snapshot,
        warnings=runtime_state.get("warnings"),
        runner_error=runtime_state.get("runner_error"),
        model_response_sent=True,
    )
    runtime_state["workflow_status"] = status_info.get("workflow_status", "unknown")
    final_status = status_info.get("final_status", "unknown")
    final_status_reason = status_info.get("status_reason", "")
    final_status_block = final_status
    if final_status_reason:
        final_status_block += f"\n\n{final_status_reason}"
    if validation_snapshot.get("validation_result") == "not_run":
        final_status_block += (
            "\n\nValidation was not run. The file change was made and verifier inspected the result, "
            "but no command/test validation evidence is available."
        )
    suggested_validation_block = ""
    if validation_snapshot.get("validation_result") == "not_run" and runtime_state.get("suggested_validation_commands"):
        suggested_validation_block = (
            "\n\n## Suggested Validation\n"
            "Validation was not run. Suggested commands:\n\n"
            "```powershell\n"
            + "\n".join(runtime_state.get("suggested_validation_commands") or [])
            + "\n```"
        )
    review_block = ""
    if workflow_info.get("workflow_mode") == "plan_build_verify_review":
        review_block = "## Review\n" + f"{reviewer_output or '(no reviewer output)'}\n\n"
    final_content = (
        "## Plan\n"
        f"{planner_output or '(no planner output)'}\n\n"
        "## Build\n"
        f"{builder_output or '(no builder output)'}\n\n"
        "## Verify\n"
        f"{verifier_output or '(no verifier output)'}\n\n"
        f"{review_block}"
        "## Final Status\n"
        f"{final_status_block}"
        f"{suggested_validation_block}"
    )

    return make_chat_response(
        req=req,
        content=final_content,
        tool_calls=None,
        prompt_text=final_content,
        raw_output=final_content,
    )

def tool_call_name(tool_call: dict[str, Any]) -> str:
    fn = tool_call.get("function") or {}
    return str(fn.get("name") or "")


def tool_result_message(tool_call: dict[str, Any], result_text: str) -> ChatMessage:
    return ChatMessage(
        role="tool",
        tool_call_id=tool_call.get("id", ""),
        content=result_text,
    )


def assistant_tool_call_message(tool_calls: list[dict[str, Any]]) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=None,
        tool_calls=tool_calls,
    )

def request_wants_mcp_tools(req: ChatCompletionRequest) -> bool:
    """
    OpenCode native tool calling path:
      req.tools contains read/edit/bash etc.
      In that mode, do not auto-append MCP tools unless caller explicitly asks.

    Direct API / MCP path:
      no req.tools, or req.mcp.enabled=true.
    """
    if isinstance(req.mcp, dict):
        if req.mcp.get("enabled") is False:
            return False
        if req.mcp.get("enabled") is True:
            return True

    # If OpenCode/AI SDK provides native tools, keep the tool list clean.
    if req.tools:
        return False

    return True


async def run_chat_with_mcp_loop(
    req: ChatCompletionRequest,
    runtime_state: Optional[dict[str, Any]] = None,
    emit_workflow_events: bool = True,
    selected_agent_override: Optional[str] = None,
) -> dict[str, Any]:
    loop_started_at = time.perf_counter()
    runtime_state = runtime_state if runtime_state is not None else {}
    runtime_state.setdefault("commands", [])
    runtime_state.setdefault("tool_events", [])
    runtime_state.setdefault("warnings", [])
    runtime_state.setdefault("stage_statuses", {})

    safe_append_event(
        source="mcp-runtime",
        event_type="mcp_native_loop_entered",
        title="進入 native tool loop",
        preview="run_chat_with_mcp_loop entered",
        payload={
            "request_type": type(req).__name__,
        },
    )

    messages = [ChatMessage(**m.model_dump()) for m in req.messages]
    messages_for_ados = [m.model_dump() for m in messages]

    ados_instruction_info = build_ados_template_instruction(
        messages_for_ados,
        selected_agent_override=selected_agent_override,
    )
    ados_instruction = ados_instruction_info.get("instruction", "")
    runtime_state["selected_agent"] = ados_instruction_info.get("selected", "")
    runtime_state["workflow_info"] = build_ados_workflow_info(runtime_state.get("selected_agent"))
    runtime_state["workflow_mode"] = runtime_state["workflow_info"].get("workflow_mode", "")
    runtime_state["active_stage"] = runtime_state["workflow_info"].get("active_stage", "")

    emit_ados_template_loaded_event(
        safe_append_event,
        ados_instruction_info,
    )

    emit_ados_template_injected_event(
        safe_append_event,
        ados_instruction_info,
    )

    if emit_workflow_events:
        emit_ados_workflow_started_events(
            safe_append_event,
            runtime_state.get("workflow_info") or build_ados_workflow_info(runtime_state.get("selected_agent")),
        )

    skill_instruction_info = build_selected_skill_instructions(
        messages_for_ados,
        max_skills=2,
    )
    skill_instruction = skill_instruction_info.get("instruction", "")
    runtime_state["loaded_skills"] = list(skill_instruction_info.get("selected_skill_names") or [])

    emit_opencode_skill_trace_events(
        safe_append_event,
        skill_instruction_info,
    )

    opencode_instruction_parts = [
        x for x in [ados_instruction, skill_instruction] if x
    ]
    opencode_instruction = "\n\n---\n\n".join(opencode_instruction_parts)

    request_tools = req.tools or []

    if request_wants_mcp_tools(req):
        mcp_tools = await mcp_manager.list_tools()
    else:
        mcp_tools = []

    all_tools = [*request_tools, *mcp_tools]

    raw_output = ""
    last_prompt = ""

    for _loop_index in range(MAX_TOOL_LOOPS):
        prompt_text = build_api_prompt(
            messages=messages,
            tools=all_tools,
            tool_choice=req.tool_choice,
            ados_instruction=opencode_instruction,
        )
        last_prompt = prompt_text

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_loop_iteration_started",
            title="MCP loop iteration 開始",
            preview=f"loop_index={_loop_index} tools={len(all_tools)} messages={len(messages)}",
            payload={
                "loop_index": _loop_index,
                "tools_count": len(all_tools),
                "messages_count": len(messages),
                "prompt_length": len(prompt_text),
                "prompt_preview": "***masked***",
            },
        )

        code, stdout, stderr, raw_output = await run_main_with_prompt(
            prompt_text,
            timeout_seconds=600,
        )

        if code != 0:
            safe_append_event(
                source="mcp-runtime",
                event_type="mcp_runner_error",
                title="ChatGPT Web runner 失敗",
                preview=f"code={code}",
                level="error",
                payload={
                    "code": code,
                    "stdout_length": len(stdout or ""),
                    "stderr_length": len(stderr or ""),
                    "output_preview": "***masked***",
                },
                status="error",
            )

            return {
                "error": {
                    "message": stderr or stdout or "ChatGPT Web UI runner failed",
                    "type": "runner_error",
                    "code": code,
                }
            }

        content, tool_calls = parse_assistant_output(raw_output)

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_model_output_parsed",
            title="模型輸出解析完成",
            preview=f"tool_calls={len(tool_calls or [])} content_length={len(content or '')}",
            payload={
                "loop_index": _loop_index,
                "content_length": len(content or ""),
                **build_text_debug_fields("content", content),
                "tool_calls_count": len(tool_calls or []),
                "raw_output_length": len(raw_output or ""),
                **build_text_debug_fields("raw_output", raw_output),
            },
        )

        if not tool_calls:
            return make_chat_response(
                req=req,
                content=content,
                tool_calls=None,
                prompt_text=prompt_text,
                raw_output=raw_output,
            )

        executable_calls: list[dict[str, Any]] = []
        external_calls: list[dict[str, Any]] = []

        for call in tool_calls:
            name = tool_call_name(call)

            if mcp_manager.is_mcp_tool(name):
                executable_calls.append(call)
            else:
                external_calls.append(call)

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_tool_calls_classified",
            title="工具呼叫分類完成",
            preview=f"mcp={len(executable_calls)} external={len(external_calls)}",
            payload={
                "loop_index": _loop_index,
                "mcp_tool_calls_count": len(executable_calls),
                "external_tool_calls_count": len(external_calls),
                "mcp_tool_calls": [summarize_mcp_tool_call(call) for call in executable_calls],
                "external_tool_calls": [summarize_mcp_tool_call(call) for call in external_calls],
            },
        )

        runtime_state["mcp_internal_tool_calls"] = int(runtime_state.get("mcp_internal_tool_calls") or 0) + len(executable_calls)
        runtime_state["mcp_external_tool_calls"] = int(runtime_state.get("mcp_external_tool_calls") or 0) + len(external_calls)
        runtime_state["tool_calls_count"] = int(runtime_state.get("tool_calls_count") or 0) + len(executable_calls) + len(external_calls)

        stage = str(runtime_state.get("active_stage") or "")
        agent = str(runtime_state.get("selected_agent") or "")
        workflow_mode = str(runtime_state.get("workflow_mode") or "")
        enforce_stage_policy = workflow_mode in {"plan_build_verify", "plan_build_verify_review"} and bool(stage)

        blocked_external_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        allowed_external_calls: list[dict[str, Any]] = []
        for call in external_calls:
            name = tool_call_name(call)
            args = tool_call_arguments(call)
            command_info = detect_command_trace(name, args)
            if enforce_stage_policy:
                policy_result = summarize_stage_tool_policy(
                    apply_stage_tool_policy(
                        workflow_mode=workflow_mode,
                        stage=stage,
                        agent=agent,
                        tool_name=name,
                        command_info=command_info,
                    )
                )
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_stage_tool_policy_checked",
                    title="Stage tool policy checked",
                    preview=build_stage_tool_policy_preview(policy_result),
                    payload=policy_result,
                )
                if policy_result.get("decision") == "blocked":
                    blocked_external_calls.append((call, policy_result))
                    safe_append_event(
                        source="opencode-runtime",
                        event_type="opencode_stage_tool_policy_blocked",
                        title="Stage tool policy blocked",
                        preview=build_stage_tool_policy_preview(policy_result),
                        payload=policy_result,
                        status="warning",
                    )
                    continue
                if policy_result.get("decision") == "warning":
                    safe_append_event(
                        source="opencode-runtime",
                        event_type="opencode_stage_tool_policy_warning",
                        title="Stage tool policy warning",
                        preview=build_stage_tool_policy_preview(policy_result),
                        payload=policy_result,
                        status="warning",
                    )
            allowed_external_calls.append(call)

        external_calls = allowed_external_calls

        # OpenCode native tools, such as read/edit/bash, must be returned to
        # OpenCode as assistant.tool_calls. Do not execute them inside this API.
        if external_calls and not executable_calls:
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_native_tool_calls_returned",
                title="回傳 OpenCode native tool calls",
                preview=f"external_tool_calls={len(external_calls)}",
                payload={
                    "external_tool_calls_count": len(external_calls),
                    "external_tool_calls": [summarize_mcp_tool_call(call) for call in external_calls],
                },
            )

            return make_chat_response(
                req=req,
                content="",
                tool_calls=external_calls,
                prompt_text=prompt_text,
                raw_output=raw_output,
            )

        if blocked_external_calls:
            messages.append(assistant_tool_call_message([call for call, _ in blocked_external_calls]))
            for call, policy_result in blocked_external_calls:
                blocked_payload = {
                    "error": "stage_tool_policy_blocked",
                    "tool": policy_result.get("tool"),
                    "stage": policy_result.get("stage"),
                    "reason": policy_result.get("reason"),
                    "decision": "blocked",
                }
                messages.append(tool_result_message(call, json.dumps(blocked_payload, ensure_ascii=False)))
            if not external_calls and not executable_calls:
                continue

        remaining_tool_calls = list(executable_calls) + list(external_calls)
        if remaining_tool_calls:
            messages.append(assistant_tool_call_message(remaining_tool_calls))

        for call in executable_calls:
            name = tool_call_name(call)
            args = tool_call_arguments(call)
            tool_started_at = time.perf_counter()
            command_info = detect_command_trace(name, args)
            command_started_at = datetime.now().isoformat()

            if enforce_stage_policy:
                policy_result = summarize_stage_tool_policy(
                    apply_stage_tool_policy(
                        workflow_mode=workflow_mode,
                        stage=stage,
                        agent=agent,
                        tool_name=name,
                        command_info=command_info,
                    )
                )
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_stage_tool_policy_checked",
                    title="Stage tool policy checked",
                    preview=build_stage_tool_policy_preview(policy_result),
                    payload=policy_result,
                )
                if policy_result.get("decision") == "blocked":
                    safe_append_event(
                        source="opencode-runtime",
                        event_type="opencode_stage_tool_policy_blocked",
                        title="Stage tool policy blocked",
                        preview=build_stage_tool_policy_preview(policy_result),
                        payload=policy_result,
                        status="warning",
                    )
                    runtime_state.setdefault("tool_events", []).append(
                        {
                            "tool_name": name,
                            "success": False,
                            "blocked": True,
                            "stage": stage,
                            "duration_ms": 0,
                        }
                    )
                    blocked_payload = {
                        "error": "stage_tool_policy_blocked",
                        "tool": policy_result.get("tool"),
                        "stage": policy_result.get("stage"),
                        "reason": policy_result.get("reason"),
                        "decision": "blocked",
                    }
                    messages.append(tool_result_message(call, json.dumps(blocked_payload, ensure_ascii=False)))
                    continue
                if policy_result.get("decision") == "warning":
                    safe_append_event(
                        source="opencode-runtime",
                        event_type="opencode_stage_tool_policy_warning",
                        title="Stage tool policy warning",
                        preview=build_stage_tool_policy_preview(policy_result),
                        payload=policy_result,
                        status="warning",
                    )

            safe_append_event(
                source="mcp-runtime",
                event_type="mcp_tool_started",
                title="MCP 工具開始執行",
                preview=f"tool={name}",
                payload={
                    "tool_name": name,
                    "tool_call": summarize_mcp_tool_call(call),
                },
            )

            if command_info:
                command_started_payload = build_command_trace_payload(
                    command_info,
                    started_at=command_started_at,
                    finished_at="",
                    duration_ms=None,
                    result_text="",
                    error_text="",
                )
                command_started_event_type = (
                    "opencode_test_started"
                    if command_info.get("is_test_command")
                    else "opencode_command_started"
                )

                safe_append_event(
                    source="opencode-runtime",
                    event_type=command_started_event_type,
                    title="Test started" if command_info.get("is_test_command") else "Command started",
                    preview=build_command_event_preview(command_started_payload),
                    payload=command_started_payload,
                )

            try:
                result_text = await mcp_manager.call_tool(name, args)
                tool_duration_ms = int((time.perf_counter() - tool_started_at) * 1000)
                command_finished_at = datetime.now().isoformat()

                safe_append_event(
                    source="mcp-runtime",
                    event_type="mcp_tool_finished",
                    title="MCP 工具執行完成",
                    preview=f"tool={name} duration_ms={tool_duration_ms}",
                    payload={
                        "tool_name": name,
                        "result": summarize_tool_result(result_text),
                    },
                    duration_ms=tool_duration_ms,
                )

                runtime_state.setdefault("tool_events", []).append(
                    {
                        "tool_name": name,
                        "success": True,
                        "stage": runtime_state.get("active_stage", ""),
                        "duration_ms": tool_duration_ms,
                    }
                )

                if command_info:
                    command_finished_payload = build_command_trace_payload(
                        command_info,
                        started_at=command_started_at,
                        finished_at=command_finished_at,
                        duration_ms=tool_duration_ms,
                        result_text=result_text,
                        error_text="",
                    )
                    command_finished_event_type = (
                        "opencode_test_finished"
                        if command_info.get("is_test_command")
                        else "opencode_command_finished"
                    )

                    safe_append_event(
                        source="opencode-runtime",
                        event_type=command_finished_event_type,
                        title="Test finished" if command_info.get("is_test_command") else "Command finished",
                        preview=build_command_event_preview(command_finished_payload),
                        payload=command_finished_payload,
                        duration_ms=tool_duration_ms,
                    )

                    runtime_state.setdefault("commands", []).append(command_finished_payload)

            except Exception as error:
                tool_duration_ms = int((time.perf_counter() - tool_started_at) * 1000)
                command_finished_at = datetime.now().isoformat()

                safe_append_event(
                    source="mcp-runtime",
                    event_type="mcp_tool_error",
                    title="MCP 工具執行失敗",
                    preview=f"tool={name} error={error}",
                    level="error",
                    payload={
                        "tool_name": name,
                        "error": str(error),
                        "error_type": type(error).__name__,
                    },
                    status="error",
                    duration_ms=tool_duration_ms,
                )

                runtime_state.setdefault("tool_events", []).append(
                    {
                        "tool_name": name,
                        "success": False,
                        "stage": runtime_state.get("active_stage", ""),
                        "duration_ms": tool_duration_ms,
                    }
                )

                if command_info:
                    command_finished_payload = build_command_trace_payload(
                        command_info,
                        started_at=command_started_at,
                        finished_at=command_finished_at,
                        duration_ms=tool_duration_ms,
                        result_text="",
                        error_text=f"{type(error).__name__}: {error}",
                    )
                    command_finished_event_type = (
                        "opencode_test_finished"
                        if command_info.get("is_test_command")
                        else "opencode_command_finished"
                    )

                    safe_append_event(
                        source="opencode-runtime",
                        event_type=command_finished_event_type,
                        title="Test finished" if command_info.get("is_test_command") else "Command finished",
                        preview=build_command_event_preview(command_finished_payload),
                        payload=command_finished_payload,
                        status="error",
                        duration_ms=tool_duration_ms,
                    )

                    runtime_state.setdefault("commands", []).append(command_finished_payload)

                result_text = f"[MCP_TOOL_ERROR]\n{name}\n{type(error).__name__}: {error}"

            messages.append(tool_result_message(call, result_text))

        # Mixed mode: execute MCP tools internally, return OpenCode native tools
        # back to OpenCode.
        if external_calls:
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_mixed_tool_calls_returned",
                title="回傳混合模式 OpenCode native tool calls",
                preview=f"external_tool_calls={len(external_calls)}",
                payload={
                    "external_tool_calls_count": len(external_calls),
                    "external_tool_calls": [summarize_mcp_tool_call(call) for call in external_calls],
                },
            )

            return make_chat_response(
                req=req,
                content="",
                tool_calls=external_calls,
                prompt_text=prompt_text,
                raw_output=raw_output,
            )

    final_content = (
        "工具呼叫已達最大輪數，停止自動 MCP loop。\n\n"
        f"最後一次模型輸出：\n\n{raw_output}"
    )

    loop_duration_ms = int((time.perf_counter() - loop_started_at) * 1000)
    safe_append_event(
        source="mcp-runtime",
        event_type="mcp_loop_max_iterations_reached",
        title="MCP loop 達最大輪數",
        preview=f"max_tool_loops={MAX_TOOL_LOOPS}",
        level="warning",
        payload={
            "max_tool_loops": MAX_TOOL_LOOPS,
            "duration_ms": loop_duration_ms,
            "last_raw_output_length": len(raw_output or ""),
            "last_raw_output_preview": "***masked***",
        },
        status="warning",
        duration_ms=loop_duration_ms,
    )

    return make_chat_response(
        req=req,
        content=final_content,
        tool_calls=None,
        prompt_text=last_prompt,
        raw_output=raw_output,
    )

app = FastAPI(
    title="DevTools Radar Local API",
    version=SERVER_VERSION,
    description=(
        "OpenAI-compatible local API backed by ChatGPT Web UI automation "
        "with MCP tool support, security layer, approval workflow, snapshots, and guards."
    ),
)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "DevTools Radar Local API",
        "version": SERVER_VERSION,
        "status": "ok",
        "openai_compatible_base_url": "http://127.0.0.1:8788/v1",
        "model": DEFAULT_MODEL,
        "mcp_config": str(MCP_CONFIG_PATH),
        "mcp_security": str(MCP_SECURITY_PATH),
        "mcp_tool_snapshots": str(MCP_TOOL_SNAPSHOT_PATH),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "main_py_exists": MAIN_PY.exists(),
        "output_txt_exists": OUTPUT_TXT.exists(),
        "base_dir": str(BASE_DIR),
        "model": DEFAULT_MODEL,
        "mcp_config_exists": MCP_CONFIG_PATH.exists(),
        "mcp_security_exists": MCP_SECURITY_PATH.exists(),
        "mcp_security_enabled": mcp_security.enabled(),
        "pending_mcp_calls": len(pending_mcp_calls),
        "pending_mcp_calls_loaded": len(pending_mcp_calls),
        "mcp_supports_streamable_http": True,
        "openai_chat_completions_stream": True,
        "openai_chat_completions_stream_mode": "pseudo_stream_after_full_response",
        "mcp_tool_snapshots_exists": MCP_TOOL_SNAPSHOT_PATH.exists(),
        "mcp_tool_snapshots_count": len(mcp_tool_snapshots.snapshots),
        "version": SERVER_VERSION,
    }

@app.get("/v1/debug/runner")
async def debug_runner(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    return {
        "runner_lock_locked": runner_lock.locked(),
        "runner_lock_file": read_runner_lock_info(),
        "main_py": str(MAIN_PY),
        "main_py_exists": MAIN_PY.exists(),
        "output_txt": str(OUTPUT_TXT),
        "output_txt_exists": OUTPUT_TXT.exists(),
        "base_dir": str(BASE_DIR),
    }


@app.post("/v1/debug/runner/cleanup-lock")
async def debug_cleanup_runner_lock(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    before = read_runner_lock_info()
    removed = cleanup_stale_runner_lock(max_age_seconds=0)
    after = read_runner_lock_info()

    return {
        "removed": removed,
        "before": before,
        "after": after,
    }


@app.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    return {
        "object": "list",
        "data": [
            ModelInfo(id=DEFAULT_MODEL).model_dump(),
            ModelInfo(id="chatgpt-web").model_dump(),
            ModelInfo(id="devtools-radar-local").model_dump(),
        ],
    }


@app.get("/v1/mcp/config")
async def get_mcp_config(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    if not MCP_CONFIG_PATH.exists():
        return {
            "config_path": str(MCP_CONFIG_PATH),
            "config": {
                "servers": {}
            },
        }

    try:
        config = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read mcp_servers.json: {type(error).__name__}: {error}",
        )

    return {
        "config_path": str(MCP_CONFIG_PATH),
        "config": config,
    }


@app.put("/v1/mcp/config")
async def save_mcp_config(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    await verify_auth(request)

    config = payload.get("config", payload)

    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    if "servers" not in config or not isinstance(config["servers"], dict):
        raise HTTPException(status_code=400, detail="config.servers must be an object")

    for name, server_config in config["servers"].items():
        if not isinstance(server_config, dict):
            raise HTTPException(status_code=400, detail=f"server config must be object: {name}")

        try:
            McpServerConfig(**server_config)
        except Exception as error:
            raise HTTPException(
                status_code=400,
                detail=f"invalid server config {name}: {type(error).__name__}: {error}",
            )

    backup_path = MCP_CONFIG_PATH.with_suffix(".json.bak")

    if MCP_CONFIG_PATH.exists():
        backup_path.write_text(
            MCP_CONFIG_PATH.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )

    MCP_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    await mcp_manager.reload()

    return {
        "status": "ok",
        "message": "mcp_servers.json saved and MCP servers reloaded",
        "config_path": str(MCP_CONFIG_PATH),
        "backup_path": str(backup_path),
        "config": config,
    }


@app.get("/v1/mcp/servers")
async def list_mcp_servers(request: Request) -> dict[str, Any]:
    await verify_auth(request)
    await mcp_manager.ensure_loaded()

    return {
        "config_path": str(MCP_CONFIG_PATH),
        "servers": [
            {
                "name": name,
                "transport": client.config.transport,
                "running": (
                    True
                    if client.config.transport == "streamable_http" and getattr(client, "http", None) is not None
                    else client.process is not None and client.process.returncode is None
                    if hasattr(client, "process")
                    else False
                ),
                "initialized": client.initialized,
                "url": getattr(client.config, "url", ""),
                "command": getattr(client.config, "command", ""),
                "args": getattr(client.config, "args", []),
                "profile": {
                    "risk_level": client.config.risk_level,
                    "trust": client.config.trust,
                    "network": client.config.network,
                    "filesystem": client.config.filesystem,
                    "shell": client.config.shell,
                    "auto_approve": client.config.auto_approve,
                    "notes": client.config.notes,
                },
            }
            for name, client in mcp_manager.clients.items()
        ],
    }


@app.get("/v1/mcp/tools")
async def list_mcp_tools(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    tools = await mcp_manager.list_tools()

    return {
        "object": "list",
        "data": tools,
        "count": len(tools),
    }


@app.post("/v1/mcp/reload")
async def reload_mcp(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    await mcp_manager.reload()

    return {
        "status": "ok",
        "message": "MCP servers reloaded",
    }


@app.get("/v1/mcp/security")
async def get_mcp_security(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    return {
        "config_path": str(MCP_SECURITY_PATH),
        "config_exists": MCP_SECURITY_PATH.exists(),
        "security": mcp_security.config,
    }


@app.put("/v1/mcp/security")
async def save_mcp_security(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    await verify_auth(request)

    config = payload.get("security", payload.get("config", payload))

    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="security config must be an object")

    if "tool_policies" not in config or not isinstance(config["tool_policies"], list):
        raise HTTPException(status_code=400, detail="security.tool_policies must be a list")

    valid_actions = {"allow", "confirm", "deny"}

    for index, policy in enumerate(config["tool_policies"]):
        if not isinstance(policy, dict):
            raise HTTPException(status_code=400, detail=f"policy #{index} must be an object")

        pattern = str(policy.get("pattern", "")).strip()
        action = str(policy.get("action", "")).strip().lower()

        if not pattern:
            raise HTTPException(status_code=400, detail=f"policy #{index} missing pattern")

        if action not in valid_actions:
            raise HTTPException(
                status_code=400,
                detail=f"policy #{index} invalid action: {action}. Must be allow, confirm, or deny.",
            )

        policy["action"] = action
        policy["reason"] = str(policy.get("reason", "")).strip() or f"{action} by MCP Security Policy UI"

    if "enabled" not in config:
        config["enabled"] = True

    if "default_action" not in config:
        config["default_action"] = "deny"

    if str(config["default_action"]).lower() not in valid_actions:
        raise HTTPException(status_code=400, detail="default_action must be allow, confirm, or deny")

    if "tool_timeout_seconds" not in config:
        config["tool_timeout_seconds"] = 20

    if "max_tool_output_chars" not in config:
        config["max_tool_output_chars"] = 12000

    if "audit_log_enabled" not in config:
        config["audit_log_enabled"] = True

    if "allowed_roots" not in config or not isinstance(config["allowed_roots"], list):
        config["allowed_roots"] = [str(BASE_DIR)]

    if "argument_guards" not in config or not isinstance(config["argument_guards"], dict):
        config["argument_guards"] = mcp_security.default_argument_guards()

    backup_path = MCP_SECURITY_PATH.with_suffix(".json.bak")

    if MCP_SECURITY_PATH.exists():
        backup_path.write_text(
            MCP_SECURITY_PATH.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )

    MCP_SECURITY_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    mcp_security.reload()

    return {
        "status": "ok",
        "message": "mcp_security.json saved and reloaded",
        "config_path": str(MCP_SECURITY_PATH),
        "backup_path": str(backup_path),
        "security": mcp_security.config,
    }

@app.get("/v1/debug/runs")
def debug_runs(limit: int = 50):
    rows = list_runs(limit)
    return {
        "object": "list",
        "data": rows,
        "count": len(rows),
    }


@app.get("/v1/debug/runs/{run_id}/events")
def debug_run_events(run_id: str, limit: int = 500):
    return {
        "run_id": run_id,
        "data": read_run_events(run_id, limit),
        "latest_event_id": None,
    }


@app.get("/v1/debug/runs/{run_id}/stats")
def debug_run_stats(run_id: str):
    return get_event_stats(run_id)


@app.get("/v1/debug/runs/{run_id}/export")
def debug_run_export(run_id: str):
    return Response(
        content=export_events_text(run_id),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="runtime-events-{run_id}.jsonl"'
        },
    )


@app.delete("/v1/debug/runs/{run_id}")
def debug_run_delete(run_id: str):
    delete_run(run_id)
    return {
        "ok": True,
        "deleted": run_id,
    }


@app.get("/v1/debug/events")
def debug_events(limit: int = 200):
    return {
        "data": read_recent_events(limit),
        "latest_event_id": None,
    }


@app.get("/v1/debug/events/stats")
def debug_events_stats():
    return get_event_stats()


@app.post("/v1/debug/events/clear")
def debug_events_clear():
    before = get_event_stats()
    clear_events()
    return {
        "ok": True,
        "cleared": before.get("total", 0),
    }


@app.get("/v1/debug/events/export")
def debug_events_export():
    return Response(
        content=export_events_text(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="runtime-events.jsonl"'
        },
    )

@app.post("/v1/mcp/security/reload")
async def reload_mcp_security(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    mcp_security.reload()

    return {
        "status": "ok",
        "message": "MCP security config reloaded",
        "config_path": str(MCP_SECURITY_PATH),
    }


@app.get("/v1/mcp/tool-snapshots")
async def list_mcp_tool_snapshots(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    await mcp_manager.list_tools()

    data = mcp_tool_snapshots.list_snapshots()

    return {
        "object": "list",
        "data": data,
        "count": len(data),
        "path": str(MCP_TOOL_SNAPSHOT_PATH),
    }


@app.post("/v1/mcp/tool-snapshots/{tool_name}/approve")
async def approve_mcp_tool_snapshot(tool_name: str, request: Request) -> dict[str, Any]:
    await verify_auth(request)

    try:
        record = mcp_tool_snapshots.approve_tool(tool_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return {
        "status": "ok",
        "snapshot": record,
    }


@app.get("/v1/mcp/audit")
async def get_mcp_audit(request: Request, limit: int = 50) -> dict[str, Any]:
    await verify_auth(request)

    if not MCP_AUDIT_LOG.exists():
        return {
            "object": "list",
            "data": [],
            "count": 0,
        }

    lines = MCP_AUDIT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[-max(1, min(limit, 500)):]

    records = []

    for line in selected:
        try:
            records.append(json.loads(line))
        except Exception:
            records.append({"raw": line})

    return {
        "object": "list",
        "data": records,
        "count": len(records),
    }


@app.get("/v1/mcp/approvals")
async def list_mcp_approvals(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    records = list(pending_mcp_calls.values())
    records.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

    return {
        "object": "list",
        "data": records,
        "count": len(records),
    }


@app.post("/v1/mcp/approvals/{pending_id}/approve")
async def approve_mcp_call(pending_id: str, request: Request) -> dict[str, Any]:
    await verify_auth(request)

    async with runner_lock:
        try:
            result = await mcp_manager.execute_approved_pending_call(pending_id)
            return {
                "status": "ok",
                "approval": result,
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"{type(error).__name__}: {error}",
            )


@app.post("/v1/mcp/approvals/{pending_id}/deny")
async def deny_mcp_call(pending_id: str, request: Request) -> dict[str, Any]:
    await verify_auth(request)

    if pending_id not in pending_mcp_calls:
        raise HTTPException(
            status_code=404,
            detail=f"Pending MCP call not found: {pending_id}",
        )

    record = update_pending_mcp_call(
        pending_id=pending_id,
        status="denied",
        error="Denied by user",
    )

    decision = McpSecurityDecision(
        allowed=False,
        action="denied_by_user",
        reason=f"User denied pending call: {pending_id}",
        requires_confirmation=False,
    )

    mcp_security.audit(
        tool_name=record["tool"],
        arguments=record["arguments"],
        decision=decision,
        status="denied_by_user",
        error="Denied by user",
    )

    return {
        "status": "ok",
        "approval": record,
    }
@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
) -> Response:
    started_at = time.perf_counter()
    run_id = None
    run_token = None

    await verify_auth(request)

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is required")

    if hasattr(req, "model_dump"):
        body = req.model_dump()
    else:
        body = req.dict()

    run_id = start_run({
        "model": body.get("model"),
        "stream": body.get("stream", False),
        "messages_count": len(body.get("messages") or []),
        "tools_count": len(body.get("tools") or []),
    })

    run_token = set_current_run_id(run_id)

    request_summary = summarize_chat_request(body)
    opencode_summary = summarize_opencode_request(body)
    context_files = opencode_summary.get("context_files") or []
    workflow_mode = detect_workflow_mode(body.get("messages", []) if isinstance(body, dict) else [])
    use_plan_build_verify = workflow_mode in {"plan_build_verify", "plan_build_verify_review"}
    runtime_state: dict[str, Any] = {
        "commands": [],
        "tool_events": [],
        "loaded_skills": [],
        "selected_agent": "",
        "workflow_mode": "",
        "active_stage": "",
        "workflow_info": {},
        "completed_stages": [],
        "failed_stages": [],
        "skipped_stages": [],
        "workflow_status": "unknown",
        "stage_statuses": {},
        "tool_calls_count": 0,
        "mcp_internal_tool_calls": 0,
        "mcp_external_tool_calls": 0,
        "runner_error": None,
        "model_response_sent": False,
        "warnings": [],
        "verifier_guard_result": "not_applicable",
        "validation_strategy": "",
        "suggested_validation_commands": [],
        "requested_skills": [],
        "approved_requested_skills": [],
        "denied_requested_skills": [],
        "review_result": {},
        "stage_outputs": {},
        "stage_agents": {},
        "stage_tool_names": {},
        "started_at": datetime.now().isoformat(),
    }

    safe_append_event(
        source="devtools-radar-api",
        event_type="model_request_received",
        title="收到模型請求",
        preview=(
            f"model={body.get('model')} "
            f"messages={len(body.get('messages') or [])} "
            f"tools={len(body.get('tools') or [])} "
            f"stream={body.get('stream', False)}"
        ),
        payload=request_summary,
    )

    try:
        emit_ados_trace_events(
            safe_append_event,
            messages=body.get("messages", []) if isinstance(body, dict) else [],
            source="api_server",
        )
    except Exception as exc:
        safe_append_event(
            source="opencode-ados",
            event_type="opencode_ados_trace_error",
            title="ADOS trace 錯誤",
            preview=str(exc)[:240],
            payload={
                "source": "api_server",
                "error": str(exc),
            },
        )


    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_request_received",
        title="OpenCode/API 請求進入",
        preview=(
            f"model={body.get('model')} "
            f"messages={len(body.get('messages') or [])} "
            f"tools={len(body.get('tools') or [])}"
        ),
        payload=opencode_summary,
    )

    safe_append_event(
        source="opencode-runtime",
        event_type="opencode_context_files_detected",
        title="偵測 context files",
        preview=f"context_files={len(context_files)}",
        payload={
            "context_files_count": len(context_files),
            "context_files": context_files,
        },
    )

    try:
        git_diff_baseline = capture_git_diff_snapshot()

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_loop_started",
            title="MCP loop 開始",
            preview=f"model={body.get('model')}",
            payload={
                "model": body.get("model"),
                "stream": body.get("stream", False),
            },
        )

        cleanup_stale_runner_lock(max_age_seconds=900)

        async with runner_lock:
            if use_plan_build_verify:
                result = await run_plan_build_verify_workflow(req, runtime_state, git_diff_baseline, workflow_mode=workflow_mode)
            else:
                result = await run_chat_with_mcp_loop(req, runtime_state=runtime_state)

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_loop_completed",
            title="MCP loop 完成",
            preview="run_chat_with_mcp_loop completed",
            payload=summarize_loop_result(result),
        )

        git_diff_current = capture_git_diff_snapshot()
        git_diff_delta = build_git_diff_delta(git_diff_baseline, git_diff_current)

        duration_ms = int((time.perf_counter() - started_at) * 1000)

        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_changed_files_detected",
            title="Detected changed files",
            preview=(
                f"changed_files={len(git_diff_delta.get('changed_files') or [])} "
                f"additions={git_diff_delta.get('additions', 0)} "
                f"deletions={git_diff_delta.get('deletions', 0)}"
            ),
            payload=summarize_changed_files_snapshot(git_diff_delta),
            duration_ms=duration_ms,
        )

        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_diff_generated",
            title="Generated git diff preview",
            preview=(
                f"changed_files={len(git_diff_delta.get('changed_files') or [])} "
                f"diff_preview_length={len(git_diff_delta.get('diff_preview') or '')}"
            ),
            payload=summarize_diff_generated_snapshot(git_diff_delta),
            duration_ms=duration_ms,
        )
        runtime_state["changed_files"] = list(git_diff_delta.get("changed_files") or [])
        runtime_state["diff_preview"] = str(git_diff_delta.get("diff_preview") or "")

        if not runtime_state.get("validation_strategy"):
            validation_strategy_payload = summarize_validation_strategy(
                suggest_validation_strategy(
                    list(runtime_state.get("changed_files") or []) + list(git_diff_delta.get("untracked_files") or []),
                    BASE_DIR,
                )
            )
            runtime_state["validation_strategy"] = validation_strategy_payload.get("strategy", "")
            runtime_state["suggested_validation_commands"] = [
                item.get("command", "")
                for item in validation_strategy_payload.get("commands") or []
                if item.get("command")
            ]
            safe_append_event(
                source="opencode-runtime",
                event_type="opencode_validation_strategy_selected",
                title="Validation strategy selected",
                preview=build_validation_strategy_preview(validation_strategy_payload),
                payload=validation_strategy_payload,
                duration_ms=duration_ms,
            )
            for command in validation_strategy_payload.get("commands") or []:
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_validation_command_suggested",
                    title="Validation command suggested",
                    preview=f"command={str(command.get('command') or '')[:120]}",
                    payload={
                        "command": command.get("command", ""),
                        "reason": command.get("reason", ""),
                        "auto_run": False,
                    },
                    duration_ms=duration_ms,
                )
            for skipped in validation_strategy_payload.get("skipped_commands") or []:
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_validation_command_skipped",
                    title="Validation command skipped",
                    preview=f"reason={str(skipped.get('reason') or '')[:120]}",
                    payload={
                        "command": skipped.get("command", ""),
                        "reason": skipped.get("reason", ""),
                        "auto_run": False,
                        "strategy": validation_strategy_payload.get("strategy", ""),
                    },
                    duration_ms=duration_ms,
                )

        if isinstance(result, dict) and "error" in result:
            runtime_state["runner_error"] = result.get("error")
        else:
            runtime_state["runner_error"] = None

        validation_summary_payload = summarize_validation_summary(
            build_validation_summary_payload(runtime_state)
        )
        runtime_state["validation_result"] = validation_summary_payload.get("validation_result", "unknown")
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_validation_summary",
            title="Validation summary",
            preview=build_validation_summary_preview(validation_summary_payload),
            payload=validation_summary_payload,
            duration_ms=duration_ms,
        )

        runtime_state["model_response_sent"] = not (isinstance(result, dict) and "error" in result)
        run_summary_payload = summarize_run_summary(
            build_run_summary_payload(
                run_id=run_id,
                runtime_state=runtime_state,
                git_diff_delta=git_diff_delta,
                validation_summary=validation_summary_payload,
                result=result,
                duration_ms=duration_ms,
            )
        )
        safe_append_event(
            source="opencode-runtime",
            event_type="opencode_run_summary_generated",
            title="OpenCode run summary",
            preview=build_run_summary_preview(run_summary_payload),
            payload=run_summary_payload,
            duration_ms=duration_ms,
        )
        runtime_state["final_status"] = run_summary_payload.get("final_status", "unknown")
        runtime_state["workflow_status"] = run_summary_payload.get("workflow_status", runtime_state.get("workflow_status", "unknown"))
        runtime_state["finished_at"] = datetime.now().isoformat()

        if use_plan_build_verify:
            try:
                artifact_info = summarize_audit_artifact(
                    write_ados_run_artifacts(
                        run_id,
                        runtime_state,
                        runtime_state.get("stage_outputs") or {},
                        BASE_DIR,
                    )
                )
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_ados_run_manifest_written",
                    title="ADOS run manifest written",
                    preview=build_audit_artifact_preview(artifact_info),
                    payload=artifact_info,
                    duration_ms=duration_ms,
                )
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_ados_audit_artifact_written",
                    title="ADOS audit artifacts written",
                    preview=build_audit_artifact_preview(artifact_info),
                    payload=artifact_info,
                    duration_ms=duration_ms,
                )
            except Exception as artifact_error:
                safe_append_event(
                    source="opencode-runtime",
                    event_type="opencode_ados_audit_artifact_error",
                    title="ADOS audit artifact error",
                    preview=str(artifact_error)[:200],
                    payload={
                        "run_id": run_id,
                        "error": str(artifact_error),
                        "error_type": type(artifact_error).__name__,
                    },
                    status="warning",
                    duration_ms=duration_ms,
                )

        if use_plan_build_verify:
            if run_summary_payload.get("workflow_status"):
                runtime_state["workflow_status"] = run_summary_payload.get("workflow_status")
            emit_plan_build_verify_workflow_completed(
                runtime_state.get("workflow_info") or build_plan_build_verify_workflow_info(),
                run_summary_payload,
                duration_ms=duration_ms,
            )
        else:
            emit_ados_workflow_completed_events(
                safe_append_event,
                runtime_state.get("workflow_info") or build_ados_workflow_info(runtime_state.get("selected_agent")),
                status=str(run_summary_payload.get("workflow_status") or run_summary_payload.get("final_status") or "unknown"),
                duration_ms=duration_ms,
                completed_stages_override=run_summary_payload.get("completed_stages"),
                failed_stages_override=run_summary_payload.get("failed_stages"),
                skipped_stages_override=runtime_state.get("skipped_stages"),
            )

        if isinstance(result, dict) and "error" in result:
            safe_append_event(
                source="devtools-radar-api",
                event_type="model_response_error",
                title="模型請求失敗",
                preview=str(result.get("error")),
                level="error",
                payload={
                    "error": str(result.get("error")),
                    "result_keys": list(result.keys()),
                },
                status="error",
                duration_ms=duration_ms,
            )

            finish_run(run_id, status="error", duration_ms=duration_ms)

            return JSONResponse(status_code=500, content=result)

        result = coerce_response_tool_calls(result)

        response_summary = summarize_chat_response(result)
        tool_calls_summary = summarize_tool_calls_from_response(result)

        duration_ms = int((time.perf_counter() - started_at) * 1000)

        safe_append_event(
            source="devtools-radar-api",
            event_type="model_response_sent",
            title="送出模型回覆",
            preview=(
                f"duration_ms={duration_ms} "
                f"stream={req.stream} "
                f"tool_calls={len(tool_calls_summary)}"
            ),
            payload=response_summary,
            duration_ms=duration_ms,
        )

        if tool_calls_summary:
            safe_append_event(
                source="devtools-radar-api",
                event_type="mcp_tool_calls_requested",
                title="模型要求工具呼叫",
                preview=f"tool_calls={len(tool_calls_summary)}",
                payload={
                    "tool_calls_count": len(tool_calls_summary),
                    "tool_calls": tool_calls_summary,
                },
                duration_ms=duration_ms,
            )

            for index, tool_call_summary in enumerate(tool_calls_summary):
                safe_append_event(
                    source="mcp-runtime",
                    event_type="mcp_tool_call_requested",
                    title="MCP 工具呼叫被要求",
                    preview=(
                        f"tool={tool_call_summary.get('name')} "
                        f"index={index}"
                    ),
                    payload={
                        "index": index,
                        "tool_call": tool_call_summary,
                    },
                    duration_ms=duration_ms,
                )

        if req.stream:
            safe_append_event(
                source="devtools-radar-api",
                event_type="model_streaming_response_built",
                title="建立串流回覆",
                preview="stream=true",
                payload={
                    "stream": True,
                    "source_response_id": result.get("id") if isinstance(result, dict) else None,
                    "note": "stream body is generated from summarized chat response",
                },
                duration_ms=duration_ms,
            )

            finish_run(run_id, status="completed", duration_ms=duration_ms)

            return build_streaming_response_from_chat_response(result)

        finish_run(run_id, status="completed", duration_ms=duration_ms)

        return JSONResponse(content=result)

    except Exception as error:
        duration_ms = int((time.perf_counter() - started_at) * 1000)

        safe_append_event(
            source="mcp-runtime",
            event_type="mcp_loop_error",
            title="MCP loop 例外",
            preview=str(error),
            level="error",
            payload={
                "error": str(error),
                "error_type": type(error).__name__,
            },
            status="error",
            duration_ms=duration_ms,
        )

        safe_append_event(
            source="devtools-radar-api",
            event_type="model_response_error",
            title="模型請求例外",
            preview=str(error),
            level="error",
            payload=summarize_error(error),
            status="error",
            duration_ms=duration_ms,
        )

        if run_id:
            finish_run(run_id, status="error", duration_ms=duration_ms)

        raise

    finally:
        if run_token is not None:
            reset_current_run_id(run_token)
