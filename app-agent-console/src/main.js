import "./styles.css";

const channels = [
  { id: "api", name: "api-requests", icon: "◆", count: 3 },
  { id: "opencode", name: "opencode-runtime", icon: "◇", count: 4 },
  { id: "model", name: "model-messages", icon: "●", count: 2 },
  { id: "mcp", name: "mcp-tools", icon: "▲", count: 2 },
  { id: "config", name: "config-editor", icon: "▣", count: 1 },
  { id: "errors", name: "errors", icon: "!", count: 1 }
];

const events = [
  { id: "evt-1", channel: "api", time: "20:51:12", source: "API", level: "info", title: "POST /v1/chat/completions", preview: "model=devtools-radar/chatgpt-web-local stream=false messages=4 tools=6", payload: { endpoint: "/v1/chat/completions", model: "devtools-radar/chatgpt-web-local", stream: false, messages: 4, tools: 6 } },
  { id: "evt-2", channel: "opencode", time: "20:51:13", source: "OpenCode", level: "info", title: "Loaded command", preview: ".opencode/command/check.md", payload: { command: ".opencode/command/check.md", agent: "default", skillCount: 2 } },
  { id: "evt-3", channel: "model", time: "20:51:15", source: "Model Request", level: "masked", title: "Prompt preview only", preview: "System + user + tool definitions. Sensitive fields are redacted.", payload: { redaction: "masked", apiKey: "sk-***REDACTED***", cookie: "***REDACTED***", promptPreview: "請檢查目前專案狀態..." } },
  { id: "evt-4", channel: "mcp", time: "20:51:20", source: "Tool Runner", level: "info", title: "Tool call requested", preview: "filesystem.read_file path=AGENTS.md", payload: { tool: "filesystem.read_file", path: "AGENTS.md", approval: "not_required", status: "ok" } },
  { id: "evt-5", channel: "config", time: "20:52:02", source: "Config Editor", level: "diff", title: "opencode.jsonc changed", preview: "Save blocked until diff is reviewed.", payload: { file: "opencode.jsonc", before: "model: old-model", after: "model: devtools-radar/chatgpt-web-local" } },
  { id: "evt-6", channel: "errors", time: "20:53:44", source: "Runtime", level: "error", title: "Mock error example", preview: "MCP tool timeout. Click to inspect raw metadata.", payload: { error: "MCP timeout", durationMs: 30000, retryable: true } }
];

const state = { channel: "api", event: "evt-1" };

function visibleEvents() {
  if (state.channel === "config") return events.filter((event) => event.channel === "config");
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
  return `<section class="inspector-card"><h2>${event.title}</h2><p class="muted">${event.source} · ${event.time}</p><div class="tabs"><button>Overview</button><button>Payload</button><button>Raw JSON</button></div><pre>${JSON.stringify(event.payload, null, 2)}</pre></section>`;
}

function renderConfigMock() {
  return `<section class="config-card"><h2>Config diff preview</h2><p>Mock editor only. No file is written in this version.</p><div class="diff"><div><h4>Before</h4><pre>- model: old-model&#10;- apiKey: sk-live-secret</pre></div><div><h4>After</h4><pre>+ model: devtools-radar/chatgpt-web-local&#10;+ apiKey: ***REDACTED***</pre></div></div><button class="primary">Review diff before save</button></section>`;
}

function render() {
  document.querySelector("#app").innerHTML = `<div class="shell"><aside class="rail"><div class="logo">DR</div><button class="rail-item active">API</button><button class="rail-item">OC</button><button class="rail-item">MCP</button><button class="rail-item">CFG</button></aside><aside class="sidebar"><h1>DevTools Radar</h1><p class="status">● API online · mock mode</p><h2>Runtime</h2>${renderChannels()}<h2>Runs</h2><button class="channel"><span>◎</span><span>latest-run</span><b>now</b></button><button class="channel"><span>◌</span><span>archived-runs</span><b>12</b></button></aside><main class="timeline"><header><div><h2># ${channels.find((channel) => channel.id === state.channel).name}</h2><p>Runtime observability preview. Prompt and response are collapsed by default.</p></div><button class="ghost">Mock data</button></header><section class="events">${renderTimeline()}${state.channel === "config" ? renderConfigMock() : ""}</section><footer><input value="/search model_request" readonly /><button>Run</button></footer></main><aside class="inspector">${renderInspector()}</aside></div>`;

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
