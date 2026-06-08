# DevTools Radar Local

DevTools Radar Local is a local-first Windows desktop automation tool for collecting, organizing, and summarizing developer-tool news using ChatGPT Web UI automation.

It is designed for developers who want to track emerging open-source tools, AI coding tools, CLI utilities, frontend frameworks, DevOps tools, security tools, and community trends on a daily schedule.

The app provides a Vue + Electron desktop interface, a Python automation runner, task scheduling, Markdown output, and run history.

---

## Why This Project Exists

The main goal of this project is to automatically collect and summarize computer science and developer-tool updates every day.

Example use cases:

- Track trending open-source repositories
- Discover new free or open-source developer tools
- Monitor AI coding tools and agent frameworks
- Scan CLI, DevOps, frontend, security, and local-first tooling trends
- Run multiple prompts step by step
- Use ChatGPT context to progressively consolidate information
- Generate a final Traditional Chinese Markdown report

---

## Features

- Local Windows desktop app
- Vue + Electron UI
- Python runner
- Microsoft Edge CDP automation
- ChatGPT Web UI interaction
- Multi-task prompt workflow
- JSON-based task management
- Windows Task Scheduler integration
- Run history
- Markdown output
- Date/time-based output filenames
- Traditional Chinese report generation
- Local-first design

---

## Architecture

```text
DevTools Radar Local
│
├─ Electron + Vue Desktop UI
│  ├─ Dashboard
│  ├─ Chat
│  ├─ Tasks
│  ├─ Outputs
│  ├─ Runs
│  ├─ Logs
│  └─ Settings
│
├─ Electron Main Process
│  ├─ Reads and writes local files
│  ├─ Calls Python runner
│  ├─ Creates Windows scheduled tasks
│  └─ Bridges UI and local system
│
├─ Python Runner
│  ├─ Connects to Edge through CDP
│  ├─ Sends prompts to ChatGPT Web UI
│  ├─ Waits for responses
│  ├─ Extracts Markdown-like output
│  └─ Writes result files
│
└─ Windows Task Scheduler
   └─ Runs scheduled task JSON files



下面是你現在這個 **DevTools Radar Local API** 的使用整理。它本質上是：

> 用 OpenAI-compatible API 規格，把你本機控制 ChatGPT Web UI 的能力包成一個 local model endpoint。

---

# 1. 啟動 API

在專案根目錄：

```powershell
cd D:\side_project\auto_gpt
.\run_api.bat
```

或手動啟動：

```powershell
cd D:\side_project\auto_gpt
.\.venv\Scripts\Activate.ps1

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

python api_server.py --host 127.0.0.1 --port 8788
```

啟動成功會看到：

```text
Uvicorn running on http://127.0.0.1:8788
```

---

# 2. API Base URL

OpenAI-compatible base URL：

```text
http://127.0.0.1:8788/v1
```

模型名稱：

```text
chatgpt-web-local
```

完整 chat completions endpoint：

```text
POST http://127.0.0.1:8788/v1/chat/completions
```

models endpoint：

```text
GET http://127.0.0.1:8788/v1/models
```

health check：

```text
GET http://127.0.0.1:8788/health
```

---

# 3. 檢查 API 狀態

```powershell
Invoke-RestMethod http://127.0.0.1:8788/health
```

預期會看到類似：

```json
{
  "status": "ok",
  "main_py_exists": true,
  "output_txt_exists": true,
  "base_dir": "D:\\side_project\\auto_gpt",
  "model": "chatgpt-web-local"
}
```

查模型：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/v1/models
```

---

# 4. 基本聊天呼叫

PowerShell 測試：

```powershell
$bodyObj = @{
  model = "chatgpt-web-local"
  messages = @(
    @{
      role = "user"
      content = "請用繁體中文用三個 bullet points 說明 MCP 是什麼。"
    }
  )
  stream = $false
}

$body = $bodyObj | ConvertTo-Json -Depth 20
$utf8Body = [System.Text.Encoding]::UTF8.GetBytes($body)

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8788/v1/chat/completions" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $utf8Body
```

預期回傳格式接近 OpenAI：

```json
{
  "id": "chatcmpl_xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "chatgpt-web-local",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "- MCP 是...\n- ..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 80,
    "total_tokens": 180
  }
}
```

---

# 5. curl 使用方式

```bash
curl http://127.0.0.1:8788/v1/chat/completions ^
  -H "Content-Type: application/json; charset=utf-8" ^
  -d "{\"model\":\"chatgpt-web-local\",\"messages\":[{\"role\":\"user\",\"content\":\"請用繁體中文說明 MCP 是什麼\"}],\"stream\":false}"
```

Windows PowerShell 比較建議用前面的 `Invoke-RestMethod + UTF8 bytes`，中文比較穩。

---

# 6. Python client 使用方式

如果使用 OpenAI Python SDK：

```powershell
pip install openai
```

範例：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8788/v1",
    api_key="local-anything",
)

response = client.chat.completions.create(
    model="chatgpt-web-local",
    messages=[
        {
            "role": "user",
            "content": "請用繁體中文用三個 bullet points 說明 MCP 是什麼。",
        }
    ],
    stream=False,
)

print(response.choices[0].message.content)
```

---

# 7. Node.js client 使用方式

```bash
npm install openai
```

```ts
import OpenAI from "openai"

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8788/v1",
  apiKey: "local-anything",
})

const response = await client.chat.completions.create({
  model: "chatgpt-web-local",
  messages: [
    {
      role: "user",
      content: "請用繁體中文說明 MCP 是什麼。",
    },
  ],
  stream: false,
})

console.log(response.choices[0].message.content)
```

---

# 8. Function Calling / Tools 使用方式

你的 API 支援基礎 OpenAI-compatible `tools` 格式。

Request：

```powershell
$bodyObj = @{
  model = "chatgpt-web-local"
  messages = @(
    @{
      role = "user"
      content = "請查詢最近熱門的 developer tools。"
    }
  )
  tools = @(
    @{
      type = "function"
      function = @{
        name = "search_web"
        description = "Search the web for recent public information."
        parameters = @{
          type = "object"
          properties = @{
            query = @{
              type = "string"
              description = "Search query"
            }
          }
          required = @("query")
        }
      }
    }
  )
  tool_choice = "auto"
  stream = $false
}

$body = $bodyObj | ConvertTo-Json -Depth 30
$utf8Body = [System.Text.Encoding]::UTF8.GetBytes($body)

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8788/v1/chat/completions" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $utf8Body
```

如果 ChatGPT 判斷需要工具，API 會嘗試把 ChatGPT 的：

```xml
<tool_call>
{"name":"search_web","arguments":{"query":"GitHub Trending developer tools today"}}
</tool_call>
```

轉成：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_xxx",
            "type": "function",
            "function": {
              "name": "search_web",
              "arguments": "{\"query\":\"GitHub Trending developer tools today\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

---

# 9. Tool result 回傳方式

第一版 API 不會自動執行 tool。
你需要由外部 agent / MCP host 執行 tool 後，再把結果送回 API。

第二次 request 範例：

```json
{
  "model": "chatgpt-web-local",
  "messages": [
    {
      "role": "user",
      "content": "請查詢最近熱門的 developer tools。"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_abc",
          "type": "function",
          "function": {
            "name": "search_web",
            "arguments": "{\"query\":\"GitHub Trending developer tools today\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc",
      "content": "這裡放 search_web 工具執行後的結果..."
    }
  ],
  "stream": false
}
```

API 會把 `tool` role 轉成 prompt 裡的 `[tool_result]`，再交給 ChatGPT 產生最終回答。

---

# 10. 支援的 OpenAI-compatible 欄位

目前支援或接受：

```text
model
messages
stream
tools
tool_choice
temperature
top_p
max_tokens
```

其中：

```text
temperature / top_p / max_tokens
```

目前只是接收，不一定能精準控制 ChatGPT Web UI。

---

# 11. 不支援的功能

目前第一版不支援：

```text
stream=true
/v1/responses
/v1/embeddings
/v1/audio
/v1/images
多工並行
真正 token streaming
自動 MCP tool loop
自動執行 function call
精準 token usage
```

`usage` 目前是粗略估算，不是真實 token count。

---

# 12. API Key 使用方式

預設不需要 API key。

如果要加本機 API key，啟動前設定：

```powershell
$env:DEVTOOLS_RADAR_API_KEY="my-local-key"
python api_server.py --host 127.0.0.1 --port 8788
```

呼叫時加 header：

```text
Authorization: Bearer my-local-key
```

PowerShell：

```powershell
$headers = @{
  Authorization = "Bearer my-local-key"
}

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8788/v1/models" `
  -Headers $headers
```

---

# 13. 併發限制

目前 API 有單一 lock：

```text
一次只處理一個 /v1/chat/completions request
```

原因是你的核心 runner 會控制同一個 ChatGPT Web UI / Edge CDP session。
這樣可以避免多個 request 同時輸入、互相污染上下文。

---

# 14. 使用前必要條件

呼叫 API 前請確認：

```text
1. Edge debug browser 已啟動
2. ChatGPT 已登入
3. main.py 可正常執行
4. output.txt 可正常寫入
5. API server 已啟動在 127.0.0.1:8788
```

建議先測：

```powershell
python main.py --prompt-text "請回覆 OK"
```

再測 API。

---

# 15. 最小接入設定

未來任何支援 OpenAI-compatible provider 的工具，可以先試：

```text
Provider: OpenAI-compatible
Base URL: http://127.0.0.1:8788/v1
Model: chatgpt-web-local
API Key: local-anything
Streaming: off
```

如果工具強制要求 streaming，先關掉 streaming；目前 `stream=true` 會回 400。

---

# 16. 建議補到 README 的 API 區塊

你可以在 README 增加這段簡化版：

````md
## Local OpenAI-Compatible API

Start the API server:

```powershell
cd D:\side_project\auto_gpt
.\run_api.bat
````

Base URL:

```text
http://127.0.0.1:8788/v1
```

Model:

```text
chatgpt-web-local
```

Example:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8788/v1",
    api_key="local-anything",
)

response = client.chat.completions.create(
    model="chatgpt-web-local",
    messages=[
        {"role": "user", "content": "請用繁體中文說明 MCP 是什麼。"}
    ],
    stream=False,
)

print(response.choices[0].message.content)
```

Current limitations:

* `stream=true` is not supported
* `/v1/responses` is not supported
* Tool calls are parsed and returned, but tools are not executed automatically yet
* Only one request is processed at a time

```

---

目前這版 API 的定位很清楚：

> **ChatGPT Web UI-backed OpenAI-compatible local endpoint。**

下一步就可以做 **MCP tool loop**：API 收到 tool call 後，自動呼叫 MCP tool，再把結果回灌給 ChatGPT 產生 final answer。
```
