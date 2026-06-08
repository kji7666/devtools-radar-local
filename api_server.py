from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict


BASE_DIR = Path(__file__).resolve().parent
MAIN_PY = BASE_DIR / "main.py"
OUTPUT_TXT = BASE_DIR / "output.txt"
API_TMP_DIR = BASE_DIR / "api_tmp"
API_LOG_DIR = BASE_DIR / "logs"
MCP_CONFIG_PATH = BASE_DIR / "mcp_servers.json"

API_TMP_DIR.mkdir(exist_ok=True)
API_LOG_DIR.mkdir(exist_ok=True)

DEFAULT_MODEL = "chatgpt-web-local"
SERVER_VERSION = "0.2.0"
MAX_TOOL_LOOPS = 5

runner_lock = asyncio.Lock()


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
    command: str
    args: list[str] = []
    env: dict[str, str] = {}


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
6. 最終回答請盡量保留 Markdown。
""".strip()


def build_api_prompt(
    messages: list[ChatMessage],
    tools: Optional[list[dict[str, Any]]],
    tool_choice: Any,
) -> str:
    tools_prompt = build_tools_prompt(tools, tool_choice)
    messages_prompt = build_messages_prompt(messages)

    parts = []

    parts.append(
        """
你是透過本機 API 包裝的 ChatGPT Web UI。
請根據下列對話內容回答。
請保留 Markdown 格式。
""".strip()
    )

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
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "roots": {
                            "listChanged": False
                        },
                        "sampling": {}
                    },
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION
                    }
                },
            )
        except Exception:
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "devtools-radar-local-api",
                        "version": SERVER_VERSION
                    }
                },
            )

        await self.notify("notifications/initialized", {})
        self.initialized = True

    async def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload = {
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

            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }

            if params is not None:
                payload["params"] = params

            await self._write_message(payload)

            while True:
                message = await self._read_message()

                if message.get("id") != request_id:
                    continue

                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))

                return message.get("result")

    async def _write_message(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP server {self.name} is not running")

        # MCP stdio transport uses newline-delimited JSON-RPC messages.
        # Do not use LSP-style Content-Length headers here.
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


class McpManager:
    def __init__(self):
        self.clients: dict[str, McpStdioClient] = {}
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
            except Exception:
                continue

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
                    "properties": {}
                }

                description = tool.get("description") or f"MCP tool {original_name} from server {server_name}"

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

        info = self.tool_map[api_tool_name]
        client: McpStdioClient = info["client"]
        original_tool_name = info["tool"]

        result = await client.call_tool(original_tool_name, arguments)
        return mcp_result_to_text(result)


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


mcp_manager = McpManager()


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

    for loop_index in range(MAX_TOOL_LOOPS):
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
    description="OpenAI-compatible local API backed by ChatGPT Web UI automation with MCP tool support.",
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


@app.get("/v1/mcp/servers")
async def list_mcp_servers(request: Request) -> dict[str, Any]:
    await verify_auth(request)
    await mcp_manager.ensure_loaded()

    return {
        "config_path": str(MCP_CONFIG_PATH),
        "servers": [
            {
                "name": name,
                "running": client.process is not None and client.process.returncode is None,
                "initialized": client.initialized,
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