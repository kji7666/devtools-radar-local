import "./styles.css";

const app = document.querySelector("#app");

const API_PREFIX = "/api-local";

const channels = [
  { id: "api", name: "API 請求", icon: "DR" },
  { id: "opencode", name: "OpenCode 執行流程", icon: "OC" },
  { id: "model", name: "模型訊息", icon: "AI" },
  { id: "mcp", name: "MCP 工具呼叫", icon: "MC" },
  { id: "config", name: "設定檔編輯", icon: "CF" },
  { id: "error", name: "錯誤紀錄", icon: "ER" },
];

const expanded = new Set();
let flowDigestFeedbackTimer = null;
let pbvSummaryFeedbackTimer = null;

const COMPACT_FLOW_EVENT_TYPES = new Set([
  "model_request_received",
  "opencode_request_received",
  "opencode_ados_assets_detected",
  "opencode_ados_template_selected",
  "opencode_ados_template_loaded",
  "opencode_ados_template_injected",
  "opencode_skill_selection_completed",
  "opencode_skill_loaded",
  "opencode_skill_injected",
  "opencode_ados_workflow_started",
  "opencode_ados_stage_skipped",
  "opencode_ados_stage_started",
  "opencode_ados_stage_finished",
  "opencode_ados_workflow_completed",
  "opencode_ados_stage_model_call_started",
  "opencode_ados_stage_model_call_finished",
  "opencode_ados_stage_handoff_created",
  "mcp_loop_started",
  "mcp_loop_iteration_started",
  "mcp_model_output_parsed",
  "mcp_tool_calls_classified",
  "mcp_tool_started",
  "mcp_tool_finished",
  "mcp_tool_error",
  "mcp_loop_completed",
  "opencode_changed_files_detected",
  "opencode_diff_generated",
  "opencode_validation_summary",
  "opencode_run_summary_generated",
  "model_response_sent",
  "runner_error",
  "model_response_error",
]);

const state = {
  viewMode: "channel",
  selectedRunId: "latest",
  channel: "api",
  event: "",
  inspectorTab: "overview",
  query: "",
  quickFilter: "all",
  pinnedEventId: null,
  autoRefresh: true,
  sortDirection: "desc",
  eventStats: null,

  apiStatus: {
    loading: true,
    online: false,
    health: null,
    models: [],
    error: null,
  },

  runnerDebug: {
    loading: true,
    data: null,
    error: null,
  },

  runtimeEvents: {
    loading: true,
    data: [],
    error: null,
    usingMock: false,
  },

  runs: {
    loading: true,
    data: [],
    error: null,
  },

  flowDigest: {
    copied: "",
    error: "",
  },

  pbvSummary: {
    copied: "",
    error: "",
  },

  testPanel: {
    prompt: "只回答 ok",
    model: "chatgpt-web-local",
    stream: false,
    toolsEnabled: false,
    sending: false,
    response: "",
    error: "",
    durationMs: null,
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function stableStringify(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function normalizeLevel(level) {
  const value = String(level || "").toLowerCase();

  if (value === "error") return "錯誤";
  if (value === "warning" || value === "warn") return "警告";
  if (value === "debug") return "除錯";

  return "資訊";
}

function inferChannelFromRuntimeEvent(event) {
  const source = String(event.source || "").toLowerCase();
  const type = String(event.type || "").toLowerCase();
  const level = String(event.level || "").toLowerCase();

  if (level === "error" || type.includes("error")) return "error";

  if (type.includes("mcp_model_output")) return "model";

  if (source.includes("devtools-radar-api")) return "api";
  if (source.includes("opencode")) return "opencode";
  if (source.includes("mcp")) return "mcp";
  if (source.includes("model")) return "model";
  if (source.includes("config")) return "config";

  if (type.includes("model_request") || type.includes("model_response")) return "api";
  if (type.includes("opencode")) return "opencode";
  if (type.includes("mcp")) return "mcp";
  if (type.includes("tool")) return "mcp";

  return "api";
}

function mapRuntimeEventToUiEvent(event) {
  const rawType = String(event.type || "");
  const rawLevel = String(event.level || "");
  const rawStatus = String(event.status || "");

  return {
    id: String(event.id || crypto.randomUUID()),
    channel: inferChannelFromRuntimeEvent(event),
    type: rawType,
    status: rawStatus,
    durationMs: event.duration_ms,
    runId: String(event.run_id || ""),
    time: event.ts
      ? new Date(event.ts).toLocaleTimeString("zh-TW", { hour12: false })
      : "--:--:--",
    source: String(event.source || "runtime"),
    level: normalizeLevel(rawLevel),
    rawLevel,
    title: String(event.title || event.type || "Runtime Event"),
    preview: String(event.preview || ""),
    full: String(event.preview || event.title || ""),
    payload: event,
  };
}

function getRawEventPayload(event) {
  return event.payload?.payload || {};
}

function getLoopIndex(event) {
  const value = getRawEventPayload(event).loop_index;

  if (value === null || value === undefined) {
    return null;
  }

  return value;
}

function formatDuration(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }

  const ms = Number(value);

  if (!Number.isFinite(ms)) {
    return "";
  }

  if (ms < 1000) {
    return `${ms}ms`;
  }

  return `${(ms / 1000).toFixed(1)}s`;
}

function getLevelClass(event) {
  const level = String(event.rawLevel || event.level || "").toLowerCase();

  if (level.includes("error") || event.level === "錯誤") return "error";
  if (level.includes("warning") || event.level === "警告") return "warning";
  if (level.includes("debug") || event.level === "除錯") return "debug";

  return "info";
}

function getEventFamily(event) {
  const type = event.type || "";

  if (type.includes("model_request")) return "api-request";
  if (type.includes("model_response")) return "api-response";
  if (type.includes("opencode")) return "opencode";
  if (type.includes("context")) return "context";
  if (type.includes("mcp_tool_started")) return "tool-started";
  if (type.includes("mcp_tool_finished")) return "tool-finished";
  if (type.includes("mcp_tool_error")) return "tool-error";
  if (type.includes("mcp_loop")) return "mcp-loop";
  if (type.includes("mcp_model_output")) return "model-output";
  if (type.includes("mcp")) return "mcp";

  return "generic";
}

function getLifecycleBadge(event) {
  const type = event.type || "";

  if (type.includes("model_request_received")) return "REQUEST";
  if (type.includes("model_response_sent")) return "RESPONSE";
  if (type.includes("opencode_request_received")) return "OPENCODE";
  if (type.includes("opencode_context_files_detected")) return "CONTEXT";
  if (type.includes("mcp_loop_started")) return "LOOP START";
  if (type.includes("mcp_loop_completed")) return "LOOP DONE";
  if (type.includes("mcp_loop_iteration_started")) return "ITERATION";
  if (type.includes("mcp_model_output_parsed")) return "MODEL OUT";
  if (type.includes("mcp_tool_started")) return "TOOL START";
  if (type.includes("mcp_tool_finished")) return "TOOL DONE";
  if (type.includes("mcp_tool_error")) return "TOOL ERROR";
  if (type.includes("error")) return "ERROR";

  return "";
}

function getEventTypeLabel(event) {
  return event.type || event.payload?.type || "unknown_event";
}

function renderEventBadges(event) {
  const duration = formatDuration(event.durationMs);
  const lifecycle = getLifecycleBadge(event);
  const loopIndex = getLoopIndex(event);

  return `
    <div class="event-badges">
      <span class="event-type-badge">${escapeHtml(getEventTypeLabel(event))}</span>
      ${lifecycle ? `<span class="lifecycle-badge">${escapeHtml(lifecycle)}</span>` : ""}
      ${loopIndex !== null ? `<span class="loop-badge">loop #${escapeHtml(loopIndex)}</span>` : ""}
      ${duration ? `<span class="duration-badge">${escapeHtml(duration)}</span>` : ""}
      ${event.status ? `<span class="status-badge">${escapeHtml(event.status)}</span>` : ""}
    </div>
  `;
}

function getEventSource() {
  return state.runtimeEvents.data || [];
}

function getChronologicalEvents() {
  return [...getEventSource()].sort((a, b) => {
    return String(a.payload?.ts || a.time).localeCompare(String(b.payload?.ts || b.time));
  });
}

function visibleEvents() {
  const query = state.query.trim().toLowerCase();

  let list = getEventSource().filter((event) => event.channel === state.channel);

  if (state.quickFilter === "errors") {
    list = list.filter((event) => event.level === "錯誤");
  }

  if (state.quickFilter === "tools") {
    list = list.filter((event) => event.type.includes("tool") || event.type.includes("mcp"));
  }

  if (state.quickFilter === "model") {
    list = list.filter((event) => event.type.includes("model"));
  }

  if (query) {
    list = list.filter((event) => {
      const text = `${event.source} ${event.title} ${event.preview} ${event.full} ${event.channel} ${event.type}`.toLowerCase();
      return text.includes(query);
    });
  }

  if (state.sortDirection === "desc") {
    return [...list].reverse();
  }

  return list;
}

function getDisplayedEvents() {
  if (state.viewMode === "history") {
    const query = state.query.trim().toLowerCase();

    return getChronologicalEvents().filter((event) => {
      const text = `${event.source} ${event.title} ${event.preview} ${event.full} ${event.channel} ${event.type}`.toLowerCase();
      return !query || text.includes(query);
    });
  }

  return visibleEvents();
}

function getCurrentChannel() {
  if (state.viewMode === "history") {
    return {
      id: "history",
      name: "歷史紀錄",
      icon: "◌",
      count: getEventSource().length,
    };
  }

  return channels.find((channel) => channel.id === state.channel) || channels[0];
}

function getCurrentEvent() {
  const source = getEventSource();
  const displayed = getDisplayedEvents();

  return (
    source.find((event) => event.id === state.event) ||
    displayed[0] ||
    source[0] ||
    {
      id: "empty",
      channel: "api",
      type: "empty",
      status: "",
      durationMs: null,
      runId: "",
      time: "--:--:--",
      source: "runtime",
      level: "資訊",
      rawLevel: "info",
      title: "沒有事件",
      preview: "目前沒有 runtime event。",
      full: "目前沒有 runtime event。",
      payload: {},
    }
  );
}

function getCurrentEventIndex() {
  const list = getDisplayedEvents();
  return list.findIndex((event) => event.id === state.event);
}

function getPreviousEvent() {
  const list = getDisplayedEvents();
  const index = getCurrentEventIndex();

  if (index <= 0) return null;

  return list[index - 1];
}

function getNextEvent() {
  const list = getDisplayedEvents();
  const index = getCurrentEventIndex();

  if (index < 0 || index >= list.length - 1) return null;

  return list[index + 1];
}

function getInnerPayload(event) {
  return event?.payload?.payload || {};
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && String(value) !== "") {
      return value;
    }
  }

  return "";
}

function getTextBlockValue(event) {
  const payload = getInnerPayload(event);

  return firstNonEmpty(
    payload.content_full,
    payload.content_preview,
    payload.raw_output_full,
    payload.raw_output_preview,
    payload.arguments_full,
    payload.arguments_preview,
    payload.text?.full,
    payload.text?.preview,
    payload.result?.text?.full,
    payload.result?.text?.preview
  );
}

function getRequestMessages(event) {
  const payload = getInnerPayload(event);
  return payload.messages || [];
}

function getToolCalls(event) {
  const payload = getInnerPayload(event);

  return (
    payload.tool_calls ||
    payload.mcp_tool_calls ||
    payload.external_tool_calls ||
    []
  );
}

function getToolResult(event) {
  const payload = getInnerPayload(event);

  return (
    payload.result ||
    payload.text ||
    payload.tool_result ||
    null
  );
}

function toArray(value) {
  return Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined && String(item) !== "") : [];
}

function isFileTraceEvent(event) {
  return event?.type === "opencode_changed_files_detected" || event?.type === "opencode_diff_generated";
}

function isValidationSummaryEvent(event) {
  return event?.type === "opencode_validation_summary";
}

function isRunSummaryEvent(event) {
  return event?.type === "opencode_run_summary_generated";
}

function isAdosWorkflowStartedEvent(event) {
  return event?.type === "opencode_ados_workflow_started";
}

function isAdosStageSkippedEvent(event) {
  return event?.type === "opencode_ados_stage_skipped";
}

function isAdosStageStartedEvent(event) {
  return event?.type === "opencode_ados_stage_started";
}

function isAdosStageFinishedEvent(event) {
  return event?.type === "opencode_ados_stage_finished";
}

function isAdosWorkflowCompletedEvent(event) {
  return event?.type === "opencode_ados_workflow_completed";
}

function isAdosWorkflowEvent(event) {
  return (
    isAdosWorkflowStartedEvent(event) ||
    isAdosStageSkippedEvent(event) ||
    isAdosStageStartedEvent(event) ||
    isAdosStageFinishedEvent(event) ||
    isAdosWorkflowCompletedEvent(event)
  );
}

function isSummaryTraceEvent(event) {
  return isFileTraceEvent(event) || isValidationSummaryEvent(event) || isRunSummaryEvent(event);
}

function getFileTraceData(event) {
  const payload = getInnerPayload(event);
  const changedFiles = toArray(payload.changed_files);
  const untrackedFiles = toArray(payload.untracked_files);
  const changedFilesCount = Number.isFinite(Number(payload.changed_files_count))
    ? Number(payload.changed_files_count)
    : changedFiles.length;
  const untrackedFilesCount = Number.isFinite(Number(payload.untracked_files_count))
    ? Number(payload.untracked_files_count)
    : untrackedFiles.length;
  const additions = Number.isFinite(Number(payload.additions)) ? Number(payload.additions) : 0;
  const deletions = Number.isFinite(Number(payload.deletions)) ? Number(payload.deletions) : 0;
  const diffPreview = typeof payload.diff_preview === "string" ? payload.diff_preview : "";
  const diffPreviewLength = Number.isFinite(Number(payload.diff_preview_length))
    ? Number(payload.diff_preview_length)
    : diffPreview.length;

  return {
    changedFiles,
    changedFilesCount,
    untrackedFiles,
    untrackedFilesCount,
    additions,
    deletions,
    diffPreview,
    diffPreviewLength,
  };
}

function getValidationSummaryData(event) {
  const payload = getInnerPayload(event);
  const validationSignals = toArray(payload.validation_signals);

  return {
    validationResult: typeof payload.validation_result === "string" ? payload.validation_result : "unknown",
    commandsRun: Number.isFinite(Number(payload.commands_run)) ? Number(payload.commands_run) : 0,
    testCommandsRun: Number.isFinite(Number(payload.test_commands_run)) ? Number(payload.test_commands_run) : 0,
    passedCommands: Number.isFinite(Number(payload.passed_commands)) ? Number(payload.passed_commands) : 0,
    failedCommands: Number.isFinite(Number(payload.failed_commands)) ? Number(payload.failed_commands) : 0,
    validationSignals,
  };
}

function getRunSummaryData(event) {
  const payload = getInnerPayload(event);
  const changedFiles = toArray(payload.changed_files);
  const untrackedFiles = toArray(payload.untracked_files);
  const completedStages = toArray(payload.completed_stages);
  const failedStages = toArray(payload.failed_stages);

  return {
    workflowMode: typeof payload.workflow_mode === "string" ? payload.workflow_mode : "",
    activeStage: typeof payload.active_stage === "string" ? payload.active_stage : "",
    finalStatus: typeof payload.final_status === "string" ? payload.final_status : "unknown",
    filesChangedCount: Number.isFinite(Number(payload.files_changed_count))
      ? Number(payload.files_changed_count)
      : changedFiles.length,
    untrackedFilesCount: Number.isFinite(Number(payload.untracked_files_count))
      ? Number(payload.untracked_files_count)
      : untrackedFiles.length,
    commandsRun: Number.isFinite(Number(payload.commands_run)) ? Number(payload.commands_run) : 0,
    testCommandsRun: Number.isFinite(Number(payload.test_commands_run)) ? Number(payload.test_commands_run) : 0,
    validationResult: typeof payload.validation_result === "string" ? payload.validation_result : "unknown",
    toolCallsCount: Number.isFinite(Number(payload.tool_calls_count)) ? Number(payload.tool_calls_count) : null,
    durationMs: Number.isFinite(Number(payload.duration_ms)) ? Number(payload.duration_ms) : null,
    changedFiles,
    untrackedFiles,
    completedStages,
    failedStages,
    runnerError: payload.runner_error ? String(payload.runner_error) : "",
  };
}

function getAdosWorkflowData(event) {
  const payload = getInnerPayload(event);
  const stages = toArray(payload.stages);
  const completedStages = toArray(payload.completed_stages);
  const skippedStages = toArray(payload.skipped_stages);
  const failedStages = toArray(payload.failed_stages);

  return {
    workflowId: typeof payload.workflow_id === "string" ? payload.workflow_id : "",
    workflowMode: typeof payload.workflow_mode === "string" ? payload.workflow_mode : "",
    selectedAgent: typeof payload.selected_agent === "string" ? payload.selected_agent : "",
    activeStage: typeof payload.active_stage === "string" ? payload.active_stage : "",
    stage: typeof payload.stage === "string" ? payload.stage : "",
    reason: typeof payload.reason === "string" ? payload.reason : "",
    status: typeof payload.status === "string" ? payload.status : "",
    durationMs: Number.isFinite(Number(payload.duration_ms)) ? Number(payload.duration_ms) : null,
    stages,
    completedStages,
    skippedStages,
    failedStages,
  };
}

function getEventSummaryText(event) {
  if (isAdosWorkflowStartedEvent(event)) {
    const details = getAdosWorkflowData(event);
    return `workflow=${details.workflowMode || "single_agent_unknown"} agent=${details.selectedAgent || "unknown"} stage=${details.activeStage || "unknown"}`;
  }

  if (isAdosStageStartedEvent(event)) {
    const details = getAdosWorkflowData(event);
    return `stage=${details.stage || details.activeStage || "unknown"} agent=${details.selectedAgent || "unknown"}`;
  }

  if (isAdosStageFinishedEvent(event)) {
    const details = getAdosWorkflowData(event);
    return `stage=${details.stage || details.activeStage || "unknown"} status=${details.status || "unknown"}`;
  }

  if (isAdosStageSkippedEvent(event)) {
    const details = getAdosWorkflowData(event);
    return `stage=${details.stage || "unknown"} reason=${details.reason || "unknown"}`;
  }

  if (isAdosWorkflowCompletedEvent(event)) {
    const details = getAdosWorkflowData(event);
    const completedText = details.completedStages[0] || details.activeStage || "unknown";
    return `workflow=${details.workflowMode || "single_agent_unknown"} status=${details.status || "unknown"} completed=${completedText} skipped=${details.skippedStages.length}`;
  }

  if (isValidationSummaryEvent(event)) {
    const details = getValidationSummaryData(event);
    return `validation=${details.validationResult} commands=${details.commandsRun} tests=${details.testCommandsRun}`;
  }

  if (isRunSummaryEvent(event)) {
    const details = getRunSummaryData(event);
    return `files_changed=${details.filesChangedCount} commands=${details.commandsRun} validation=${details.validationResult} status=${details.finalStatus}`;
  }

  if (!isFileTraceEvent(event)) {
    return event.preview;
  }

  const details = getFileTraceData(event);

  if (event.type === "opencode_diff_generated") {
    return `changed_files=${details.changedFilesCount} untracked=${details.untrackedFilesCount} diff_preview_length=${details.diffPreviewLength}`;
  }

  return `changed_files=${details.changedFilesCount} untracked=${details.untrackedFilesCount} additions=${details.additions} deletions=${details.deletions}`;
}

function renderFileListBlock(title, items) {
  const rows = toArray(items);

  if (!rows.length) {
    return "";
  }

  return `
    <section class="trace-block">
      <h4>${escapeHtml(title)}</h4>
      <ul class="trace-file-list">
        ${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderWorkflowStagePills(items, type) {
  const rows = toArray(items);

  if (!rows.length) {
    return "";
  }

  return `
    <div class="workflow-stage-list">
      ${rows
        .map((item) => `<span class="workflow-stage-pill ${escapeHtml(type)}">${escapeHtml(item)}</span>`)
        .join("")}
    </div>
  `;
}

function renderSummaryMetricRows(rows) {
  const safeRows = rows.filter((row) => row && row.value !== null && row.value !== undefined && String(row.value) !== "");

  if (!safeRows.length) {
    return "";
  }

  return `
    <div class="trace-metrics">
      ${safeRows
        .map((row) => {
          return `
            <div class="trace-metric-row">
              <span>${escapeHtml(row.label)}</span>
              <strong>${escapeHtml(row.value)}</strong>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderValidationSummaryDetails(event) {
  if (!isValidationSummaryEvent(event)) {
    return "";
  }

  const details = getValidationSummaryData(event);

  return `
    <section class="trace-panel">
      <section class="trace-block">
        <h4>Validation Summary</h4>
        ${renderSummaryMetricRows([
          { label: "validation", value: details.validationResult },
          { label: "commands", value: details.commandsRun },
          { label: "tests", value: details.testCommandsRun },
          { label: "passed", value: details.passedCommands },
          { label: "failed", value: details.failedCommands },
        ])}
      </section>
      ${renderFileListBlock("Validation signals", details.validationSignals)}
    </section>
  `;
}

function renderRunSummaryDetails(event) {
  if (!isRunSummaryEvent(event)) {
    return "";
  }

  const details = getRunSummaryData(event);
  const durationText = details.durationMs !== null ? formatDuration(details.durationMs) : "";

  return `
    <section class="trace-panel">
      <section class="trace-block">
        <h4>Run Summary</h4>
        ${renderSummaryMetricRows([
          { label: "status", value: details.finalStatus },
          { label: "files changed", value: details.filesChangedCount },
          { label: "untracked", value: details.untrackedFilesCount },
          { label: "commands", value: details.commandsRun },
          { label: "tests", value: details.testCommandsRun },
          { label: "validation", value: details.validationResult },
          { label: "tool calls", value: details.toolCallsCount },
          { label: "duration", value: durationText },
        ])}
      </section>
      ${renderFileListBlock("Changed files", details.changedFiles)}
      ${renderFileListBlock("Untracked files", details.untrackedFiles)}
      ${details.runnerError ? renderTextBlock("Runner error", details.runnerError) : ""}
    </section>
  `;
}

function renderAdosWorkflowDetails(event) {
  if (!isAdosWorkflowEvent(event)) {
    return "";
  }

  const details = getAdosWorkflowData(event);
  const durationText = details.durationMs !== null ? formatDuration(details.durationMs) : "";

  if (isAdosWorkflowStartedEvent(event)) {
    return `
      <section class="trace-panel">
        <section class="trace-block">
          <h4>ADOS Workflow</h4>
          ${renderSummaryMetricRows([
            { label: "workflow", value: details.workflowMode },
            { label: "agent", value: details.selectedAgent },
            { label: "active stage", value: details.activeStage },
            { label: "workflow id", value: details.workflowId },
          ])}
        </section>
        ${renderFileListBlock("Stages", details.stages)}
      </section>
    `;
  }

  if (isAdosStageSkippedEvent(event)) {
    return `
      <section class="trace-panel">
        <section class="trace-block">
          <h4>ADOS Stage</h4>
          ${renderSummaryMetricRows([
            { label: "stage", value: details.stage },
            { label: "status", value: "skipped" },
            { label: "reason", value: details.reason },
            { label: "agent", value: details.selectedAgent },
            { label: "workflow", value: details.workflowMode },
          ])}
        </section>
      </section>
    `;
  }

  if (isAdosStageStartedEvent(event)) {
    return `
      <section class="trace-panel">
        <section class="trace-block">
          <h4>ADOS Stage</h4>
          ${renderSummaryMetricRows([
            { label: "stage", value: details.stage || details.activeStage },
            { label: "status", value: "started" },
            { label: "agent", value: details.selectedAgent },
            { label: "workflow", value: details.workflowMode },
          ])}
        </section>
      </section>
    `;
  }

  if (isAdosStageFinishedEvent(event)) {
    return `
      <section class="trace-panel">
        <section class="trace-block">
          <h4>ADOS Stage</h4>
          ${renderSummaryMetricRows([
            { label: "stage", value: details.stage || details.activeStage },
            { label: "status", value: details.status },
            { label: "agent", value: details.selectedAgent },
            { label: "workflow", value: details.workflowMode },
          ])}
        </section>
      </section>
    `;
  }

  return `
    <section class="trace-panel">
      <section class="trace-block">
        <h4>ADOS Workflow Completed</h4>
        ${renderSummaryMetricRows([
          { label: "workflow", value: details.workflowMode },
          { label: "status", value: details.status },
          { label: "agent", value: details.selectedAgent },
          { label: "active stage", value: details.activeStage },
          { label: "duration", value: durationText },
        ])}
      </section>
      ${
        details.completedStages.length
          ? `
            <section class="trace-block">
              <h4>Completed stages</h4>
              ${renderWorkflowStagePills(details.completedStages, "completed")}
            </section>
          `
          : ""
      }
      ${
        details.skippedStages.length
          ? `
            <section class="trace-block">
              <h4>Skipped stages</h4>
              ${renderWorkflowStagePills(details.skippedStages, "skipped")}
            </section>
          `
          : ""
      }
      ${
        details.failedStages.length
          ? `
            <section class="trace-block">
              <h4>Failed stages</h4>
              ${renderWorkflowStagePills(details.failedStages, "failed")}
            </section>
          `
          : ""
      }
    </section>
  `;
}

function renderEventDetails(event) {
  if (isAdosWorkflowEvent(event)) {
    return renderAdosWorkflowDetails(event);
  }

  if (isFileTraceEvent(event)) {
    return renderFileTraceDetails(event);
  }

  if (isValidationSummaryEvent(event)) {
    return renderValidationSummaryDetails(event);
  }

  if (isRunSummaryEvent(event)) {
    return renderRunSummaryDetails(event);
  }

  return "";
}

function renderFileTraceDetails(event) {
  if (!isFileTraceEvent(event)) {
    return "";
  }

  const details = getFileTraceData(event);

  return `
    <section class="trace-panel">
      <div class="trace-summary">
        <span>changed_files=${escapeHtml(details.changedFilesCount)}</span>
        <span>untracked=${escapeHtml(details.untrackedFilesCount)}</span>
        <span>additions=${escapeHtml(details.additions)}</span>
        <span>deletions=${escapeHtml(details.deletions)}</span>
        ${
          event.type === "opencode_diff_generated"
            ? `<span>diff_preview_length=${escapeHtml(details.diffPreviewLength)}</span>`
            : ""
        }
      </div>
      ${renderFileListBlock("Changed files", details.changedFiles)}
      ${renderFileListBlock("Untracked files", details.untrackedFiles)}
      ${
        details.diffPreview
          ? `
            <section class="trace-block">
              <h4>Diff preview</h4>
              <pre class="trace-preview">${escapeHtml(details.diffPreview)}</pre>
            </section>
          `
          : ""
      }
    </section>
  `;
}

function renderCopyButton(label, value, extraClass = "") {
  const encoded = encodeURIComponent(String(value ?? ""));

  return `
    <button class="mini-button copy-button ${extraClass}" data-copy="${encoded}">
      ${escapeHtml(label)}
    </button>
  `;
}

function renderTextBlock(title, value, emptyText = "沒有文字內容") {
  const text = String(value ?? "");

  return `
    <section class="text-block">
      <div class="text-block-head">
        <h3>${escapeHtml(title)}</h3>
        ${text ? renderCopyButton("複製", text) : ""}
      </div>
      ${
        text
          ? `<pre class="text-block-content">${escapeHtml(text)}</pre>`
          : `<p class="muted">${escapeHtml(emptyText)}</p>`
      }
    </section>
  `;
}

function renderKeyValueRows(rows) {
  return `
    <div class="kv">
      ${rows
        .map((row) => {
          return `
            <div>
              <span>${escapeHtml(row.label)}</span>
              <strong>${escapeHtml(row.value)}</strong>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function getFlowDigestScopeLabel(kind) {
  if (kind === "visible") return "visible";
  if (kind === "run") return "run";
  return "compact";
}

function getFlowDigestRunLabel(events) {
  if (state.selectedRunId && state.selectedRunId !== "latest") {
    return state.selectedRunId;
  }

  const runId = events.find((event) => event.runId)?.runId;
  return runId || "latest";
}

function getFlowDigestViewLabel() {
  return state.viewMode === "history" ? "history" : "channel";
}

function getFlowDigestChannelLabel(kind) {
  if (state.viewMode === "history") {
    return kind === "visible" ? "history-filtered" : "all";
  }

  const currentChannel = channels.find((channel) => channel.id === state.channel);
  return currentChannel?.id || state.channel || "all";
}

function getFlowDigestGeneratedAt() {
  return new Date().toLocaleString("zh-TW", { hour12: false });
}

function getDigestEvents(kind) {
  if (kind === "visible") {
    return getDisplayedEvents();
  }

  const source = getChronologicalEvents();

  if (kind === "compact") {
    return source.filter((event) => isCompactFlowEvent(event));
  }

  return source;
}

function isCompactFlowEvent(event) {
  const type = String(event?.type || "").toLowerCase();
  const rawLevel = String(event?.rawLevel || "").toLowerCase();

  return (
    COMPACT_FLOW_EVENT_TYPES.has(type) ||
    rawLevel === "error" ||
    type.includes("error") ||
    type.includes("workflow") ||
    type.includes("stage") ||
    type.includes("summary") ||
    type.includes("diff") ||
    type.includes("changed_files")
  );
}

function formatDigestEvent(event, index) {
  const duration = formatDuration(event.durationMs) || "-";
  const status = event.status || "-";
  const runId = event.runId || "-";
  const summary = getEventSummaryText(event) || "-";

  return [
    `${index + 1}. [${event.time || "--:--:--"}] ${event.type || "unknown_event"}`,
    `   title: ${event.title || "-"}`,
    `   summary: ${summary}`,
    `   source: ${event.source || "-"}`,
    `   status: ${status}`,
    `   duration: ${duration}`,
    `   run_id: ${runId}`,
  ].join("\n");
}

function buildQuickFlowSummary(events) {
  const source = [...events];
  const workflowStarted = [...source].reverse().find((event) => event.type === "opencode_ados_workflow_started");
  const workflowCompleted = [...source].reverse().find((event) => event.type === "opencode_ados_workflow_completed");
  const changedFiles = [...source].reverse().find((event) => event.type === "opencode_changed_files_detected");
  const validation = [...source].reverse().find((event) => event.type === "opencode_validation_summary");
  const runSummary = [...source].reverse().find((event) => event.type === "opencode_run_summary_generated");

  const workflowStartedData = workflowStarted ? getAdosWorkflowData(workflowStarted) : null;
  const workflowCompletedData = workflowCompleted ? getAdosWorkflowData(workflowCompleted) : null;
  const changedFilesData = changedFiles ? getFileTraceData(changedFiles) : null;
  const validationData = validation ? getValidationSummaryData(validation) : null;
  const runSummaryData = runSummary ? getRunSummaryData(runSummary) : null;

  const rows = [
    { label: "workflow", value: workflowStartedData?.workflowMode || workflowCompletedData?.workflowMode || "" },
    { label: "agent", value: workflowStartedData?.selectedAgent || workflowCompletedData?.selectedAgent || "" },
    { label: "active_stage", value: workflowCompletedData?.activeStage || workflowStartedData?.activeStage || "" },
    {
      label: "completed_stages",
      value: workflowCompletedData?.completedStages?.length ? workflowCompletedData.completedStages.join(", ") : "",
    },
    {
      label: "skipped_stages",
      value: workflowCompletedData?.skippedStages?.length ? workflowCompletedData.skippedStages.join(", ") : "",
    },
    {
      label: "changed_files",
      value: changedFilesData
        ? `${changedFilesData.changedFilesCount}${changedFilesData.untrackedFilesCount ? ` (untracked=${changedFilesData.untrackedFilesCount})` : ""}`
        : runSummaryData && Number.isFinite(runSummaryData.filesChangedCount)
          ? `${runSummaryData.filesChangedCount}${runSummaryData.untrackedFilesCount ? ` (untracked=${runSummaryData.untrackedFilesCount})` : ""}`
          : "",
    },
    { label: "validation_result", value: validationData?.validationResult || runSummaryData?.validationResult || "" },
    { label: "final_status", value: runSummaryData?.finalStatus || workflowCompletedData?.status || "" },
  ].filter((row) => row.value);

  if (!rows.length) {
    return "";
  }

  return [
    "## Quick Summary",
    "",
    ...rows.map((row) => `- ${row.label}: ${row.value}`),
    "",
  ].join("\n");
}

function buildFlowDigest(events, options = {}) {
  const kind = options.kind || "visible";
  const lines = [
    "# Runtime Flow Digest",
    "",
    `Scope: ${getFlowDigestScopeLabel(kind)}`,
    `Run: ${getFlowDigestRunLabel(events)}`,
    `View: ${getFlowDigestViewLabel()}`,
    `Channel: ${getFlowDigestChannelLabel(kind)}`,
    `Sort: ${state.sortDirection}`,
    `Events: ${events.length}`,
    `Generated: ${getFlowDigestGeneratedAt()}`,
    "",
  ];
  const quickSummary = buildQuickFlowSummary(events);

  if (quickSummary) {
    lines.push(quickSummary);
  }

  lines.push("## Timeline Cards", "");

  if (!events.length) {
    lines.push("No events available.");
    return lines.join("\n");
  }

  lines.push(...events.map((event, index) => formatDigestEvent(event, index)));
  return lines.join("\n\n");
}

async function writeTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();

  const succeeded = document.execCommand("copy");
  textarea.remove();

  if (!succeeded) {
    throw new Error("Clipboard copy failed");
  }
}

function setFlowDigestFeedback({ copied = "", error = "" }) {
  state.flowDigest.copied = copied;
  state.flowDigest.error = error;

  if (flowDigestFeedbackTimer) {
    clearTimeout(flowDigestFeedbackTimer);
    flowDigestFeedbackTimer = null;
  }

  if (copied || error) {
    flowDigestFeedbackTimer = setTimeout(() => {
      state.flowDigest.copied = "";
      state.flowDigest.error = "";
      renderPreservingScroll();
    }, 1800);
  }
}

async function copyFlowDigest(kind) {
  const events = getDigestEvents(kind);
  const text = buildFlowDigest(events, { kind });

  try {
    await writeTextToClipboard(text);
    setFlowDigestFeedback({
      copied:
        kind === "visible"
          ? "Copied visible timeline cards."
          : kind === "run"
            ? "Copied current run events."
            : "Copied compact flow digest.",
      error: "",
    });
  } catch (error) {
    setFlowDigestFeedback({
      copied: "",
      error: error instanceof Error ? error.message : "Copy failed",
    });
  }

  renderPreservingScroll();
}

function renderFlowDigestPanel() {
  const statusText = state.flowDigest.error || state.flowDigest.copied;
  const statusClass = state.flowDigest.error ? "error" : state.flowDigest.copied ? "success" : "";

  return `
    <section class="flow-digest-card">
      <div class="flow-digest-head">
        <div>
          <h2>Flow Digest</h2>
          <p class="muted">Copy timeline card titles and summaries as Markdown so the current runtime flow can be reviewed elsewhere without screenshots.</p>
        </div>
      </div>

      <div class="flow-digest-actions">
        <button class="mini-button copy-button" data-copy-flow-digest="visible">Copy Visible</button>
        <button class="mini-button copy-button" data-copy-flow-digest="run">Copy Run</button>
        <button class="mini-button copy-button" data-copy-flow-digest="compact">Copy Compact Flow</button>
      </div>

      ${
        statusText
          ? `<p class="flow-digest-status ${escapeHtml(statusClass)}">${escapeHtml(statusText)}</p>`
          : `<p class="flow-digest-status">Visible uses current filters. Run uses the selected source. Compact keeps key workflow-check events only.</p>`
      }
    </section>
  `;
}

function getPbvStageDisplayName(stage) {
  if (stage === "planner") return "Plan";
  if (stage === "builder") return "Build";
  if (stage === "verifier") return "Verify";
  return stage || "Unknown";
}

function extractStageOutputLength(payload, stage) {
  if (!payload || !stage) {
    return null;
  }

  const directValue = payload[`${stage}_output_length`];
  const numericValue = Number(directValue);
  return Number.isFinite(numericValue) ? numericValue : null;
}

function getToolNameFromEvent(event) {
  const payload = getInnerPayload(event);
  return firstNonEmpty(
    payload.tool_name,
    payload.tool_call?.name,
    payload.tool_call?.tool_name,
    payload.tool?.name
  );
}

function getContentLengthFromEvent(event) {
  const payload = getInnerPayload(event);
  const value = Number(payload.content_length);
  return Number.isFinite(value) ? value : null;
}

function getPbvEvents() {
  return getChronologicalEvents();
}

function getChangedFilesSummary(events) {
  const changedEvent = [...events].reverse().find((event) => event.type === "opencode_changed_files_detected");
  const diffEvent = [...events].reverse().find((event) => event.type === "opencode_diff_generated");
  const runSummaryEvent = [...events].reverse().find((event) => event.type === "opencode_run_summary_generated");
  const changedData = changedEvent ? getFileTraceData(changedEvent) : null;
  const diffData = diffEvent ? getFileTraceData(diffEvent) : null;
  const runSummaryData = runSummaryEvent ? getRunSummaryData(runSummaryEvent) : null;

  return {
    changedFilesCount: changedData?.changedFilesCount ?? runSummaryData?.filesChangedCount ?? 0,
    untrackedFilesCount: changedData?.untrackedFilesCount ?? runSummaryData?.untrackedFilesCount ?? 0,
    changedFiles: changedData?.changedFiles ?? runSummaryData?.changedFiles ?? [],
    untrackedFiles: changedData?.untrackedFiles ?? runSummaryData?.untrackedFiles ?? [],
    diffPreviewLength: diffData?.diffPreviewLength ?? 0,
  };
}

function getValidationSummary(events) {
  const validationEvent = [...events].reverse().find((event) => event.type === "opencode_validation_summary");
  const runSummaryEvent = [...events].reverse().find((event) => event.type === "opencode_run_summary_generated");
  const validationData = validationEvent ? getValidationSummaryData(validationEvent) : null;
  const runSummaryData = runSummaryEvent ? getRunSummaryData(runSummaryEvent) : null;

  return {
    validationResult: validationData?.validationResult || runSummaryData?.validationResult || "unknown",
    commandsRun: validationData?.commandsRun ?? runSummaryData?.commandsRun ?? 0,
    testCommandsRun: validationData?.testCommandsRun ?? runSummaryData?.testCommandsRun ?? 0,
  };
}

function getFinalResponseStatus(events) {
  if ([...events].reverse().find((event) => event.type === "model_response_sent")) {
    return "sent";
  }

  if ([...events].reverse().find((event) => event.type === "model_response_error")) {
    return "error";
  }

  return "unknown";
}

function inferPbvStages(events) {
  const orderedStages = ["planner", "builder", "verifier"];
  const stageMap = new Map(
    orderedStages.map((stage) => [
      stage,
      {
        key: stage,
        name: getPbvStageDisplayName(stage),
        agent: "",
        status: "unknown",
        templateSelected: "",
        toolCount: 0,
        toolCounts: {},
        mainTools: [],
        outputLength: null,
        durationMs: null,
        events: [],
      },
    ])
  );

  let activeStage = null;

  for (const event of [...events].sort((a, b) => String(a.payload?.ts || "").localeCompare(String(b.payload?.ts || "")))) {
    if (event.type === "opencode_ados_stage_started") {
      const details = getAdosWorkflowData(event);
      const stageKey = details.stage || details.activeStage;

      if (!stageMap.has(stageKey)) {
        stageMap.set(stageKey, {
          key: stageKey,
          name: getPbvStageDisplayName(stageKey),
          agent: "",
          status: "unknown",
          templateSelected: "",
          toolCount: 0,
          toolCounts: {},
          mainTools: [],
          outputLength: null,
          durationMs: null,
          events: [],
        });
      }

      activeStage = stageKey;
      const bucket = stageMap.get(stageKey);
      bucket.agent = details.selectedAgent || details.agent || bucket.agent || "";
      bucket.events.push(event);
      continue;
    }

    if (event.type === "opencode_ados_workflow_completed") {
      activeStage = null;
    }

    if (!activeStage || !stageMap.has(activeStage)) {
      continue;
    }

    const bucket = stageMap.get(activeStage);
    bucket.events.push(event);

    if (event.type === "opencode_ados_template_loaded") {
      const payload = getInnerPayload(event);
      bucket.templateSelected = typeof payload.selected === "string" ? payload.selected : bucket.templateSelected;
      continue;
    }

    if (event.type === "mcp_tool_started") {
      const toolName = getToolNameFromEvent(event);
      if (toolName) {
        bucket.toolCount += 1;
        bucket.toolCounts[toolName] = (bucket.toolCounts[toolName] || 0) + 1;
      }
      continue;
    }

    if (event.type === "mcp_model_output_parsed") {
      const contentLength = getContentLengthFromEvent(event);
      if (contentLength !== null) {
        bucket.outputLength = contentLength;
      }
      continue;
    }

    if (event.type === "opencode_ados_stage_finished") {
      const details = getAdosWorkflowData(event);
      const payload = getInnerPayload(event);
      bucket.status = details.status || bucket.status;
      bucket.agent = details.selectedAgent || details.agent || bucket.agent || "";
      bucket.durationMs = details.durationMs ?? bucket.durationMs;
      const stageOutputLength = extractStageOutputLength(payload, activeStage);
      if (stageOutputLength !== null) {
        bucket.outputLength = stageOutputLength;
      }
    }
  }

  return orderedStages.map((stage) => {
    const bucket = stageMap.get(stage);
    const mainTools = Object.entries(bucket.toolCounts)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 3)
      .map(([name, count]) => `${name} x${count}`);

    return {
      ...bucket,
      mainTools,
    };
  });
}

function getLatestPbvWorkflowEvents(events) {
  const workflowStarted = [...events].reverse().find((event) => {
    if (!isAdosWorkflowStartedEvent(event) && !isRunSummaryEvent(event) && !isAdosWorkflowCompletedEvent(event)) {
      return false;
    }

    const workflowData = isRunSummaryEvent(event) ? getRunSummaryData(event) : getAdosWorkflowData(event);
    return workflowData.workflowMode === "plan_build_verify";
  });
  const workflowCompleted = [...events].reverse().find((event) => {
    return event.type === "opencode_ados_workflow_completed" && getAdosWorkflowData(event).workflowMode === "plan_build_verify";
  });
  const runSummary = [...events].reverse().find((event) => {
    return event.type === "opencode_run_summary_generated" && getRunSummaryData(event).workflowMode === "plan_build_verify";
  });
  return { workflowStarted, workflowCompleted, runSummary };
}

function getWorkflowSummaryMismatch(stages, events) {
  const completedFromStages = stages
    .filter((stage) => stage.status === "completed")
    .map((stage) => stage.key);
  const workflowCompletedEvent = [...events].reverse().find((event) => event.type === "opencode_ados_workflow_completed");

  if (!workflowCompletedEvent || completedFromStages.length < 2) {
    return "";
  }

  const summaryText = getEventSummaryText(workflowCompletedEvent);
  const missingStages = completedFromStages.filter((stage) => !summaryText.includes(stage));

  if (summaryText.includes("completed=planner") && missingStages.length) {
    return "workflow_completed summary may be inconsistent with stage events";
  }

  return "";
}

function getPbvHandoffs(events, pbvSummary) {
  const stages = pbvSummary?.stages || [];
  const stageMap = new Map(stages.map((stage) => [stage.key, stage]));
  const changedFiles = getChangedFilesSummary(events);
  const validation = getValidationSummary(events);

  const planner = stageMap.get("planner");
  const builder = stageMap.get("builder");
  const verifier = stageMap.get("verifier");

  const plannerToBuilderStatus = !planner
    ? "unknown"
    : planner.status === "completed" && builder?.events?.length
      ? "ready"
      : planner.status === "partial" && builder?.events?.length
        ? "partial"
        : (planner.status === "completed" || planner.status === "partial") && !builder?.events?.length
          ? "missing"
          : "unknown";

  const builderToVerifierStatus = !builder
    ? "unknown"
    : builder.status === "completed" && verifier?.events?.length
      ? "ready"
      : builder.status === "partial" && verifier?.events?.length
        ? "partial"
        : (builder.status === "completed" || builder.status === "partial") && !verifier?.events?.length
          ? "missing"
          : "unknown";

  return [
    {
      id: "planner-builder",
      title: "Planner -> Builder",
      status: plannerToBuilderStatus,
      rows: [
        { label: "planner status", value: planner?.status || "unknown" },
        { label: "planner output", value: planner?.outputLength !== null ? `${planner.outputLength} chars` : "-" },
        { label: "builder received stage", value: builder?.events?.length ? "yes" : "no" },
        { label: "builder agent", value: builder?.agent || "-" },
      ],
    },
    {
      id: "builder-verifier",
      title: "Builder -> Verifier",
      status: builderToVerifierStatus,
      rows: [
        { label: "builder status", value: builder?.status || "unknown" },
        { label: "builder tools", value: builder?.mainTools?.join(", ") || "0" },
        { label: "changed files", value: changedFiles.changedFilesCount },
        { label: "diff preview", value: changedFiles.diffPreviewLength ? `${changedFiles.diffPreviewLength} chars` : "-" },
        { label: "validation", value: validation.validationResult || "unknown" },
        { label: "verifier received stage", value: verifier?.events?.length ? "yes" : "no" },
        { label: "verifier agent", value: verifier?.agent || "-" },
      ],
    },
  ];
}

function getPbvSummary(events) {
  const workflowEvents = getLatestPbvWorkflowEvents(events);
  const runSummaryData = workflowEvents.runSummary ? getRunSummaryData(workflowEvents.runSummary) : null;
  const workflowStartedData = workflowEvents.workflowStarted && !isRunSummaryEvent(workflowEvents.workflowStarted)
    ? getAdosWorkflowData(workflowEvents.workflowStarted)
    : null;
  const workflowCompletedData = workflowEvents.workflowCompleted ? getAdosWorkflowData(workflowEvents.workflowCompleted) : null;
  const workflowMode = workflowStartedData?.workflowMode || workflowCompletedData?.workflowMode || workflowEvents.runSummary?.payload?.payload?.workflow_mode || "";

  if (workflowMode !== "plan_build_verify") {
    return null;
  }

  const stages = inferPbvStages(events);
  const changedFiles = getChangedFilesSummary(events);
  const validation = getValidationSummary(events);
  const finalResponseStatus = getFinalResponseStatus(events);
  const warnings = [];
  const workflowMismatch = getWorkflowSummaryMismatch(stages, events);

  if (workflowMismatch) {
    warnings.push(workflowMismatch);
  }

  return {
    workflow: workflowMode,
    status: workflowCompletedData?.status || runSummaryData?.finalStatus || "unknown",
    stages,
    agents: stages.map((stage) => stage.agent).filter(Boolean),
    changedFilesCount: changedFiles.changedFilesCount,
    untrackedFilesCount: changedFiles.untrackedFilesCount,
    changedFiles: changedFiles.changedFiles,
    validationResult: validation.validationResult,
    commandsRun: validation.commandsRun,
    testCommandsRun: validation.testCommandsRun,
    finalResponseStatus,
    durationMs: runSummaryData?.durationMs ?? workflowCompletedData?.durationMs ?? null,
    handoffs: [],
    warnings,
  };
}

function buildPbvSummaryMarkdown(summary) {
  const warnings = summary.warnings?.length
    ? summary.warnings.map((warning) => `- ${warning}`).join("\n")
    : "- none";

  return [
    "# PBV Summary",
    "",
    `Workflow: ${summary.workflow || "unknown"}`,
    `Status: ${summary.status || "unknown"}`,
    `Changed files: ${summary.changedFilesCount ?? 0}`,
    `Validation: ${summary.validationResult || "unknown"}`,
    `Final response: ${summary.finalResponseStatus || "unknown"}`,
    "",
    "## Stages",
    "",
    "| Stage | Agent | Status | Tools | Output |",
    "|---|---|---|---|---|",
    ...summary.stages.map((stage) => {
      return `| ${stage.name} | ${stage.agent || "-"} | ${stage.status || "unknown"} | ${stage.mainTools.join(", ") || "0"} | ${stage.outputLength !== null ? `${stage.outputLength} chars` : "-"} |`;
    }),
    "",
    "## Handoffs",
    "",
    ...summary.handoffs.map((handoff) => `- ${handoff.title}: ${handoff.status}`),
    "",
    "## Warnings",
    "",
    warnings,
  ].join("\n");
}

function setPbvSummaryFeedback({ copied = "", error = "" }) {
  state.pbvSummary.copied = copied;
  state.pbvSummary.error = error;

  if (pbvSummaryFeedbackTimer) {
    clearTimeout(pbvSummaryFeedbackTimer);
    pbvSummaryFeedbackTimer = null;
  }

  if (copied || error) {
    pbvSummaryFeedbackTimer = setTimeout(() => {
      state.pbvSummary.copied = "";
      state.pbvSummary.error = "";
      renderPreservingScroll();
    }, 1800);
  }
}

async function copyPbvSummary() {
  const summary = getPbvSummary(getPbvEvents());
  if (!summary) {
    setPbvSummaryFeedback({ copied: "", error: "No PBV summary available." });
    renderPreservingScroll();
    return;
  }

  summary.handoffs = getPbvHandoffs(getPbvEvents(), summary);
  const text = buildPbvSummaryMarkdown(summary);

  try {
    await writeTextToClipboard(text);
    setPbvSummaryFeedback({ copied: "Copied PBV summary.", error: "" });
  } catch (error) {
    setPbvSummaryFeedback({
      copied: "",
      error: error instanceof Error ? error.message : "Copy failed",
    });
  }

  renderPreservingScroll();
}

function renderPbvHandoffPanel(summary) {
  if (!summary?.handoffs?.length) {
    return "";
  }

  return `
    <section class="pbv-handoff-card">
      <h3>PBV Handoff</h3>
      <div class="pbv-handoff-list">
        ${summary.handoffs
          .map((handoff) => {
            return `
              <div class="pbv-handoff-row">
                <div class="pbv-handoff-head">
                  <strong>${escapeHtml(handoff.title)}</strong>
                  <span class="pbv-handoff-status ${escapeHtml(handoff.status)}">${escapeHtml(handoff.status)}</span>
                </div>
                <div class="pbv-summary-grid compact">
                  ${handoff.rows
                    .map((row) => `<div><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></div>`)
                    .join("")}
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderPbvSummaryPanel() {
  const summary = getPbvSummary(getPbvEvents());

  if (!summary) {
    return "";
  }

  summary.handoffs = getPbvHandoffs(getPbvEvents(), summary);
  const statusText = state.pbvSummary.error || state.pbvSummary.copied;
  const statusClass = state.pbvSummary.error ? "error" : state.pbvSummary.copied ? "success" : "";

  return `
    <section class="pbv-summary-card">
      <div class="pbv-summary-header">
        <div>
          <h2>PBV Summary</h2>
          <p class="muted">A compact frontend-only view of the plan -> build -> verify run, inferred from the current timeline order.</p>
        </div>
        <button class="mini-button copy-button" data-copy-pbv-summary>銴ˊ PBV 摘要</button>
      </div>

      <div class="pbv-summary-grid">
        <div><span>Workflow</span><strong>${escapeHtml(summary.workflow || "-")}</strong></div>
        <div><span>Status</span><strong>${escapeHtml(summary.status || "unknown")}</strong></div>
        <div><span>Changed files</span><strong>${escapeHtml(summary.changedFilesCount)}</strong></div>
        <div><span>Untracked files</span><strong>${escapeHtml(summary.untrackedFilesCount)}</strong></div>
        <div><span>Validation</span><strong>${escapeHtml(summary.validationResult || "unknown")}</strong></div>
        <div><span>Commands</span><strong>${escapeHtml(summary.commandsRun)}</strong></div>
        <div><span>Tests</span><strong>${escapeHtml(summary.testCommandsRun)}</strong></div>
        <div><span>Final response</span><strong>${escapeHtml(summary.finalResponseStatus || "unknown")}</strong></div>
        <div><span>Duration</span><strong>${escapeHtml(formatDuration(summary.durationMs) || "-")}</strong></div>
        <div><span>Agents</span><strong>${escapeHtml(summary.agents.join(" / ") || "-")}</strong></div>
      </div>

      <div class="pbv-stage-table-wrap">
        <table class="pbv-stage-table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Agent</th>
              <th>Status</th>
              <th>Template</th>
              <th>Tools</th>
              <th>Main tools</th>
              <th>Output</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            ${summary.stages
              .map((stage) => {
                return `
                  <tr>
                    <td>${escapeHtml(stage.name)}</td>
                    <td>${escapeHtml(stage.agent || "-")}</td>
                    <td>${escapeHtml(stage.status || "unknown")}</td>
                    <td>${escapeHtml(stage.templateSelected || "-")}</td>
                    <td>${escapeHtml(stage.toolCount)}</td>
                    <td>${escapeHtml(stage.mainTools.join(", ") || "0")}</td>
                    <td>${escapeHtml(stage.outputLength !== null ? `${stage.outputLength} chars` : "-")}</td>
                    <td>${escapeHtml(formatDuration(stage.durationMs) || "-")}</td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>

      ${renderPbvHandoffPanel(summary)}

      ${
        summary.warnings.length
          ? `
            <div class="pbv-warning">
              ${summary.warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}
            </div>
          `
          : ""
      }

      ${
        statusText
          ? `<p class="pbv-copy-status ${escapeHtml(statusClass)}">${escapeHtml(statusText)}</p>`
          : `<p class="pbv-copy-status">Copy a compact Markdown PBV summary for planner/build/verifier handoffs and final status.</p>`
      }
    </section>
  `;
}

function renderRequestTextPanel(event) {
  const messages = getRequestMessages(event);

  if (!messages.length) {
    return renderTextBlock("Request Text", getTextBlockValue(event));
  }

  return `
    <section class="text-block">
      <div class="text-block-head">
        <h3>Request Messages</h3>
        ${renderCopyButton("複製全部", JSON.stringify(messages, null, 2))}
      </div>

      ${messages
        .map((message, index) => {
          const content = firstNonEmpty(
            message.content_full,
            message.content_preview,
            message.content_debug_preview
          );

          return `
            <div class="message-block">
              <div class="message-head">
                <strong>#${index + 1} · ${escapeHtml(message.role || "unknown")}</strong>
                <span>${escapeHtml(message.content_type || "text")}</span>
              </div>
              <pre>${escapeHtml(content || "")}</pre>
            </div>
          `;
        })
        .join("")}
    </section>
  `;
}

function renderResponseTextPanel(event) {
  const payload = getInnerPayload(event);

  const content = firstNonEmpty(
    payload.content_full,
    payload.content_preview,
    payload.content_debug_preview
  );

  const rawOutput = firstNonEmpty(
    payload.raw_output_full,
    payload.raw_output_preview,
    payload.raw_output_debug_preview
  );

  return `
    ${renderTextBlock("Response Content", content)}
    ${rawOutput ? renderTextBlock("Raw Model Output", rawOutput) : ""}
  `;
}

function renderToolPanel(event) {
  const payload = getInnerPayload(event);
  const toolCalls = getToolCalls(event);
  const toolResult = getToolResult(event);

  const directArguments = firstNonEmpty(
    payload.arguments_full,
    payload.arguments_preview,
    payload.tool_call?.arguments?.full,
    payload.tool_call?.arguments?.preview,
    payload.tool_call?.arguments_full,
    payload.tool_call?.arguments_preview
  );

  return `
    <section class="text-block">
      <div class="text-block-head">
        <h3>Tool Info</h3>
        ${renderCopyButton("複製 payload", JSON.stringify(payload, null, 2))}
      </div>

      ${renderKeyValueRows([
        { label: "tool_name", value: payload.tool_name || payload.tool_call?.name || "" },
        { label: "tool_calls_count", value: payload.tool_calls_count ?? payload.mcp_tool_calls_count ?? toolCalls.length },
        { label: "duration_ms", value: event.durationMs ?? payload.duration_ms ?? "" },
        { label: "status", value: event.status || payload.status || "" },
      ])}

      ${directArguments ? renderTextBlock("Tool Arguments", directArguments) : ""}

      ${
        toolCalls.length
          ? `
            <div class="tool-call-list">
              <h3>Tool Calls</h3>
              ${toolCalls
                .map((call, index) => {
                  const args = firstNonEmpty(
                    call.arguments_full,
                    call.arguments_preview,
                    call.arguments?.full,
                    call.arguments?.preview
                  );

                  return `
                    <div class="tool-call-card">
                      <div class="message-head">
                        <strong>#${index + 1} · ${escapeHtml(call.name || call.tool_name || "unknown_tool")}</strong>
                        <span>${escapeHtml(call.type || "function")}</span>
                      </div>
                      ${
                        args
                          ? `<pre>${escapeHtml(args)}</pre>`
                          : `<pre>${escapeHtml(JSON.stringify(call, null, 2))}</pre>`
                      }
                    </div>
                  `;
                })
                .join("")}
            </div>
          `
          : ""
      }

      ${toolResult ? renderTextBlock("Tool Result", JSON.stringify(toolResult, null, 2)) : ""}
    </section>
  `;
}

function renderInspectorOverview(event) {
  return `
    ${renderKeyValueRows([
      { label: "來源", value: event.source },
      { label: "時間", value: event.time },
      { label: "等級", value: event.level },
      { label: "Channel", value: channels.find((channel) => channel.id === event.channel)?.name || event.channel },
      { label: "Type", value: event.type || "" },
      { label: "Status", value: event.status || "" },
      { label: "Duration", value: formatDuration(event.durationMs) || "" },
      { label: "Run ID", value: event.runId || "" },
    ])}

    <div class="full-block">${escapeHtml(event.full)}</div>
    ${renderEventDetails(event)}
  `;
}

async function refreshApiStatus() {
  try {
    const [healthRes, modelsRes] = await Promise.all([
      fetch(`${API_PREFIX}/health`),
      fetch(`${API_PREFIX}/v1/models`),
    ]);

    const health = await healthRes.json();
    const modelsJson = await modelsRes.json();

    state.apiStatus = {
      loading: false,
      online: true,
      health,
      models: modelsJson.data || [],
      error: null,
    };

    const models = getAvailableModels();
    if (!models.includes(state.testPanel.model)) {
      state.testPanel.model = models[0] || "chatgpt-web-local";
    }
  } catch (error) {
    state.apiStatus = {
      loading: false,
      online: false,
      health: null,
      models: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }

  renderPreservingScroll();
}

async function refreshRunnerDebug(forceRender = false) {
  try {
    const res = await fetch(`${API_PREFIX}/v1/debug/runner`);
    const json = await res.json();
    const key = stableStringify(json);

    if (!forceRender && key === state.runnerDebug.lastKey) {
      return;
    }

    state.runnerDebug = {
      loading: false,
      data: json,
      error: null,
      lastKey: key,
    };
  } catch (error) {
    state.runnerDebug = {
      loading: false,
      data: null,
      error: error instanceof Error ? error.message : String(error),
      lastKey: "",
    };
  }

  renderPreservingScroll();
}

async function refreshRuns() {
  try {
    const res = await fetch(`${API_PREFIX}/v1/debug/runs?limit=30`);
    const json = await res.json();

    state.runs = {
      loading: false,
      data: json.data || [],
      error: null,
    };
  } catch (error) {
    state.runs = {
      loading: false,
      data: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function refreshRuntimeEvents(forceRender = false) {
  try {
    const eventsUrl =
      state.selectedRunId && state.selectedRunId !== "latest"
        ? `${API_PREFIX}/v1/debug/runs/${encodeURIComponent(state.selectedRunId)}/events?limit=500`
        : `${API_PREFIX}/v1/debug/events?limit=300`;

    const res = await fetch(eventsUrl);
    const json = await res.json();

    const data = (json.data || []).map(mapRuntimeEventToUiEvent);
    const key = stableStringify(data);

    if (!forceRender && key === state.runtimeEvents.lastKey) {
      return;
    }

    state.runtimeEvents = {
      loading: false,
      data,
      error: null,
      usingMock: false,
      lastKey: key,
    };

    if (!state.event && data[0]) {
      state.event = data[0].id;
    }

    if (!data.find((event) => event.id === state.event) && data[0]) {
      state.event = data[0].id;
    }
  } catch (error) {
    state.runtimeEvents = {
      loading: false,
      data: [],
      error: error instanceof Error ? error.message : String(error),
      usingMock: false,
      lastKey: "",
    };
  }

  renderPreservingScroll();
}

async function refreshEventStats() {
  try {
    const statsUrl =
      state.selectedRunId && state.selectedRunId !== "latest"
        ? `${API_PREFIX}/v1/debug/runs/${encodeURIComponent(state.selectedRunId)}/stats`
        : `${API_PREFIX}/v1/debug/events/stats`;

    const res = await fetch(statsUrl);
    state.eventStats = await res.json();
  } catch {
    state.eventStats = null;
  }

  renderPreservingScroll();
}

async function clearEvents() {
  await fetch(`${API_PREFIX}/v1/debug/events/clear`, {
    method: "POST",
  });

  state.event = "";
  await refreshRuntimeEvents(true);
  await refreshEventStats();
}

function exportEvents() {
  const url =
    state.selectedRunId && state.selectedRunId !== "latest"
      ? `${API_PREFIX}/v1/debug/runs/${encodeURIComponent(state.selectedRunId)}/export`
      : `${API_PREFIX}/v1/debug/events/export`;

  window.open(url, "_blank");
}

function getAvailableModels() {
  const models = state.apiStatus?.models || [];

  if (!models.length) {
    return ["chatgpt-web-local"];
  }

  return models
    .map((model) => {
      if (typeof model === "string") return model;
      return model.id || model.name || "";
    })
    .filter(Boolean);
}

function extractResponseText(json) {
  try {
    return json?.choices?.[0]?.message?.content || JSON.stringify(json, null, 2);
  } catch {
    return JSON.stringify(json, null, 2);
  }
}

async function sendTestChatCompletion() {
  const prompt = state.testPanel.prompt.trim();

  if (!prompt) {
    state.testPanel.error = "請先輸入 prompt";
    renderPreservingScroll();
    return;
  }

  state.testPanel.sending = true;
  state.testPanel.error = "";
  state.testPanel.response = "";
  state.testPanel.durationMs = null;
  renderPreservingScroll();

  const startedAt = performance.now();

  try {
    const body = {
      model: state.testPanel.model || "chatgpt-web-local",
      stream: Boolean(state.testPanel.stream),
      tools: state.testPanel.toolsEnabled ? undefined : [],
      messages: [
        {
          role: "user",
          content: prompt,
        },
      ],
    };

    if (!state.testPanel.toolsEnabled) {
      body.tools = [];
    }

    const res = await fetch(`${API_PREFIX}/v1/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
      },
      body: JSON.stringify(body),
    });

    const text = await res.text();
    const durationMs = Math.round(performance.now() - startedAt);

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${text}`);
    }

    let json;

    try {
      json = JSON.parse(text);
    } catch {
      json = { raw: text };
    }

    state.testPanel.response = extractResponseText(json);
    state.testPanel.durationMs = durationMs;
    state.testPanel.error = "";

    state.selectedRunId = "latest";
    state.viewMode = "history";
    state.inspectorTab = "text";

    await refreshRuns();
    await refreshRuntimeEvents(true);
    await refreshEventStats();

    const first = getDisplayedEvents()[0];
    if (first) {
      state.event = first.id;
    }
  } catch (error) {
    state.testPanel.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.testPanel.sending = false;
    renderPreservingScroll();
  }
}

function formatRunTime(value) {
  if (!value) return "--:--:--";

  try {
    return new Date(value).toLocaleTimeString("zh-TW", { hour12: false });
  } catch {
    return String(value);
  }
}

function renderRunList() {
  const latestActive = state.selectedRunId === "latest";

  const latestButton = `
    <button class="channel run-view ${latestActive ? "active" : ""}" data-run-id="latest" data-view-mode="history">
      <span>◎</span>
      <span>
        <strong>最新執行</strong>
        <small>latest events</small>
      </span>
      <b>${getEventSource().length}</b>
    </button>
  `;

  if (state.runs.loading) {
    return latestButton + `<p class="muted small-note">載入歷史 run...</p>`;
  }

  if (state.runs.error) {
    return latestButton + `<p class="muted small-note">${escapeHtml(state.runs.error)}</p>`;
  }

  const rows = state.runs.data
    .map((run) => {
      const active = state.selectedRunId === run.run_id;
      const total = run.stats?.total ?? 0;
      const errors = run.stats?.errors ?? 0;
      const label = formatRunTime(run.started_at);
      const status = run.status || "unknown";

      return `
        <button class="channel run-view ${active ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}" data-view-mode="history">
          <span>${errors > 0 ? "!" : "◌"}</span>
          <span>
            <strong>${escapeHtml(label)}</strong>
            <small>${escapeHtml(status)} · ${total} events</small>
          </span>
          <b>${total}</b>
        </button>
      `;
    })
    .join("");

  return latestButton + rows;
}

function renderChannels() {
  const source = getEventSource();

  return channels
    .map((channel) => {
      const count = source.filter((event) => event.channel === channel.id).length;
      const active = state.viewMode === "channel" && state.channel === channel.id;

      return `
        <button class="channel ${active ? "active" : ""}" data-channel="${escapeHtml(channel.id)}">
          <span>${escapeHtml(channel.icon)}</span>
          <span>${escapeHtml(channel.name)}</span>
          <b>${count}</b>
        </button>
      `;
    })
    .join("");
}

function renderApiStatus() {
  if (state.apiStatus.loading) {
    return `<span class="status-pill loading">API 檢查中</span>`;
  }

  if (!state.apiStatus.online) {
    return `<span class="status-pill offline">API 離線</span>`;
  }

  return `<span class="status-pill online">API 在線上 · models=${state.apiStatus.models.length}</span>`;
}

function renderRunnerDebug() {
  if (state.runnerDebug.loading) {
    return `<section class="runner-card">載入 Runner Debug...</section>`;
  }

  if (state.runnerDebug.error) {
    return `<section class="runner-card error">Runner Debug Error: ${escapeHtml(state.runnerDebug.error)}</section>`;
  }

  const data = state.runnerDebug.data || {};
  const lock = data.runner_lock_file || {};

  return `
    <section class="runner-card">
      <div class="runner-head">
        <h2>Runner Debug</h2>
        <button class="mini-button" data-refresh-runner>刷新</button>
      </div>

      ${renderKeyValueRows([
        { label: "Runner Lock", value: data.runner_lock_locked ? "已鎖定" : "未鎖定" },
        { label: "Lock File", value: lock.exists ? "存在" : "不存在" },
        { label: "PID", value: lock.pid || "" },
        { label: "main.py", value: data.main_py_exists ? "存在" : "不存在" },
        { label: "output.txt", value: data.output_txt_exists ? "存在" : "不存在" },
        { label: "base_dir", value: data.base_dir || "" },
      ])}
    </section>
  `;
}

function renderRunSummary() {
  const source = getEventSource();
  const stats = state.eventStats;

  const total = stats?.total ?? source.length;
  const errors = stats?.errors ?? source.filter((event) => event.level === "錯誤").length;
  const model = stats?.model_events ?? source.filter((event) => event.channel === "model").length;
  const tools = stats?.tool_events ?? source.filter((event) => event.channel === "mcp").length;

  return `
    <section class="summary-grid">
      <div><strong>${total}</strong><span>事件</span></div>
      <div><strong>${model}</strong><span>模型</span></div>
      <div><strong>${tools}</strong><span>MCP</span></div>
      <div><strong>${errors}</strong><span>錯誤</span></div>
    </section>
  `;
}

function renderApiTestPanel() {
  const models = getAvailableModels();

  return `
    <section class="test-panel">
      <div class="test-panel-head">
        <div>
          <h2>API 測試面板</h2>
          <p class="muted">從 UI 直接送 /v1/chat/completions，使用 UTF-8 JSON body。</p>
        </div>
        <span class="test-badge">Phase 3.7</span>
      </div>

      <label class="field">
        <span>Model</span>
        <select data-test-model>
          ${models
            .map((model) => {
              return `
                <option value="${escapeHtml(model)}" ${state.testPanel.model === model ? "selected" : ""}>
                  ${escapeHtml(model)}
                </option>
              `;
            })
            .join("")}
        </select>
      </label>

      <label class="field">
        <span>Prompt</span>
        <textarea data-test-prompt rows="6" placeholder="輸入中文 prompt，例如：只回答 ok">${escapeHtml(state.testPanel.prompt)}</textarea>
      </label>

      <div class="test-options">
        <label>
          <input type="checkbox" data-test-stream ${state.testPanel.stream ? "checked" : ""}>
          <span>stream</span>
        </label>

        <label>
          <input type="checkbox" data-test-tools ${state.testPanel.toolsEnabled ? "checked" : ""}>
          <span>允許 tools</span>
        </label>
      </div>

      <div class="test-actions">
        <button class="primary-button" data-send-test ${state.testPanel.sending ? "disabled" : ""}>
          ${state.testPanel.sending ? "送出中..." : "送出測試"}
        </button>

        <button class="mini-button" data-fill-test="ok">填入 ok 測試</button>
        <button class="mini-button" data-fill-test="tools">填入 tool 測試</button>
        <button class="mini-button" data-clear-test-response>清空結果</button>
      </div>

      ${
        state.testPanel.durationMs !== null
          ? `<p class="muted small-note">耗時：${escapeHtml(String(state.testPanel.durationMs))}ms</p>`
          : ""
      }

      ${state.testPanel.error ? `<div class="test-error">${escapeHtml(state.testPanel.error)}</div>` : ""}

      ${
        state.testPanel.response
          ? `
            <section class="text-block">
              <div class="text-block-head">
                <h3>Response</h3>
                ${renderCopyButton("複製 response", state.testPanel.response)}
              </div>
              <pre class="text-block-content">${escapeHtml(state.testPanel.response)}</pre>
            </section>
          `
          : ""
      }
    </section>
  `;
}

function renderTimeline() {
  const list = getDisplayedEvents();

  if (list.length === 0) {
    return `<div class="empty">沒有符合搜尋條件的事件。</div>`;
  }

  let lastLoopIndex = "__none__";

  return list
    .map((event, index) => {
      const isExpanded = expanded.has(event.id);
      const levelClass = getLevelClass(event);
      const familyClass = getEventFamily(event);
      const loopIndex = getLoopIndex(event);

      let iterationBreak = "";

      if (
        state.viewMode === "history" &&
        loopIndex !== null &&
        loopIndex !== lastLoopIndex
      ) {
        lastLoopIndex = loopIndex;
        iterationBreak = `
          <div class="iteration-break">
            <span>MCP loop iteration #${escapeHtml(loopIndex)}</span>
          </div>
        `;
      }

      return `
        ${iterationBreak}
        <article class="event level-${levelClass} family-${familyClass} ${state.event === event.id ? "selected" : ""}" data-event="${escapeHtml(event.id)}">
          <div class="event-avatar">
            ${state.viewMode === "history" ? `#${index + 1}` : escapeHtml(event.source.slice(0, 2))}
          </div>

          <div class="event-body">
            <div class="event-head">
              <strong>
                ${state.viewMode === "history" ? `#${index + 1} · ` : ""}
                ${escapeHtml(event.source)}
              </strong>
              <span>${escapeHtml(event.time)}</span>
              <em class="level">${escapeHtml(event.level)}</em>
            </div>

            ${renderEventBadges(event)}

            <div class="event-title-row">
              <h3>${escapeHtml(event.title)}</h3>
            </div>

            <p>${escapeHtml(getEventSummaryText(event))}</p>

            ${isExpanded ? `<div class="full-block">${escapeHtml(event.full)}</div>${renderEventDetails(event)}` : ""}

            <div class="event-actions">
              <button class="mini-button" data-toggle="${escapeHtml(event.id)}">${isExpanded ? "收合" : "展開"}</button>
              <button class="mini-button" data-event-jump="${escapeHtml(event.id)}">查看細節</button>
              <button class="mini-button" data-pin="${escapeHtml(event.id)}">固定</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderInspector() {
  const event = getCurrentEvent();
  const previousEvent = getPreviousEvent();
  const nextEvent = getNextEvent();

  const tabs = [
    { id: "overview", name: "總覽" },
    { id: "text", name: "文字" },
    { id: "tool", name: "工具" },
    { id: "payload", name: "資料內容" },
    { id: "raw", name: "原始 JSON" },
  ];

  let body = "";

  if (state.inspectorTab === "overview") {
    body = renderInspectorOverview(event);
  } else if (state.inspectorTab === "text") {
    body = `
      ${renderRequestTextPanel(event)}
      ${renderResponseTextPanel(event)}
    `;
  } else if (state.inspectorTab === "tool") {
    body = renderToolPanel(event);
  } else if (state.inspectorTab === "payload") {
    const payload = getInnerPayload(event);
    body = `
      <div class="inspector-actions">
        ${renderCopyButton("複製 payload JSON", JSON.stringify(payload, null, 2))}
      </div>
      <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
    `;
  } else {
    body = `
      <div class="inspector-actions">
        ${renderCopyButton("複製完整 JSON", JSON.stringify(event.payload, null, 2))}
      </div>
      <pre>${escapeHtml(JSON.stringify(event.payload, null, 2))}</pre>
    `;
  }

  return `
    <section class="inspector-card">
      <div class="inspector-title-row">
        <div>
          <h2>${escapeHtml(event.title)}</h2>
          <p class="muted">${escapeHtml(event.source)} · ${escapeHtml(event.time)}</p>
        </div>
      </div>

      <div class="inspector-nav">
        <button class="mini-button" data-nav-event="${previousEvent ? escapeHtml(previousEvent.id) : ""}" ${previousEvent ? "" : "disabled"}>上一筆</button>
        <button class="mini-button" data-nav-event="${nextEvent ? escapeHtml(nextEvent.id) : ""}" ${nextEvent ? "" : "disabled"}>下一筆</button>
        ${renderCopyButton("複製事件 JSON", JSON.stringify(event.payload, null, 2))}
      </div>

      <div class="tabs">
        ${tabs
          .map((tab) => {
            return `<button class="${state.inspectorTab === tab.id ? "active-tab" : ""}" data-tab="${escapeHtml(tab.id)}">${escapeHtml(tab.name)}</button>`;
          })
          .join("")}
      </div>

      ${body}
    </section>
  `;
}

function renderPinnedInspector() {
  if (!state.pinnedEventId) {
    return "";
  }

  const pinned = getEventSource().find((event) => event.id === state.pinnedEventId);
  if (!pinned) {
    return "";
  }

  return `
    <section class="inspector-card pinned-card">
      <h2>固定事件</h2>
      <p class="muted">${escapeHtml(pinned.title)}</p>
      <div class="inspector-actions">
        ${renderCopyButton("複製固定事件 JSON", JSON.stringify(pinned.payload, null, 2))}
        <button class="mini-button" data-unpin>取消固定</button>
      </div>
      <pre>${escapeHtml(JSON.stringify(pinned.payload, null, 2))}</pre>
    </section>
  `;
}

function render() {
  const currentChannel = getCurrentChannel();

  app.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <strong>DevTools Radar</strong>
          <span>Agent Console</span>
        </div>

        <h2>執行紀錄</h2>
        ${renderRunList()}

        <h2>事件分類</h2>
        ${renderChannels()}

        ${renderRunnerDebug()}
      </aside>

      <main class="main">
        <header class="topbar">
          <div>
            <h1>${escapeHtml(currentChannel.name)}</h1>
            <p>
              ${
                state.viewMode === "history"
                  ? "依照時間順序顯示 selected run 的所有 runtime event cards。"
                  : "Runtime 觀察預覽。點開事件可看 payload 與 debug 文字。"
              }
            </p>
          </div>

          <div class="top-actions">
            ${renderApiStatus()}
            <span class="status-pill">${state.runtimeEvents.usingMock ? "Mock" : "真實事件"}</span>
          </div>
        </header>

        <section class="toolbar">
          <input
            type="search"
            value="${escapeHtml(state.query)}"
            data-search
            placeholder="${state.viewMode === "history" ? "搜尋全部歷史事件，例如：mcp、model、timeout" : "搜尋目前 channel，例如：模型、tool、timeout"}"
          />

          <select data-quick-filter>
            <option value="all" ${state.quickFilter === "all" ? "selected" : ""}>全部</option>
            <option value="errors" ${state.quickFilter === "errors" ? "selected" : ""}>只看錯誤</option>
            <option value="tools" ${state.quickFilter === "tools" ? "selected" : ""}>只看 MCP / Tool</option>
            <option value="model" ${state.quickFilter === "model" ? "selected" : ""}>只看 Model</option>
          </select>

          <button class="mini-button" data-refresh>手動刷新</button>
          <button class="mini-button" data-toggle-auto>${state.autoRefresh ? "暫停自動刷新" : "啟用自動刷新"}</button>
          <button class="mini-button" data-sort>${state.sortDirection === "desc" ? "新到舊" : "舊到新"}</button>
          <button class="mini-button danger" data-clear-events>清空 latest</button>
          <button class="mini-button" data-export-events>匯出</button>
        </section>

        ${renderApiTestPanel()}

        ${renderRunSummary()}

        ${renderPbvSummaryPanel()}

        ${renderFlowDigestPanel()}

        <section class="timeline">
          ${renderTimeline()}
        </section>
      </main>

      <aside class="inspector">
        ${renderInspector()}
        ${renderPinnedInspector()}
      </aside>
    </div>
  `;

  bindEvents();
}

function renderPreservingScroll() {
  const scrollY = window.scrollY;
  render();
  window.scrollTo(0, scrollY);
}

function bindEvents() {
  document.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.viewMode = "history";
      state.selectedRunId = button.dataset.runId || "latest";
      state.query = "";
      state.inspectorTab = "overview";
      state.pinnedEventId = null;

      await refreshRuntimeEvents(true);
      await refreshEventStats();

      const first = getDisplayedEvents()[0];
      if (first) {
        state.event = first.id;
      }

      renderPreservingScroll();
    });
  });

  document.querySelectorAll(".channel[data-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      state.viewMode = "channel";
      state.selectedRunId = "latest";
      state.channel = button.dataset.channel;
      state.query = "";
      state.inspectorTab = "overview";

      const first = getDisplayedEvents()[0];
      if (first) {
        state.event = first.id;
      }

      renderPreservingScroll();
    });
  });

  document.querySelectorAll("[data-event]").forEach((card) => {
    card.addEventListener("click", () => {
      state.event = card.dataset.event;
      renderPreservingScroll();
    });
  });

  document.querySelectorAll("[data-event-jump]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.event = button.dataset.eventJump;
      renderPreservingScroll();
    });
  });

  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();

      const id = button.dataset.toggle;

      if (expanded.has(id)) {
        expanded.delete(id);
      } else {
        expanded.add(id);
      }

      renderPreservingScroll();
    });
  });

  document.querySelectorAll("[data-pin]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.pinnedEventId = button.dataset.pin;
      renderPreservingScroll();
    });
  });

  const unpin = document.querySelector("[data-unpin]");
  if (unpin) {
    unpin.addEventListener("click", () => {
      state.pinnedEventId = null;
      renderPreservingScroll();
    });
  }

  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.inspectorTab = button.dataset.tab;
      renderPreservingScroll();
    });
  });

  const search = document.querySelector("[data-search]");
  if (search) {
    search.addEventListener("input", (event) => {
      state.query = event.target.value;
      renderPreservingScroll();
    });
  }

  const quickFilter = document.querySelector("[data-quick-filter]");
  if (quickFilter) {
    quickFilter.addEventListener("change", (event) => {
      state.quickFilter = event.target.value;
      renderPreservingScroll();
    });
  }

  const refresh = document.querySelector("[data-refresh]");
  if (refresh) {
    refresh.addEventListener("click", async () => {
      await refreshApiStatus();
      await refreshRunnerDebug(true);
      await refreshRuns();
      await refreshRuntimeEvents(true);
      await refreshEventStats();
    });
  }

  const refreshRunner = document.querySelector("[data-refresh-runner]");
  if (refreshRunner) {
    refreshRunner.addEventListener("click", () => refreshRunnerDebug(true));
  }

  const toggleAuto = document.querySelector("[data-toggle-auto]");
  if (toggleAuto) {
    toggleAuto.addEventListener("click", () => {
      state.autoRefresh = !state.autoRefresh;
      renderPreservingScroll();
    });
  }

  const sort = document.querySelector("[data-sort]");
  if (sort) {
    sort.addEventListener("click", () => {
      state.sortDirection = state.sortDirection === "desc" ? "asc" : "desc";
      renderPreservingScroll();
    });
  }

  const clearButton = document.querySelector("[data-clear-events]");
  if (clearButton) {
    clearButton.addEventListener("click", async () => {
      if (!confirm("確定清空 latest events？")) return;
      await clearEvents();
    });
  }

  const exportButton = document.querySelector("[data-export-events]");
  if (exportButton) {
    exportButton.addEventListener("click", exportEvents);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();

      const value = decodeURIComponent(button.dataset.copy || "");

      try {
        await writeTextToClipboard(value);
        const originalText = button.textContent;
        button.textContent = "已複製";

        setTimeout(() => {
          button.textContent = originalText;
        }, 1000);
      } catch {
        const textarea = document.createElement("textarea");
        textarea.value = value;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
    });
  });

  document.querySelectorAll("[data-copy-flow-digest]").forEach((button) => {
    button.addEventListener("click", async () => {
      await copyFlowDigest(button.dataset.copyFlowDigest || "visible");
    });
  });

  document.querySelectorAll("[data-copy-pbv-summary]").forEach((button) => {
    button.addEventListener("click", async () => {
      await copyPbvSummary();
    });
  });

  document.querySelectorAll("[data-nav-event]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.navEvent;
      if (!id) return;

      state.event = id;
      renderPreservingScroll();
    });
  });

  const testPrompt = document.querySelector("[data-test-prompt]");
  if (testPrompt) {
    testPrompt.addEventListener("input", (event) => {
      state.testPanel.prompt = event.target.value;
    });
  }

  const testModel = document.querySelector("[data-test-model]");
  if (testModel) {
    testModel.addEventListener("change", (event) => {
      state.testPanel.model = event.target.value;
    });
  }

  const testStream = document.querySelector("[data-test-stream]");
  if (testStream) {
    testStream.addEventListener("change", (event) => {
      state.testPanel.stream = event.target.checked;
    });
  }

  const testTools = document.querySelector("[data-test-tools]");
  if (testTools) {
    testTools.addEventListener("change", (event) => {
      state.testPanel.toolsEnabled = event.target.checked;
    });
  }

  const sendTestButton = document.querySelector("[data-send-test]");
  if (sendTestButton) {
    sendTestButton.addEventListener("click", () => {
      sendTestChatCompletion();
    });
  }

  document.querySelectorAll("[data-fill-test]").forEach((button) => {
    button.addEventListener("click", () => {
      const kind = button.dataset.fillTest;

      if (kind === "tools") {
        state.testPanel.prompt = "請列出目前專案根目錄有哪些檔案，必要時可以使用工具。";
        state.testPanel.toolsEnabled = true;
      } else {
        state.testPanel.prompt = "只回答 ok";
        state.testPanel.toolsEnabled = false;
      }

      state.testPanel.response = "";
      state.testPanel.error = "";
      state.testPanel.durationMs = null;

      renderPreservingScroll();
    });
  });

  const clearTestResponseButton = document.querySelector("[data-clear-test-response]");
  if (clearTestResponseButton) {
    clearTestResponseButton.addEventListener("click", () => {
      state.testPanel.response = "";
      state.testPanel.error = "";
      state.testPanel.durationMs = null;
      renderPreservingScroll();
    });
  }
}

render();

refreshApiStatus();
refreshRunnerDebug(true);
refreshRuns();
refreshRuntimeEvents(true);
refreshEventStats();

setInterval(() => {
  if (!state.autoRefresh) return;

  refreshApiStatus();
  refreshRunnerDebug(false);
  refreshRuns();
  refreshRuntimeEvents(false);
  refreshEventStats();
}, 30000);
