import "./styles.css";

const channels = [
  { id: "api", name: "API 請求", icon: "◆", count: 3 },
  { id: "opencode", name: "OpenCode 執行流程", icon: "◇", count: 4 },
  { id: "model", name: "模型訊息", icon: "●", count: 2 },
  { id: "mcp", name: "MCP 工具呼叫", icon: "▲", count: 2 },
  { id: "config", name: "設定檔編輯", icon: "▣", count: 1 },
  { id: "errors", name: "錯誤紀錄", icon: "!", count: 1 }
];

const events = [
  { id: "evt-1", channel: "api", time: "20:51:12", source: "API", level: "資訊", title: "POST /v1/chat/completions", preview: "模型=devtools-radar/chatgpt-web-local，stream=false，messages=4，tools=6", payload: { endpoint: "/v1/chat/completions", model: "devtools-radar/chatgpt-web-local", stream: false, messages: 4, tools: 6 } },
  { id: "evt-2", channel: "opencode", time: "20:51:13", source: "OpenCode", level: "資訊", title: "載入指令檔", preview: ".opencode/command/check.md", payload: { command: ".opencode/command/check.md", agent: "default", skillCount: 2 } },
  { id: "evt-3", channel: "model", time: "20:51:15", source: "模型請求", level: "已遮蔽", title: "Prompt 預覽", preview: "系統訊息、使用者訊息與工具定義。敏感資訊已遮蔽。", payload: { redaction: "masked", apiKey: "sk-***已遮蔽***", cookie: "***已遮蔽***", promptPreview: "請檢查目前專案狀態..." } },
  { id: "evt-4", channel: "mcp", time: "20:51:20", source: "工具執行器", level: "資訊", title: "模型要求呼叫工具", preview: "filesystem.read_file path=AGENTS.md", payload: { tool: "filesystem.read_file", path: "AGENTS.md", approval: "不需要批准", status: "成功" } },
  { id: "evt-5", channel: "config", time: "20:52:02", source: "設定檔編輯器", level: "差異", title: "opencode.jsonc 已變更", preview: "儲存前需要先檢查 diff。", payload: { file: "opencode.jsonc", before: "model: old-model", after: "model: devtools-radar/chatgpt-web-local" } },
  { id: "evt-6", channel: "errors", time: "20:53:44", source: "執行環境", level: "錯誤", title: "Mock 錯誤範例", preview: "MCP 工具逾時。點擊後可查看原始 metadata。", payload: { error: "MCP timeout", durationMs: 30000, retryable: true } }
];

const state = { channel: "api", event: "evt-1" };

function visibleEvents() {
  return events.filter((event) => event.channel === state.channel);
}

function renderChannels() {
  return channels.map((channel) => `<button class="channel ${state.channel === channel.id ? "active" : ""}" data-channel="${channel.id}"><span>${channel.icon}</span><span>${channel.name}</span><b>${channel.count}</b></button>`).join("");
}

function renderTimeline() {
  const list = visibleEvents();
  return list.map((event) => `<article class="event ${state.event === event.id ? "selected" : ""}" data-event="${event.id}"><div class="event-avatar">${event.source.slice(0, 2)}</div><div class="event-body"><div class="event-head"><strong>${event.source}</strong><span>${event.time}</span><em class="level ${event.level}">${event.level}</em></div><h3>${event.title}</h3><p>${event.preview}</p></div></article>`).join("");
}

function renderInspector() {
  const fallback = visibleEvents()[0] || events[0];
  const event = events.find((item) => item.id === state.event) || fallback;
  return `<section class="inspector-card"><h2>${event.title}</h2><p class="muted">${event.source} · ${event.time}</p><div class="tabs"><button>總覽</button><button>資料內容</button><button>原始 JSON</button></div><pre>${JSON.stringify(event.payload, null, 2)}</pre></section>`;
}

function renderConfigMock() {
  return `<section class="config-card"><h2>設定檔差異預覽</h2><p>目前是 mock 編輯器，不會真的寫入檔案。</p><div class="diff"><div><h4>修改前</h4><pre>- model: old-model&#10;- apiKey: sk-live-secret</pre></div><div><h4>修改後</h4><pre>+ model: devtools-radar/chatgpt-web-local&#10;+ apiKey: ***已遮蔽***</pre></div></div><button class="primary">儲存前先檢查差異</button></section>`;
}

function render() {
  const currentChannel = channels.find((channel) => channel.id === state.channel);
  document.querySelector("#app").innerHTML = `<div class="shell"><aside class="rail"><div class="logo">DR</div><button class="rail-item active">API</button><button class="rail-item">OC</button><button class="rail-item">MCP</button><button class="rail-item">設定</button></aside><aside class="sidebar"><h1>DevTools Radar</h1><p class="status">● API 在線上 · mock 模式</p><h2>執行觀察</h2>${renderChannels()}<h2>執行紀錄</h2><button class="channel"><span>◎</span><span>最新執行</span><b>現在</b></button><button class="channel"><span>◌</span><span>歷史紀錄</span><b>12</b></button></aside><main class="timeline"><header><div><h2># ${currentChannel.name}</h2><p>Runtime 觀察預覽。Prompt 與 Response 預設只顯示摘要，點開才看完整內容。</p></div><button class="ghost">Mock 資料</button></header><section class="events">${renderTimeline()}${state.channel === "config" ? renderConfigMock() : ""}</section><footer><input value="/搜尋 模型請求" readonly /><button>執行</button></footer></main><aside class="inspector">${renderInspector()}</aside></div>`;

  document.querySelectorAll(".channel[data-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      state.channel = button.dataset.channel;
      const first = visibleEvents()[0];
      state.event = first ? first.id : events[0].id;
      render();
    });
  });

  document.querySelectorAll(".event[data-event]").forEach((button) => {
    button.addEventListener("click", () => {
      state.event = button.dataset.event;
      render();
    });
  });
}

render();