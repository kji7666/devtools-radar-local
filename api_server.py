from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import os
import re
import shutil
import sys
import time
import uuid
import httpx
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


BASE_DIR = Path(__file__).resolve().parent

MAIN_PY = BASE_DIR / "main.py"
OUTPUT_TXT = BASE_DIR / "output.txt"

API_TMP_DIR = BASE_DIR / "api_tmp"
API_LOG_DIR = BASE_DIR / "logs"

MCP_CONFIG_PATH = BASE_DIR / "mcp_servers.json"
MCP_SECURITY_PATH = BASE_DIR / "mcp_security.json"

MCP_AUDIT_LOG = API_LOG_DIR / "mcp_audit.log"
MCP_SECURITY_LOG = API_LOG_DIR / "mcp_security.log"
MCP_PENDING_LOG = API_LOG_DIR / "mcp_pending.log"

API_TMP_DIR.mkdir(exist_ok=True)
API_LOG_DIR.mkdir(exist_ok=True)

DEFAULT_MODEL = "chatgpt-web-local"
SERVER_VERSION = "0.4.0"
MAX_TOOL_LOOPS = 5

runner_lock = asyncio.Lock()
pending_mcp_calls: dict[str, dict[str, Any]] = {}

def load_pending_mcp_calls_from_log() -> None:
    """
    Rebuild pending_mcp_calls from append-only logs/mcp_pending.log.
    The log stores multiple versions of the same pending id.
    We keep the latest record per id.
    """
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
    """
    Rewrite mcp_pending.log with latest records only.
    Useful after many approvals.
    """
    if not pending_mcp_calls:
        return

    with MCP_PENDING_LOG.open("w", encoding="utf-8") as f:
        for record in pending_mcp_calls.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

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
    mcp: Optional[dict[str, Any]] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "local"


class McpServerConfig(BaseModel):
    enabled: bool = True

    # stdio or streamable_http
    transport: str = "stdio"

    # stdio fields
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}

    # streamable_http fields
    url: str = ""
    headers: dict[str, str] = {}
    timeout_seconds: int = 30


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


def read_output_text() -> str:
    if not OUTPUT_TXT.exists():
        return ""
    return OUTPUT_TXT.read_text(encoding="utf-8", errors="replace")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


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
4. 如果使用工具可以更準確回答，優先呼叫工具。
5. 如果使用者已提供 tool_result，請根據 tool_result 給最終回答。
6. 如果工具因安全策略被拒絕，請向使用者說明原因，不要假裝已執行。
7. 如果工具回傳 pending_id，代表該工具需要人工確認，請清楚告訴使用者 pending_id。
8. 最終回答請盡量保留 Markdown。
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


def try_parse_json(text: str) -> Any:
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    code_block = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if code_block:
        try:
            return json.loads(code_block.group(1).strip())
        except Exception:
            pass

    first_obj = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if first_obj:
        try:
            return json.loads(first_obj.group(1))
        except Exception:
            pass

    first_array = re.search(r"(\[.*\])", text, flags=re.DOTALL)
    if first_array:
        try:
            return json.loads(first_array.group(1))
        except Exception:
            pass

    return None


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
        try:
            arguments_obj = json.loads(arguments)
        except Exception:
            arguments_obj = {"input": arguments}
    elif isinstance(arguments, dict):
        arguments_obj = arguments
    else:
        arguments_obj = {"input": arguments}

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
    tool_calls_block = extract_tag(text, "tool_calls")
    if tool_calls_block:
        parsed = try_parse_json(tool_calls_block)
        if isinstance(parsed, list):
            return "", [normalize_tool_call(x) for x in parsed if isinstance(x, dict)]

    tool_call_block = extract_tag(text, "tool_call")
    if tool_call_block:
        parsed = try_parse_json(tool_call_block)
        if isinstance(parsed, dict):
            return "", [normalize_tool_call(parsed)]

    final_block = extract_tag(text, "final")
    if final_block is not None:
        return final_block, None

    return text.strip(), None


async def run_main_with_prompt(prompt: str, timeout_seconds: int = 900) -> tuple[int, str, str, str]:
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
            return 124, "", f"Timeout after {timeout_seconds} seconds", read_output_text()

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return process.returncode or 0, stdout, stderr, read_output_text()

    finally:
        try:
            prompt_file.unlink(missing_ok=True)
        except Exception:
            pass


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
                "tool_policies": [],
            }
            return

        try:
            self.config = json.loads(MCP_SECURITY_PATH.read_text(encoding="utf-8"))
        except Exception as error:
            self.config = {
                "enabled": True,
                "default_action": "deny",
                "tool_timeout_seconds": 20,
                "max_tool_output_chars": 12000,
                "audit_log_enabled": True,
                "allowed_roots": [str(BASE_DIR)],
                "tool_policies": [],
            }

            with MCP_SECURITY_LOG.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] failed to load mcp_security.json: {error}\n")

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
            path_decision = self.check_paths(arguments)

            if not path_decision.allowed:
                return path_decision

            return McpSecurityDecision(
                allowed=True,
                action="allow",
                reason=reason,
            )

        if action == "confirm":
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

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        """
        Handle requests sent from MCP server to this client.
        Some servers, like filesystem, may request roots/list.
        """
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
                        "roots": {
                            "listChanged": False,
                        },
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
                        "roots": {
                            "listChanged": False,
                        },
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
                # Some HTTP MCP servers may return notifications before the target response.
                # This minimal implementation expects one response per POST.
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
        """
        Minimal SSE parser.
        It returns the first JSON object found in data: lines.
        """
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


def create_pending_mcp_call(
    tool_name: str,
    arguments: dict[str, Any],
    decision: McpSecurityDecision,
) -> dict[str, Any]:
    pending_id = f"mcp_pending_{uuid.uuid4().hex[:16]}"
    now = datetime.now().isoformat(timespec="seconds")

    record = {
        "id": pending_id,
        "tool": tool_name,
        "arguments": arguments,
        "decision": decision.model_dump(),
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

                openai_tools.append(openai_tool)
                self.tool_map[api_name] = {
                    "server": server_name,
                    "tool": original_name,
                    "client": client,
                    "schema": input_schema,
                    "description": description,
                }

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
        client: McpStdioClient = info["client"]
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
        client: McpStdioClient = info["client"]
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
mcp_manager = McpManager()

load_pending_mcp_calls_from_log()
compact_pending_mcp_log()

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


async def run_chat_with_mcp_loop(req: ChatCompletionRequest) -> dict[str, Any]:
    messages = [ChatMessage(**m.model_dump()) for m in req.messages]

    mcp_tools = await mcp_manager.list_tools()
    request_tools = req.tools or []

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

        executable_calls = []
        external_calls = []

        for call in tool_calls:
            name = tool_call_name(call)
            if mcp_manager.is_mcp_tool(name):
                executable_calls.append(call)
            else:
                external_calls.append(call)

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
        "with MCP tool support, security layer, and approval workflow."
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
            }
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

    # Validate before writing.
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
                    if client.config.transport == "streamable_http" and client.http is not None
                    else client.process is not None and client.process.returncode is None
                    if hasattr(client, "process")
                    else False
                ),
                "initialized": client.initialized,
                "url": getattr(client.config, "url", ""),
                "command": getattr(client.config, "command", ""),
                "args": getattr(client.config, "args", []),
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


@app.post("/v1/mcp/security/reload")
async def reload_mcp_security(request: Request) -> dict[str, Any]:
    await verify_auth(request)

    mcp_security.reload()

    return {
        "status": "ok",
        "message": "MCP security config reloaded",
        "config_path": str(MCP_SECURITY_PATH),
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


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request) -> JSONResponse:
    await verify_auth(request)

    if req.stream:
        raise HTTPException(
            status_code=400,
            detail="stream=true is not supported yet. Please use stream=false.",
        )

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is required")

    async with runner_lock:
        result = await run_chat_with_mcp_loop(req)

    if "error" in result:
        return JSONResponse(status_code=500, content=result)

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