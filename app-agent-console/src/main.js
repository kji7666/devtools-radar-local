import "./styles.css";

const channels = [
  { id: "api", name: "API 請求", icon: "◆", count: 3 },
  { id: "opencode", name: "OpenCode 執行流程", icon: "◇", count: 4 },
  { id: "model", name: "模型訊息", icon: "●", count: 3 },
  { id: "mcp", name: "MCP 工具呼叫", icon: "▲", count: 2 },
  { id: "config", name: "設定檔編輯", icon: "▣", count: 2 },
  { id: "errors", name: "錯誤紀錄", icon: "!", count: 1 }
];

const events = [
  {
    id: "evt-1",
    channel: "api",
    time: "20:51:12",
    source: "API",
    level: "資訊",
    title: "POST /v1/chat/completions",
    preview: "模型=devtools-radar/chatgpt-web-local，stream=false，messages=4，tools=6",
    full: "收到 OpenAI-compatible chat completion request。此事件記錄 endpoint、model、stream、messages 數量與 tools 數量。第一版僅顯示 mock data，尚未接真後端。",
    payload: { endpoint: "/v1/chat/completions", model: "devtools-radar/chatgpt-web-local", stream: false, messages: 4, tools: 6, redaction: "enabled" }
  },
  {
    id: "evt-2",
    channel: "opencode",
    time: "20:51:13",
    source: "OpenCode",
    level: "資訊",
    title: "載入指令檔",
    preview: ".opencode/command/check.md",
    full: "OpenCode runtime 載入 command、agent、skill 與 AGENTS.md。後續真實版本會記錄哪些 .md 被放入 context。",
    payload: { command: ".opencode/command/check.md", agent: "default", skillCount: 2, contextFiles: ["AGENTS.md", ".opencode/command/check.md"] }
  },
  {
    id: "evt-3",
    channel: "model",
    time: "20:51:15",
    source: "模型請求",
    level: "已遮蔽",
    title: "Prompt 預覽",
    preview: "系統訊息、使用者訊息與工具定義。敏感資訊已遮蔽。",
    full: "完整 prompt 預設不直接展開，避免畫面過長與洩漏敏感資訊。點擊展開後仍只顯示已遮蔽版本。API key、cookie、.env 內容都應顯示為 ***已遮蔽***。",
    payload: { redaction: "masked", apiKey: "sk-***已遮蔽***", cookie: "***已遮蔽***", promptPreview: "請檢查目前專案狀態...", messages: [{ role: "system", content: "你是本地工具代理。" }, { role: "user", content: "請檢查目前專案狀態。" }] }
  },
  {
    id: "evt-4",
    channel: "model",
    time: "20:51:18",
    source: "模型回覆",
    level: "資訊",
    title: "模型要求呼叫工具",
    preview: "tool_call: filesystem.read_file",
    full: "模型沒有直接回答，而是要求呼叫 filesystem.read_file。這表示 native tool calling 流程正在運作。",
    payload: { finishReason: "tool_calls", toolCalls: [{ name: "filesystem.read_file", arguments: { path: "AGENTS.md" } }] }
  },
  {
    id: "evt-5",
    channel: "mcp",
    time: "20:51:20",
    source: "工具執行器",
    level: "資訊",
    title: "執行 MCP 工具",
    preview: "filesystem.read_file path=AGENTS.md",
    full: "工具執行器收到模型要求後，檢查權限與 root policy。讀取 AGENTS.md 不需要人工批准，因此直接執行。",
    payload: { tool: "filesystem.read_file", path: "AGENTS.md", approval: "不需要批准", status: "成功", durationMs: 82 }
  },
  {
    id: "evt-6",
    channel: "config",
    time: "20:52:02",
    source: "設定檔編輯器",
    level: "差異",
    title: "opencode.jsonc 已變更",
    preview: "儲存前需要先檢查 diff。",
    full: "設定檔可編輯，但儲存前必須顯示 diff。第一版只做 mock，不會寫入任何真實檔案。",
    payload: { file: "opencode.jsonc", before: "model: old-model", after: "model: devtools-radar/chatgpt-web-local" }
  },
  {
    id: "evt-7",
    channel: "errors",
    time: "20:53:44",
    source: "執行環境",
    level: "錯誤",
    title: "Mock 錯誤範例",
    preview: "MCP 工具逾時。點擊後可查看原始 metadata。",
    full: "這是一個錯誤事件範例。未來真實版本會記錄錯誤 stack、request id、run id、duration、是否可重試。",
    payload: { error: "MCP timeout", durationMs: 30000, retryable: true, runId: "run_20260616_205112" }
  }
];

const configFiles = [
  "AGENTS.md",
  "opencode.jsonc",
  ".opencode/command/check.md",
  ".opencode/agent/default.md",
  ".opencode/skill/review.md",
  "docs/spec/runtime-log.md"
];

const expanded = new Set();

const state = {
  channel: "api",
  event: "evt-1",
  inspectorTab: "overview",
  query: ""
};

function getCurrentChannel() {
  return channels.find((channel) => channel.id === state.channel) || channels[0];
}

function getCurrentEvent() {
  return events.find((event) => event.id === state.event) || visibleEvents()[0] || events[0];
}

function visibleEvents() {
  const query = state.query.trim().toLowerCase();
  return events.filter((event) => {
    const sameChannel = event.channel === state.channel;
    const text = `${event.source} ${event.title} ${event.preview} ${event.full}`.toLowerCase();
    return sameChannel && (!query || text.includes(query));
  });
}

function renderChannels() {
  return channels.map((channel) => `<button class="channel ${state.channel === channel.id ? "active" : ""}" data-channel="${channel.id}"><span>${channel.icon}</span><span>${channel.name}</span><b>${channel.count}</b></button>`).join("");
}

function renderRunSummary() {
  const total = events.length;
  const errors = events.filter((event) => event.level === "錯誤").length;
  const masked = events.filter((event) => event.level === "已遮蔽").length;
  const tools = events.filter((event) => event.channel === "mcp").length;
  return `<section class="summary-grid"><div><strong>${total}</strong><span>事件</span></div><div><strong>${tools}</strong><span>工具</span></div><div><strong>${masked}</strong><span>遮蔽</span></div><div><strong>${errors}</strong><span>錯誤</span></div></section>`;
}

function renderTimeline() {
  const list = visibleEvents();
  if (list.length === 0) {
    return `<div class="empty">沒有符合搜尋條件的事件。</div>`;
  }

  return list.map((event) => {
    const isExpanded = expanded.has(event.id);
    return `<article class="event ${state.event === event.id ? "selected" : ""}" data-event="${event.id}"><div class="event-avatar">${event.source.slice(0, 2)}</div><div class="event-body"><div class="event-head"><strong>${event.source}</strong><span>${event.time}</span><em class="level">${event.level}</em></div><h3>${event.title}</h3><p>${event.preview}</p>${isExpanded ? `<div class="full-block">${event.full}</div>` : ""}<div class="event-actions"><button class="mini-button" data-toggle="${event.id}">${isExpanded ? "收合" : "展開"}</button><button class="mini-button" data-event-jump="${event.id}">查看細節</button></div></div></article>`;
  }).join("");
}

function renderInspector() {
  const event = getCurrentEvent();
  const tabs = [
    { id: "overview", name: "總覽" },
    { id: "payload", name: "資料內容" },
    { id: "raw", name: "原始 JSON" }
  ];

  let body = "";
  if (state.inspectorTab === "overview") {
    body = `<div class="kv"><div><span>來源</span><strong>${event.source}</strong></div><div><span>時間</span><strong>${event.time}</strong></div><div><span>等級</span><strong>${event.level}</strong></div><div><span>Channel</span><strong>${getCurrentChannel().name}</strong></div></div><div class="full-block">${event.full}</div>`;
  } else if (state.inspectorTab === "payload") {
    body = `<pre>${JSON.stringify(event.payload, null, 2)}</pre>`;
  } else {
    body = `<pre>${JSON.stringify(event, null, 2)}</pre>`;
  }

  return `<section class="inspector-card"><h2>${event.title}</h2><p class="muted">${event.source} · ${event.time}</p><div class="tabs">${tabs.map((tab) => `<button class="${state.inspectorTab === tab.id ? "active-tab" : ""}" data-tab="${tab.id}">${tab.name}</button>`).join("")}</div>${body}</section>`;
}

function renderConfigMock() {
  return `<section class="config-card"><h2>設定檔樹</h2><p>目前是 mock 編輯器，不會真的寫入檔案。</p><div class="file-tree">${configFiles.map((file) => `<button>${file}</button>`).join("")}</div><h2>設定檔差異預覽</h2><div class="diff"><div><h4>修改前</h4><pre>- model: old-model&#10;- apiKey: sk-live-secret&#10;- timeout: 30000</pre></div><div><h4>修改後</h4><pre>+ model: devtools-radar/chatgpt-web-local&#10;+ apiKey: ***已遮蔽***&#10;+ timeout: 600000</pre></div></div><button class="primary">儲存前先檢查差異</button></section>`;
}

function render() {
  const currentChannel = getCurrentChannel();
  document.querySelector("#app").innerHTML = `<div class="shell"><aside class="rail"><div class="logo">DR</div><button class="rail-item active">API</button><button class="rail-item">OC</button><button class="rail-item">MCP</button><button class="rail-item">設定</button></aside><aside class="sidebar"><h1>DevTools Radar</h1><p class="status">● API 在線上 · mock 模式</p><h2>執行觀察</h2>${renderChannels()}<h2>執行紀錄</h2><button class="channel"><span>◎</span><span>最新執行</span><b>現在</b></button><button class="channel"><span>◌</span><span>歷史紀錄</span><b>12</b></button></aside><main class="timeline"><header><div><h2># ${currentChannel.name}</h2><p>Runtime 觀察預覽。Prompt 與 Response 預設只顯示摘要，點開才看完整內容。</p></div><button class="ghost">Mock 資料</button></header><section class="toolbar"><input id="searchBox" class="search-box" value="${state.query}" placeholder="搜尋目前 channel，例如：模型、tool、timeout" /><button id="clearSearch" class="ghost">清除</button></section><section class="events">${renderRunSummary()}${renderTimeline()}${state.channel === "config" ? renderConfigMock() : ""}</section><footer><input value="/搜尋 模型請求" readonly /><button>執行</button></footer></main><aside class="inspector">${renderInspector()}</aside></div>`;

  document.querySelectorAll(".channel[data-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      state.channel = button.dataset.channel;
      state.query = "";
      state.inspectorTab = "overview";
      const first = visibleEvents()[0];
      state.event = first ? first.id : events[0].id;
      render();
    });
  });

  document.querySelectorAll(".event[data-event]").forEach((node) => {
    node.addEventListener("click", () => {
      state.event = node.dataset.event;
      render();
    });
  });

  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const id = button.dataset.toggle;
      if (expanded.has(id)) expanded.delete(id);
      else expanded.add(id);
      state.event = id;
      render();
    });
  });

  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.inspectorTab = button.dataset.tab;
      render();
    });
  });

  const searchBox = document.querySelector("#searchBox");
  searchBox.addEventListener("input", () => {
    state.query = searchBox.value;
    const first = visibleEvents()[0];
    if (first) state.event = first.id;
    render();
  });

  document.querySelector("#clearSearch").addEventListener("click", () => {
    state.query = "";
    render();
  });
}

render();