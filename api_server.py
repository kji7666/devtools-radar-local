from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
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
from runtime_event_log import append_event, read_recent_events

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

DEFAULT_MODEL = "chatgpt-web-local"
SERVER_VERSION = "0.7.0"
MAX_TOOL_LOOPS = 5

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

    return "\n\n".join(rendered).strip()

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


async def run_chat_with_mcp_loop(req: ChatCompletionRequest) -> dict[str, Any]:
    messages = [ChatMessage(**m.model_dump()) for m in req.messages]

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
        )
        last_prompt = prompt_text

        code, stdout, stderr, raw_output = await run_main_with_prompt(prompt_text)

        if code != 0:
            return {
                "error": {
                    "message": stderr or stdout or "ChatGPT Web UI runner failed",
                    "type": "runner_error",
                    "code": code,
                }
            }

        content, tool_calls = parse_assistant_output(raw_output)

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

        # OpenCode native tools, such as read/edit/bash, must be returned to
        # OpenCode as assistant.tool_calls. Do not execute them inside this API.
        if external_calls and not executable_calls:
            return make_chat_response(
                req=req,
                content="",
                tool_calls=external_calls,
                prompt_text=prompt_text,
                raw_output=raw_output,
            )

        messages.append(assistant_tool_call_message(tool_calls))

        for call in executable_calls:
            name = tool_call_name(call)
            args = tool_call_arguments(call)

            try:
                result_text = await mcp_manager.call_tool(name, args)
            except Exception as error:
                result_text = f"[MCP_TOOL_ERROR]\n{name}\n{type(error).__name__}: {error}"

            messages.append(tool_result_message(call, result_text))

        # Mixed mode: execute MCP tools internally, return OpenCode native tools
        # back to OpenCode.
        if external_calls:
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


@app.get("/v1/debug/events")
def debug_events(limit: int = 200):
    event = append_event(
        source="devtools-radar-api",
        event_type="debug_events_read",
        title="讀取 runtime events",
        preview=f"limit={limit}",
        payload={"limit": limit},
    )
    return {
        "data": read_recent_events(limit),
        "latest_event_id": event["id"],
    }
    
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
    await verify_auth(request)

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is required")

    cleanup_stale_runner_lock(max_age_seconds=900)

    async with runner_lock:
        result = await run_chat_with_mcp_loop(req)

    if "error" in result:
        return JSONResponse(status_code=500, content=result)

    result = coerce_response_tool_calls(result)

    if req.stream:
        return build_streaming_response_from_chat_response(result)

    return JSONResponse(content=result)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def main() -> None:
    import uvicorn

    args = parse_args()

    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

# =========================
# DEBUG ENDPOINTS (ADDED FIX)
# =========================

@app.get("/v1/debug/runner")
async def debug_runner():
    return {
        "locked": runner_lock.locked(),
        "runner_lock_file": str(BASE_DIR / ".runner.lock"),
        "exists": (BASE_DIR / ".runner.lock").exists(),
    }


@app.get("/v1/debug/prompt-traces")
async def debug_prompt_traces():
    p = BASE_DIR / "logs" / "prompt_traces"
    if not p.exists():
        return {"traces": []}

    out = []
    for d in sorted(p.iterdir(), reverse=True):
        if d.is_dir():
            out.append({
                "trace_id": d.name,
                "files": [f.name for f in d.iterdir()]
            })
    return {"traces": out}
