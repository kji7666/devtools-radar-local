<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type {
  FileItem,
  RunRecord,
  RunResult,
  SystemStatus,
  TaskMeta,
  McpApproval,
  McpAuditRecord,
  McpToolSnapshot
} from './types'

type Page = 'dashboard' | 'chat' | 'tasks' | 'outputs' | 'runs' | 'mcp' | 'logs' | 'settings'

const page = ref<Page>('dashboard')
const toast = ref('')
const busy = ref(false)

const status = ref<SystemStatus | null>(null)
const configText = ref('')

const chatPrompt = ref('')
const chatResult = ref('')
const chatBusy = ref(false)

const taskList = ref<TaskMeta[]>([])
const selectedTask = ref<TaskMeta | null>(null)
const taskBusy = ref(false)

const outputList = ref<FileItem[]>([])
const selectedOutput = ref<FileItem | null>(null)
const outputContent = ref('')

const runList = ref<RunRecord[]>([])
const selectedRun = ref<RunRecord | null>(null)
const runContent = ref('')

const logsText = ref('')

const mcpApprovals = ref<McpApproval[]>([])
const mcpAudit = ref<McpAuditRecord[]>([])
const mcpSecurity = ref<any>(null)
const mcpHealth = ref<any>(null)
const selectedMcpApproval = ref<McpApproval | null>(null)
const mcpBusy = ref(false)

const mcpConfigText = ref('')
const mcpServers = ref<any[]>([])
const mcpConfigBusy = ref(false)

const mcpSecurityText = ref('')
const mcpTools = ref<any[]>([])
const mcpSecurityBusy = ref(false)
const selectedMcpToolName = ref('')

const mcpToolSnapshots = ref<McpToolSnapshot[]>([])
const selectedMcpSnapshot = ref<McpToolSnapshot | null>(null)
const mcpSnapshotBusy = ref(false)

function bridge() {
  if (!window.autoGpt) {
    throw new Error('Electron bridge window.autoGpt is not available')
  }

  return window.autoGpt
}

function showToast(message: string) {
  toast.value = message

  window.setTimeout(() => {
    if (toast.value === message) {
      toast.value = ''
    }
  }, 2800)
}

const pageTitle = computed(() => {
  const map: Record<Page, string> = {
    dashboard: 'Dashboard',
    chat: 'Chat',
    tasks: 'Tasks',
    outputs: 'Outputs',
    runs: 'Runs',
    mcp: 'MCP',
    logs: 'Logs',
    settings: 'Settings'
  }

  return map[page.value]
})

const pendingApprovalCount = computed(() => {
  return mcpApprovals.value.filter((item) => item.status === 'pending').length
})

const mcpToolNames = computed(() => {
  return mcpTools.value
    .map((item) => item?.function?.name)
    .filter(Boolean)
    .sort()
})

function formatTime(value?: string) {
  if (!value) return ''

  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function formatJson(value: any) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function shortText(text: string, max = 600) {
  if (!text) return ''
  if (text.length <= max) return text

  return `${text.slice(0, max)}\n\n... truncated ${text.length - max} chars`
}

function statusClass(value: string) {
  const s = String(value || '').toLowerCase()

  if (s.includes('success') || s.includes('ok') || s.includes('approved_executed') || s === 'allow') {
    return 'ok'
  }

  if (s.includes('pending') || s.includes('timeout') || s.includes('confirm') || s === 'default') {
    return 'warn'
  }

  if (s.includes('error') || s.includes('failed') || s.includes('denied') || s.includes('blocked') || s === 'deny') {
    return 'bad'
  }

  return ''
}

function newTaskTemplate(): TaskMeta {
  const now = Date.now()

  return {
    id: `task_${now}`,
    title: 'New Task',
    prompt: '',
    enabled: true,
    conversationMode: 'same',
    schedule: {
      enabled: false,
      type: 'manual',
      date: '',
      time: '09:00',
      daysOfWeek: []
    },
    lastRunAt: '',
    lastStatus: '',
    lastRunFile: '',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString()
  } as TaskMeta
}

function toPlainTask(task: TaskMeta): TaskMeta {
  return JSON.parse(JSON.stringify(task))
}

async function refreshStatus() {
  try {
    status.value = await bridge().getStatus()
  } catch (error: any) {
    showToast(`Status 讀取失敗：${error?.message ?? error}`)
  }
}

async function refreshConfig() {
  try {
    configText.value = await bridge().readConfig()
  } catch (error: any) {
    showToast(`Config 讀取失敗：${error?.message ?? error}`)
  }
}

async function saveConfig() {
  try {
    busy.value = true
    await bridge().saveConfig(configText.value)
    showToast('Config saved')
  } catch (error: any) {
    showToast(`Config 儲存失敗：${error?.message ?? error}`)
  } finally {
    busy.value = false
  }
}

async function runChat() {
  if (!chatPrompt.value.trim()) {
    showToast('請輸入 prompt')
    return
  }

  try {
    chatBusy.value = true
    chatResult.value = ''

    const result = await bridge().runPrompt(chatPrompt.value)
    chatResult.value = result?.output || result?.stdout || result?.message || JSON.stringify(result, null, 2)

    showToast('Chat completed')
  } catch (error: any) {
    chatResult.value = error?.message ?? String(error)
    showToast(`Chat 失敗：${error?.message ?? error}`)
  } finally {
    chatBusy.value = false
  }
}

async function refreshTasksMeta() {
  try {
    const result = await bridge().listTaskMeta()
    taskList.value = result || []

    if (selectedTask.value) {
      const updated = taskList.value.find((item) => item.id === selectedTask.value?.id)
      selectedTask.value = updated ? JSON.parse(JSON.stringify(updated)) : selectedTask.value
    }
  } catch (error: any) {
    showToast(`Tasks 讀取失敗：${error?.message ?? error}`)
  }
}

function createTask() {
  selectedTask.value = newTaskTemplate()
}

function selectTask(task: TaskMeta) {
  selectedTask.value = JSON.parse(JSON.stringify(task))
}

async function saveTask() {
  if (!selectedTask.value) return

  try {
    taskBusy.value = true
    selectedTask.value.updatedAt = new Date().toISOString()

    await bridge().saveTaskMeta(toPlainTask(selectedTask.value))
    await refreshTasksMeta()

    showToast('Task saved')
  } catch (error: any) {
    showToast(`Task 儲存失敗：${error?.message ?? error}`)
  } finally {
    taskBusy.value = false
  }
}

async function deleteTask() {
  if (!selectedTask.value) return

  if (!confirm(`確定刪除 task：${selectedTask.value.title}？`)) return

  try {
    taskBusy.value = true
    await bridge().deleteTaskMeta(selectedTask.value.id)
    selectedTask.value = null
    await refreshTasksMeta()
    showToast('Task deleted')
  } catch (error: any) {
    showToast(`Task 刪除失敗：${error?.message ?? error}`)
  } finally {
    taskBusy.value = false
  }
}

async function runSelectedTask() {
  if (!selectedTask.value) return

  try {
    taskBusy.value = true
    const result: RunResult = await bridge().runTaskMeta(selectedTask.value.id)

    showToast(result?.ok ? 'Task completed' : 'Task failed')
    await refreshTasksMeta()
    await refreshRuns()
  } catch (error: any) {
    showToast(`Task 執行失敗：${error?.message ?? error}`)
  } finally {
    taskBusy.value = false
  }
}

async function createOrUpdateSchedule() {
  if (!selectedTask.value) return

  try {
    taskBusy.value = true

    await saveTask()
    await bridge().createOrUpdateSchedule(selectedTask.value.id)

    showToast('Schedule updated')
  } catch (error: any) {
    showToast(`Schedule 更新失敗：${error?.message ?? error}`)
  } finally {
    taskBusy.value = false
  }
}

async function deleteSchedule() {
  if (!selectedTask.value) return

  try {
    taskBusy.value = true
    await bridge().deleteSchedule(selectedTask.value.id)
    showToast('Schedule deleted')
  } catch (error: any) {
    showToast(`Schedule 刪除失敗：${error?.message ?? error}`)
  } finally {
    taskBusy.value = false
  }
}

async function refreshOutputs() {
  try {
    outputList.value = await bridge().listOutputs()
  } catch (error: any) {
    showToast(`Outputs 讀取失敗：${error?.message ?? error}`)
  }
}

async function selectOutput(item: FileItem) {
  selectedOutput.value = item

  try {
    outputContent.value = await bridge().readOutput(item.name)
  } catch (error: any) {
    outputContent.value = error?.message ?? String(error)
  }
}

async function refreshRuns() {
  try {
    runList.value = await bridge().listRuns()

    if (selectedRun.value) {
      const updated = runList.value.find((item) => item.file === selectedRun.value?.file)
      selectedRun.value = updated || selectedRun.value
    }
  } catch (error: any) {
    showToast(`Runs 讀取失敗：${error?.message ?? error}`)
  }
}

async function selectRun(item: RunRecord) {
  selectedRun.value = item

  try {
    runContent.value = await bridge().readRun(item.file)
  } catch (error: any) {
    runContent.value = error?.message ?? String(error)
  }
}

async function refreshLogs() {
  try {
    logsText.value = await bridge().readLogs()
  } catch (error: any) {
    logsText.value = error?.message ?? String(error)
  }
}

async function refreshMcpPage() {
  try {
    mcpBusy.value = true

    const [health, security, approvals, audit, configResult, serverResult, toolsResult] = await Promise.all([
      bridge().apiHealth(),
      bridge().getMcpSecurity(),
      bridge().listMcpApprovals(),
      bridge().getMcpAudit(100),
      bridge().getMcpConfig(),
      bridge().listMcpServers(),
      bridge().getMcpTools(),
      bridge().listMcpToolSnapshots()
    ])

    mcpHealth.value = health
    mcpSecurity.value = security
    mcpApprovals.value = approvals?.data || []
    mcpAudit.value = audit?.data || []
    mcpConfigText.value = formatJson(configResult?.config || { servers: {} })
    mcpServers.value = serverResult?.servers || []
    mcpSecurityText.value = formatJson(security?.security || {})
    mcpTools.value = toolsResult?.data || []

    if (selectedMcpApproval.value) {
      const updated = mcpApprovals.value.find((item) => item.id === selectedMcpApproval.value?.id)
      selectedMcpApproval.value = updated ? JSON.parse(JSON.stringify(updated)) : selectedMcpApproval.value
    }
  } catch (error: any) {
    showToast(`MCP 讀取失敗：${error?.message ?? error}`)
  } finally {
    mcpBusy.value = false
  }
}

function snapshotStatusClass(status: string) {
  const s = String(status || '').toLowerCase()

  if (s === 'approved') return 'ok'
  if (s === 'new' || s === 'changed') return 'warn'
  if (s === 'missing') return 'bad'

  return ''
}

function getSnapshotForTool(toolName: string) {
  return mcpToolSnapshots.value.find((item) => item.api_tool_name === toolName) || null
}

async function approveSelectedSnapshot() {
  if (!selectedMcpSnapshot.value) {
    showToast('請先選擇一個 tool snapshot')
    return
  }

  try {
    mcpSnapshotBusy.value = true

    await bridge().approveMcpToolSnapshot(selectedMcpSnapshot.value.api_tool_name)
    await refreshMcpPage()

    showToast(`已核准 tool snapshot：${selectedMcpSnapshot.value.api_tool_name}`)
  } catch (error: any) {
    showToast(`核准 snapshot 失敗：${error?.message ?? error}`)
  } finally {
    mcpSnapshotBusy.value = false
  }
}

async function saveMcpConfig() {
  try {
    mcpConfigBusy.value = true

    const parsed = JSON.parse(mcpConfigText.value)

    await bridge().saveMcpConfig(parsed)
    await refreshMcpPage()

    showToast('MCP servers config saved and reloaded')
  } catch (error: any) {
    showToast(`MCP config 儲存失敗：${error?.message ?? error}`)
  } finally {
    mcpConfigBusy.value = false
  }
}

async function reloadMcpServers() {
  try {
    mcpConfigBusy.value = true

    await bridge().reloadMcpServers()
    await refreshMcpPage()

    showToast('MCP servers reloaded')
  } catch (error: any) {
    showToast(`MCP reload 失敗：${error?.message ?? error}`)
  } finally {
    mcpConfigBusy.value = false
  }
}

function insertMcpServerTemplate(name: string, serverConfig: any) {
  try {
    const parsed = JSON.parse(mcpConfigText.value || '{"servers":{}}')

    if (!parsed.servers) parsed.servers = {}

    let finalName = name
    let index = 1

    while (parsed.servers[finalName]) {
      finalName = `${name}_${index}`
      index += 1
    }

    parsed.servers[finalName] = serverConfig
    mcpConfigText.value = formatJson(parsed)

    showToast(`Inserted ${finalName}`)
  } catch (error: any) {
    showToast(`插入 template 失敗：${error?.message ?? error}`)
  }
}

function insertStdioMcpTemplate() {
  insertMcpServerTemplate('new_stdio_server', {
    enabled: true,
    transport: 'stdio',
    command: 'npx',
    args: ['-y', 'package-name-here'],
    env: {}
  })
}

function insertHttpMcpTemplate() {
  insertMcpServerTemplate('new_http_server', {
    enabled: false,
    transport: 'streamable_http',
    url: 'http://127.0.0.1:3000/mcp',
    headers: {},
    timeout_seconds: 30
  })
}

function getSecurityConfigObject() {
  try {
    const parsed = JSON.parse(mcpSecurityText.value || '{}')
    if (!parsed.tool_policies) parsed.tool_policies = []
    if (!parsed.allowed_roots) parsed.allowed_roots = []
    return parsed
  } catch {
    return null
  }
}

function findPolicyForTool(toolName: string) {
  const config = getSecurityConfigObject()
  if (!config) return null

  return config.tool_policies.find((policy: any) => policy.pattern === toolName) || null
}

function getPolicyAction(toolName: string) {
  const policy = findPolicyForTool(toolName)
  return policy?.action || 'default'
}

function setToolPolicy(toolName: string, action: 'allow' | 'confirm' | 'deny') {
  const config = getSecurityConfigObject()

  if (!config) {
    showToast('mcp_security.json 不是合法 JSON')
    return
  }

  if (!Array.isArray(config.tool_policies)) {
    config.tool_policies = []
  }

  let policy = config.tool_policies.find((item: any) => item.pattern === toolName)

  if (!policy) {
    policy = {
      pattern: toolName,
      action,
      reason: `${action} by MCP Security Policy UI`
    }

    config.tool_policies.unshift(policy)
  } else {
    policy.action = action
    policy.reason = policy.reason || `${action} by MCP Security Policy UI`
  }

  mcpSecurityText.value = formatJson(config)
  selectedMcpToolName.value = toolName
}

function removeToolPolicy(toolName: string) {
  const config = getSecurityConfigObject()

  if (!config) {
    showToast('mcp_security.json 不是合法 JSON')
    return
  }

  config.tool_policies = (config.tool_policies || []).filter((item: any) => item.pattern !== toolName)
  mcpSecurityText.value = formatJson(config)
  selectedMcpToolName.value = toolName
}

function generateMissingMcpPolicies() {
  const config = getSecurityConfigObject()

  if (!config) {
    showToast('mcp_security.json 不是合法 JSON')
    return
  }

  if (!Array.isArray(config.tool_policies)) {
    config.tool_policies = []
  }

  const existing = new Set(config.tool_policies.map((item: any) => item.pattern))
  let added = 0

  for (const toolName of mcpToolNames.value) {
    if (existing.has(toolName)) continue

    let action: 'allow' | 'confirm' | 'deny' = 'deny'
    let reason = 'Generated by MCP Security Policy UI'

    const lower = toolName.toLowerCase()

    if (
      lower.includes('read') ||
      lower.includes('list') ||
      lower.includes('search') ||
      lower.includes('get_file_info') ||
      lower.includes('directory_tree')
    ) {
      action = 'allow'
      reason = 'Auto-generated read-only policy'
    } else if (
      lower.includes('write') ||
      lower.includes('edit') ||
      lower.includes('create') ||
      lower.includes('move') ||
      lower.includes('update')
    ) {
      action = 'confirm'
      reason = 'Auto-generated write/change policy requiring confirmation'
    } else if (
      lower.includes('delete') ||
      lower.includes('remove') ||
      lower.includes('rm')
    ) {
      action = 'deny'
      reason = 'Auto-generated destructive policy'
    }

    config.tool_policies.push({
      pattern: toolName,
      action,
      reason
    })

    added += 1
  }

  mcpSecurityText.value = formatJson(config)
  showToast(`已新增 ${added} 筆缺少的 policy`)
}

async function saveMcpSecurity() {
  try {
    mcpSecurityBusy.value = true

    const parsed = JSON.parse(mcpSecurityText.value)

    await bridge().saveMcpSecurity(parsed)
    await refreshMcpPage()

    showToast('MCP security policy saved and reloaded')
  } catch (error: any) {
    showToast(`MCP security 儲存失敗：${error?.message ?? error}`)
  } finally {
    mcpSecurityBusy.value = false
  }
}

async function reloadMcpSecurity() {
  try {
    mcpSecurityBusy.value = true

    await bridge().reloadMcpSecurity()
    await refreshMcpPage()

    showToast('MCP security reloaded')
  } catch (error: any) {
    showToast(`MCP security reload 失敗：${error?.message ?? error}`)
  } finally {
    mcpSecurityBusy.value = false
  }
}

function selectMcpApproval(item: McpApproval) {
  selectedMcpApproval.value = JSON.parse(JSON.stringify(item))
}

async function approveSelectedMcpCall() {
  if (!selectedMcpApproval.value) {
    showToast('請先選擇一筆 approval')
    return
  }

  if (selectedMcpApproval.value.status !== 'pending') {
    showToast('這筆 approval 不是 pending 狀態')
    return
  }

  const pendingId = selectedMcpApproval.value.id

  if (!confirm(`確定 approve？\n\nID: ${pendingId}\nTool: ${selectedMcpApproval.value.tool}`)) {
    return
  }

  try {
    mcpBusy.value = true

    const result = await bridge().approveMcpCall(pendingId)

    showToast(`已 approve：${pendingId}`)

    if (result?.approval) {
      selectedMcpApproval.value = {
        ...selectedMcpApproval.value,
        status: result.approval.status || 'approved_executed',
        result: result.approval.result || '',
        error: result.approval.error || '',
        updatedAt: new Date().toISOString()
      }
    }

    await refreshMcpPage()
  } catch (error: any) {
    showToast(`Approve 失敗：${error?.message ?? error}`)
  } finally {
    mcpBusy.value = false
  }
}

async function denySelectedMcpCall() {
  if (!selectedMcpApproval.value) {
    showToast('請先選擇一筆 approval')
    return
  }

  if (selectedMcpApproval.value.status !== 'pending') {
    showToast('這筆 approval 不是 pending 狀態')
    return
  }

  const pendingId = selectedMcpApproval.value.id

  if (!confirm(`確定 deny？\n\nID: ${pendingId}`)) {
    return
  }

  try {
    mcpBusy.value = true

    await bridge().denyMcpCall(pendingId)

    showToast(`已 deny：${pendingId}`)
    await refreshMcpPage()
  } catch (error: any) {
    showToast(`Deny 失敗：${error?.message ?? error}`)
  } finally {
    mcpBusy.value = false
  }
}

async function openFolder(kind: string) {
  try {
    await bridge().openFolder(kind)
  } catch (error: any) {
    showToast(`開啟資料夾失敗：${error?.message ?? error}`)
  }
}

async function switchPage(next: Page) {
  page.value = next

  if (next === 'dashboard') {
    await refreshStatus()
    await refreshTasksMeta()
    await refreshRuns()
    await refreshMcpPage()
  }

  if (next === 'tasks') await refreshTasksMeta()
  if (next === 'outputs') await refreshOutputs()
  if (next === 'runs') await refreshRuns()
  if (next === 'mcp') await refreshMcpPage()
  if (next === 'logs') await refreshLogs()
  if (next === 'settings') await refreshConfig()
}

onMounted(async () => {
  await refreshStatus()
  await refreshTasksMeta()
  await refreshRuns()
  await refreshMcpPage()
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">D</div>
        <div>
          <h1>DevTools Radar</h1>
          <p>Local</p>
        </div>
      </div>

      <nav class="nav">
        <button :class="{ active: page === 'dashboard' }" @click="switchPage('dashboard')">Dashboard</button>
        <button :class="{ active: page === 'chat' }" @click="switchPage('chat')">Chat</button>
        <button :class="{ active: page === 'tasks' }" @click="switchPage('tasks')">Tasks</button>
        <button :class="{ active: page === 'outputs' }" @click="switchPage('outputs')">Outputs</button>
        <button :class="{ active: page === 'runs' }" @click="switchPage('runs')">Runs</button>

        <button :class="{ active: page === 'mcp' }" @click="switchPage('mcp')">
          <span>MCP</span>
          <span v-if="pendingApprovalCount > 0" class="nav-badge">{{ pendingApprovalCount }}</span>
        </button>

        <button :class="{ active: page === 'logs' }" @click="switchPage('logs')">Logs</button>
        <button :class="{ active: page === 'settings' }" @click="switchPage('settings')">Settings</button>
      </nav>

      <div class="sidebar-footer">
        <button @click="openFolder('root')">Open Root</button>
        <button @click="openFolder('outputs')">Open Outputs</button>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h2>{{ pageTitle }}</h2>
          <p v-if="status">
            Python: {{ status.python || 'unknown' }} · Root: {{ status.root || '' }}
          </p>
        </div>

        <button class="small-btn" @click="refreshStatus">Refresh</button>
      </header>

      <section v-if="page === 'dashboard'" class="page dashboard-grid">
        <div class="panel stat-card">
          <span>Status</span>
          <strong :class="status?.ok ? 'ok' : 'warn'">
            {{ status?.ok ? 'OK' : 'Check' }}
          </strong>
        </div>

        <div class="panel stat-card">
          <span>Tasks</span>
          <strong>{{ taskList.length }}</strong>
        </div>

        <div class="panel stat-card">
          <span>Runs</span>
          <strong>{{ runList.length }}</strong>
        </div>

        <div class="panel stat-card">
          <span>MCP Pending</span>
          <strong :class="pendingApprovalCount > 0 ? 'warn' : 'ok'">
            {{ pendingApprovalCount }}
          </strong>
        </div>

        <div class="panel wide-panel">
          <div class="panel-header">
            <h3>Recent Runs</h3>
            <button class="small-btn" @click="switchPage('runs')">View</button>
          </div>

          <div class="simple-list">
            <button v-for="item in runList.slice(0, 8)" :key="item.file" @click="switchPage('runs')">
              <strong>{{ item.title || item.file }}</strong>
              <span>{{ item.status }} · {{ formatTime(item.createdAt) }}</span>
            </button>

            <p v-if="runList.length === 0" class="muted">No runs yet.</p>
          </div>
        </div>

        <div class="panel wide-panel">
          <div class="panel-header">
            <h3>MCP Pending Approvals</h3>
            <button class="small-btn" @click="switchPage('mcp')">View</button>
          </div>

          <div class="simple-list">
            <button
              v-for="item in mcpApprovals.filter((x) => x.status === 'pending').slice(0, 8)"
              :key="item.id"
              @click="switchPage('mcp')"
            >
              <strong>{{ item.tool }}</strong>
              <span>{{ item.id }} · {{ formatTime(item.createdAt) }}</span>
            </button>

            <p v-if="pendingApprovalCount === 0" class="muted">No pending MCP approvals.</p>
          </div>
        </div>
      </section>

      <section v-if="page === 'chat'" class="page chat-page">
        <div class="panel">
          <div class="panel-header">
            <h3>Prompt</h3>
            <button class="primary-btn" :disabled="chatBusy" @click="runChat">
              {{ chatBusy ? 'Running...' : 'Run' }}
            </button>
          </div>

          <textarea v-model="chatPrompt" class="big-textarea" placeholder="輸入 prompt..." />

          <h3>Result</h3>
          <pre class="content-view">{{ chatResult }}</pre>
        </div>
      </section>

      <section v-if="page === 'tasks'" class="page two-col">
        <div class="panel list-panel">
          <div class="panel-header">
            <h3>Tasks</h3>
            <button class="small-btn" @click="createTask">New</button>
          </div>

          <div class="file-list">
            <button
              v-for="task in taskList"
              :key="task.id"
              :class="{ selected: selectedTask?.id === task.id }"
              @click="selectTask(task)"
            >
              <strong>{{ task.title }}</strong>
              <span>{{ task.enabled ? 'enabled' : 'disabled' }} · {{ task.schedule?.type }}</span>
              <span>{{ task.id }}</span>
            </button>

            <p v-if="taskList.length === 0" class="muted">No tasks yet.</p>
          </div>
        </div>

        <div class="panel detail-panel" v-if="selectedTask">
          <div class="panel-header">
            <h3>{{ selectedTask.title || 'Task' }}</h3>
            <div class="inline-actions">
              <button class="small-btn" :disabled="taskBusy" @click="saveTask">Save</button>
              <button class="primary-btn" :disabled="taskBusy" @click="runSelectedTask">Run</button>
              <button class="danger-btn" :disabled="taskBusy" @click="deleteTask">Delete</button>
            </div>
          </div>

          <label>
            Title
            <input v-model="selectedTask.title" />
          </label>

          <label class="checkbox-row">
            <input v-model="selectedTask.enabled" type="checkbox" />
            Enabled
          </label>

          <label>
            Conversation Mode
            <select v-model="selectedTask.conversationMode">
              <option value="same">same</option>
              <option value="new">new</option>
            </select>
          </label>

          <label>
            Prompt
            <textarea v-model="selectedTask.prompt" class="big-textarea" />
          </label>

          <div class="form-grid">
            <label class="checkbox-row">
              <input v-model="selectedTask.schedule.enabled" type="checkbox" />
              Schedule Enabled
            </label>

            <label>
              Type
              <select v-model="selectedTask.schedule.type">
                <option value="manual">manual</option>
                <option value="once">once</option>
                <option value="daily">daily</option>
                <option value="weekly">weekly</option>
              </select>
            </label>

            <label>
              Date
              <input v-model="selectedTask.schedule.date" type="date" />
            </label>

            <label>
              Time
              <input v-model="selectedTask.schedule.time" type="time" />
            </label>
          </div>

          <div class="inline-actions">
            <button class="small-btn" :disabled="taskBusy" @click="createOrUpdateSchedule">
              Update Schedule
            </button>

            <button class="danger-btn" :disabled="taskBusy" @click="deleteSchedule">
              Delete Schedule
            </button>
          </div>

          <div class="muted">
            Last: {{ selectedTask.lastStatus || 'none' }} · {{ formatTime(selectedTask.lastRunAt) }}
          </div>
        </div>

        <div class="panel empty-state" v-else>
          <h3>Select or create a task</h3>
        </div>
      </section>

      <section v-if="page === 'outputs'" class="page two-col">
        <div class="panel list-panel">
          <div class="panel-header">
            <h3>Outputs</h3>
            <button class="small-btn" @click="refreshOutputs">Refresh</button>
          </div>

          <div class="file-list">
            <button
              v-for="item in outputList"
              :key="item.name"
              :class="{ selected: selectedOutput?.name === item.name }"
              @click="selectOutput(item)"
            >
              <strong>{{ item.name }}</strong>
              <span>{{ formatTime(item.modifiedAt) }}</span>
            </button>

            <p v-if="outputList.length === 0" class="muted">No outputs.</p>
          </div>
        </div>

        <div class="panel detail-panel">
          <h3>{{ selectedOutput?.name || 'Output' }}</h3>
          <pre class="content-view">{{ outputContent }}</pre>
        </div>
      </section>

      <section v-if="page === 'runs'" class="page two-col">
        <div class="panel list-panel">
          <div class="panel-header">
            <h3>Runs</h3>
            <button class="small-btn" @click="refreshRuns">Refresh</button>
          </div>

          <div class="file-list">
            <button
              v-for="item in runList"
              :key="item.file"
              :class="{ selected: selectedRun?.file === item.file }"
              @click="selectRun(item)"
            >
              <strong>{{ item.title || item.file }}</strong>
              <span>{{ item.status }} · {{ formatTime(item.createdAt) }}</span>
            </button>

            <p v-if="runList.length === 0" class="muted">No runs.</p>
          </div>
        </div>

        <div class="panel detail-panel">
          <h3>{{ selectedRun?.title || selectedRun?.file || 'Run' }}</h3>
          <pre class="content-view">{{ runContent }}</pre>
        </div>
      </section>

      <section v-if="page === 'mcp'" class="page mcp-page">
        <div class="panel mcp-server-panel">
          <div class="panel-header">
            <div>
              <h3>MCP Servers</h3>
              <p class="muted">Manage mcp_servers.json. Supports stdio and streamable_http.</p>
            </div>

            <div class="inline-actions">
              <button class="small-btn" :disabled="mcpConfigBusy" @click="insertStdioMcpTemplate">Add stdio</button>
              <button class="small-btn" :disabled="mcpConfigBusy" @click="insertHttpMcpTemplate">Add HTTP</button>
              <button class="small-btn" :disabled="mcpConfigBusy" @click="reloadMcpServers">Reload</button>
              <button class="primary-btn" :disabled="mcpConfigBusy" @click="saveMcpConfig">Save</button>
            </div>
          </div>

          <div class="mcp-server-list">
            <div v-for="server in mcpServers" :key="server.name" class="mcp-server-card">
              <div>
                <strong>{{ server.name }}</strong>
                <span>{{ server.transport }}</span>
              </div>

              <div>
                <span :class="server.initialized ? 'ok' : 'warn'">
                  {{ server.initialized ? 'initialized' : 'not initialized' }}
                </span>

                <span :class="server.running ? 'ok' : 'warn'">
                  {{ server.running ? 'running' : 'not running' }}
                </span>
              </div>

              <small v-if="server.url">{{ server.url }}</small>
              <small v-else>{{ server.command }} {{ (server.args || []).join(' ') }}</small>
            </div>

            <p v-if="mcpServers.length === 0" class="muted">No enabled MCP servers.</p>
          </div>

          <textarea
            v-model="mcpConfigText"
            class="config-textarea mcp-config-editor"
            spellcheck="false"
          />
        </div>

        <div class="panel mcp-security-panel">
          <div class="panel-header">
            <div>
              <h3>MCP Security Policy</h3>
              <p class="muted">Manage mcp_security.json. Set each tool to allow / confirm / deny.</p>
            </div>

            <div class="inline-actions">
              <button class="small-btn" :disabled="mcpSecurityBusy" @click="generateMissingMcpPolicies">
                Generate Missing
              </button>

              <button class="small-btn" :disabled="mcpSecurityBusy" @click="reloadMcpSecurity">
                Reload
              </button>

              <button class="primary-btn" :disabled="mcpSecurityBusy" @click="saveMcpSecurity">
                Save
              </button>
            </div>
          </div>

          <div class="mcp-security-summary">
            <div class="kv">
              <span>Enabled</span>
              <strong :class="mcpSecurity?.security?.enabled ? 'ok' : 'warn'">
                {{ mcpSecurity?.security?.enabled ? 'true' : 'false' }}
              </strong>
            </div>

            <div class="kv">
              <span>Default Action</span>
              <strong>{{ mcpSecurity?.security?.default_action || 'unknown' }}</strong>
            </div>

            <div class="kv">
              <span>Tool Policies</span>
              <strong>{{ mcpSecurity?.security?.tool_policies?.length || 0 }}</strong>
            </div>

            <div class="kv">
              <span>Detected Tools</span>
              <strong>{{ mcpToolNames.length }}</strong>
            </div>
          </div>

          <div class="mcp-policy-grid">
            <div class="mcp-policy-list">
              <div
                v-for="toolName in mcpToolNames"
                :key="toolName"
                class="mcp-policy-row"
                :class="{ selected: selectedMcpToolName === toolName }"
                @click="selectedMcpToolName = toolName"
              >
                <div>
                  <strong>{{ toolName }}</strong>
                  <span :class="statusClass(getPolicyAction(toolName))">
                    {{ getPolicyAction(toolName) }}
                  </span>
                </div>

                <div class="inline-actions">
                  <button class="small-btn" @click.stop="setToolPolicy(toolName, 'allow')">Allow</button>
                  <button class="small-btn" @click.stop="setToolPolicy(toolName, 'confirm')">Confirm</button>
                  <button class="danger-btn" @click.stop="setToolPolicy(toolName, 'deny')">Deny</button>
                  <button class="small-btn" @click.stop="removeToolPolicy(toolName)">Default</button>
                </div>
              </div>

              <p v-if="mcpToolNames.length === 0" class="muted">
                No MCP tools detected. Check MCP Servers or click Reload.
              </p>
            </div>

            <textarea
              v-model="mcpSecurityText"
              class="config-textarea mcp-security-editor"
              spellcheck="false"
            />
          </div>
        </div>

        <div class="panel mcp-snapshot-panel">
          <div class="panel-header">
            <div>
              <h3>MCP Tool Snapshots</h3>
              <p class="muted">
                Detect new or changed tool descriptors. Approve snapshots after review.
              </p>
            </div>

            <div class="inline-actions">
              <button class="primary-btn" :disabled="mcpSnapshotBusy || !selectedMcpSnapshot" @click="approveSelectedSnapshot">
                Approve Snapshot
              </button>
            </div>
          </div>

          <div class="mcp-snapshot-grid">
            <div class="mcp-snapshot-list">
              <button
                v-for="item in mcpToolSnapshots"
                :key="item.api_tool_name"
                class="mcp-snapshot-row"
                :class="{ selected: selectedMcpSnapshot?.api_tool_name === item.api_tool_name }"
                @click="selectedMcpSnapshot = item"
              >
                <strong>{{ item.api_tool_name }}</strong>
                <span :class="snapshotStatusClass(item.status)">
                  {{ item.status }}
                </span>
                <small>{{ item.server }} · {{ item.name }}</small>
              </button>

              <p v-if="mcpToolSnapshots.length === 0" class="muted">
                No snapshots yet. Refresh MCP tools first.
              </p>
            </div>

            <div class="mcp-snapshot-detail" v-if="selectedMcpSnapshot">
              <div class="kv">
                <span>Status</span>
                <strong :class="snapshotStatusClass(selectedMcpSnapshot.status)">
                  {{ selectedMcpSnapshot.status }}
                </strong>
              </div>

              <div class="kv">
                <span>Approved</span>
                <strong :class="selectedMcpSnapshot.approved ? 'ok' : 'warn'">
                  {{ selectedMcpSnapshot.approved ? 'true' : 'false' }}
                </strong>
              </div>

              <h3>Description</h3>
              <pre class="content-view small-view">{{ selectedMcpSnapshot.description }}</pre>

              <h3>Schema</h3>
              <pre class="content-view small-view">{{ formatJson(selectedMcpSnapshot.schema) }}</pre>

              <h3>Changes</h3>
              <pre class="content-view small-view">{{ formatJson(selectedMcpSnapshot.changes || []) }}</pre>
            </div>

            <div class="panel empty-state" v-else>
              <h3>Select a tool snapshot</h3>
            </div>
          </div>
        </div>

        <div class="mcp-grid">
          <div class="panel list-panel">
            <div class="panel-header">
              <h3>MCP Approvals</h3>
              <button class="small-btn" :disabled="mcpBusy" @click="refreshMcpPage">Refresh</button>
            </div>

            <div class="mcp-status-box">
              <div class="status-row">
                <span>API</span>
                <strong :class="mcpHealth?.status === 'ok' ? 'ok' : 'bad'">
                  {{ mcpHealth?.status || 'unknown' }}
                </strong>
              </div>

              <div class="status-row">
                <span>Security</span>
                <strong :class="mcpSecurity?.security?.enabled ? 'ok' : 'warn'">
                  {{ mcpSecurity?.security?.enabled ? 'Enabled' : 'Disabled' }}
                </strong>
              </div>

              <div class="status-row">
                <span>Default</span>
                <strong>{{ mcpSecurity?.security?.default_action || 'unknown' }}</strong>
              </div>

              <div class="status-row">
                <span>Pending</span>
                <strong :class="pendingApprovalCount > 0 ? 'warn' : 'ok'">
                  {{ pendingApprovalCount }}
                </strong>
              </div>
            </div>

            <div class="file-list">
              <button
                v-for="item in mcpApprovals"
                :key="item.id"
                :class="{ selected: selectedMcpApproval?.id === item.id }"
                @click="selectMcpApproval(item)"
              >
                <strong>{{ item.tool }}</strong>
                <span :class="statusClass(item.status)">
                  {{ item.status }} · {{ formatTime(item.createdAt) }}
                </span>
                <span>{{ item.id }}</span>
              </button>

              <p v-if="mcpApprovals.length === 0" class="muted">No MCP approvals.</p>
            </div>
          </div>

          <div class="panel detail-panel" v-if="selectedMcpApproval">
            <div class="panel-header">
              <div>
                <h3>{{ selectedMcpApproval.tool }}</h3>
                <p class="muted">{{ selectedMcpApproval.id }}</p>
              </div>

              <div class="inline-actions">
                <button
                  class="primary-btn"
                  :disabled="mcpBusy || selectedMcpApproval.status !== 'pending'"
                  @click="approveSelectedMcpCall"
                >
                  Approve
                </button>

                <button
                  class="danger-btn"
                  :disabled="mcpBusy || selectedMcpApproval.status !== 'pending'"
                  @click="denySelectedMcpCall"
                >
                  Deny
                </button>
              </div>
            </div>

            <div class="mcp-summary">
              <div class="kv">
                <span>Status</span>
                <strong :class="statusClass(selectedMcpApproval.status)">
                  {{ selectedMcpApproval.status }}
                </strong>
              </div>

              <div class="kv">
                <span>Action</span>
                <strong>{{ selectedMcpApproval.decision?.action }}</strong>
              </div>

              <div class="kv">
                <span>Created</span>
                <strong>{{ formatTime(selectedMcpApproval.createdAt) }}</strong>
              </div>

              <div class="kv">
                <span>Updated</span>
                <strong>{{ formatTime(selectedMcpApproval.updatedAt) }}</strong>
              </div>
            </div>

            <h3>Reason</h3>
            <pre class="content-view small-view">{{ selectedMcpApproval.decision?.reason }}</pre>

            <h3>Review</h3>
            <div class="mcp-review-grid" v-if="selectedMcpApproval.review">
              <div class="kv">
                <span>Server</span>
                <strong>{{ selectedMcpApproval.review.server }}</strong>
              </div>

              <div class="kv">
                <span>Risk</span>
                <strong :class="statusClass(selectedMcpApproval.review.risk?.risk)">
                  {{ selectedMcpApproval.review.risk?.risk }}
                </strong>
              </div>

              <div class="kv">
                <span>Snapshot</span>
                <strong :class="snapshotStatusClass(selectedMcpApproval.review.tool_snapshot?.status)">
                  {{ selectedMcpApproval.review.tool_snapshot?.status || 'unknown' }}
                </strong>
              </div>

              <div class="kv">
                <span>Policy</span>
                <strong>{{ selectedMcpApproval.review.matched_policy?.action || 'none' }}</strong>
              </div>
            </div>

            <h3 v-if="selectedMcpApproval.review">Server Profile</h3>
            <pre v-if="selectedMcpApproval.review" class="content-view small-view">
            {{ formatJson(selectedMcpApproval.review.server_profile) }}
            </pre>

            <h3 v-if="selectedMcpApproval.review">Affected Paths</h3>
            <pre v-if="selectedMcpApproval.review" class="content-view small-view">
            {{ formatJson(selectedMcpApproval.review.risk?.affected_paths || []) }}
            </pre>

            <h3>Arguments</h3>
            <pre class="content-view small-view">{{ formatJson(selectedMcpApproval.arguments) }}</pre>

            <h3 v-if="selectedMcpApproval.result">Result</h3>
            <pre v-if="selectedMcpApproval.result" class="content-view small-view">{{ selectedMcpApproval.result }}</pre>

            <h3 v-if="selectedMcpApproval.error">Error</h3>
            <pre v-if="selectedMcpApproval.error" class="content-view error-box">{{ selectedMcpApproval.error }}</pre>
          </div>

          <div class="panel empty-state" v-else>
            <h3>Select an MCP approval</h3>
            <p class="muted">
              write / edit / create / move tools that require confirmation will appear here.
            </p>
          </div>
        </div>

        <div class="panel mcp-audit-panel">
          <div class="panel-header">
            <h3>MCP Audit</h3>
            <button class="small-btn" :disabled="mcpBusy" @click="refreshMcpPage">Refresh</button>
          </div>

          <div class="audit-list">
            <div
              v-for="item in mcpAudit"
              :key="`${item.time}-${item.tool}-${item.status}-${formatJson(item.arguments)}`"
              class="audit-item"
            >
              <div class="audit-head">
                <strong>{{ item.tool }}</strong>
                <span :class="statusClass(item.status)">
                  {{ item.status }}
                </span>
              </div>

              <div class="muted">{{ item.time }}</div>

              <pre class="audit-preview">{{ shortText(item.error || item.resultPreview || formatJson(item.arguments), 700) }}</pre>
            </div>

            <p v-if="mcpAudit.length === 0" class="muted">No MCP audit records.</p>
          </div>
        </div>
      </section>

      <section v-if="page === 'logs'" class="page">
        <div class="panel">
          <div class="panel-header">
            <h3>Logs</h3>
            <button class="small-btn" @click="refreshLogs">Refresh</button>
          </div>

          <pre class="content-view">{{ logsText }}</pre>
        </div>
      </section>

      <section v-if="page === 'settings'" class="page">
        <div class="panel">
          <div class="panel-header">
            <h3>config.yaml</h3>
            <button class="primary-btn" :disabled="busy" @click="saveConfig">Save</button>
          </div>

          <textarea v-model="configText" class="config-textarea" />
        </div>
      </section>
    </main>

    <div v-if="toast" class="toast">
      {{ toast }}
    </div>
  </div>
</template>