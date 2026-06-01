<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { FileItem, RunRecord, RunResult, SystemStatus, TaskMeta } from './types'

type Page = 'dashboard' | 'chat' | 'tasks' | 'outputs' | 'runs' | 'logs' | 'settings'

const page = ref<Page>('dashboard')
const busy = ref(false)
const status = ref<SystemStatus | null>(null)

const chatInput = ref('')
const chatMessages = ref<{ role: 'user' | 'assistant' | 'system' | 'error'; text: string }[]>([])
const lastRun = ref<RunResult | null>(null)

const tasksMeta = ref<TaskMeta[]>([])
const selectedTask = ref<TaskMeta | null>(null)

const outputs = ref<FileItem[]>([])
const selectedOutputName = ref('')
const selectedOutputContent = ref('')

const runs = ref<RunRecord[]>([])
const selectedRun = ref<RunRecord | null>(null)

const selectedLog = ref<'runner.log' | 'bat.log'>('runner.log')
const logContent = ref('')

const configContent = ref('')
const toast = ref('')

const weekDays = [
  { label: '一', value: 'MON' },
  { label: '二', value: 'TUE' },
  { label: '三', value: 'WED' },
  { label: '四', value: 'THU' },
  { label: '五', value: 'FRI' },
  { label: '六', value: 'SAT' },
  { label: '日', value: 'SUN' }
]

const pageTitle = computed(() => {
  const map: Record<Page, string> = {
    dashboard: 'Dashboard',
    chat: 'Chat',
    tasks: 'Tasks',
    outputs: 'Outputs',
    runs: 'Runs',
    logs: 'Logs',
    settings: 'Settings'
  }
  return map[page.value]
})

function bridge() {
  if (!window.autoGpt) {
    throw new Error('Electron bridge 未載入。請用 npm run dev 啟動 Electron，不要只用瀏覽器開 Vite。')
  }
  return window.autoGpt
}

function showToast(message: string) {
  toast.value = message
  setTimeout(() => {
    if (toast.value === message) toast.value = ''
  }, 3500)
}

function toPlainTask(task: TaskMeta): TaskMeta {
  return JSON.parse(JSON.stringify(task))
}

function formatTime(iso: string) {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

async function refreshStatus() {
  try {
    status.value = await bridge().getStatus()
  } catch (error: any) {
    console.error('refreshStatus failed:', error)
    showToast(`狀態讀取失敗：${error?.message ?? error}`)
  }
}

async function sendPrompt() {
  const prompt = chatInput.value.trim()
  if (!prompt || busy.value) return

  chatMessages.value.push({ role: 'user', text: prompt })
  chatInput.value = ''
  busy.value = true

  try {
    const result = await bridge().runPrompt(prompt)
    lastRun.value = result

    chatMessages.value.push({
      role: result.code === 0 ? 'assistant' : 'error',
      text:
        result.code === 0
          ? result.outputText || '(output.txt 沒有內容)'
          : result.stderr || result.stdout || result.outputText || '執行失敗'
    })

    await refreshOutputs()
    await refreshRuns()
    await refreshLogs()
  } catch (error: any) {
    console.error('sendPrompt failed:', error)
    chatMessages.value.push({
      role: 'error',
      text: `${error?.name ?? 'Error'}: ${error?.message ?? error}`
    })
  } finally {
    busy.value = false
    await refreshStatus()
  }
}

async function runBatch() {
  if (busy.value) return

  busy.value = true
  chatMessages.value.push({ role: 'system', text: '開始執行 tasks/ 批次任務...' })

  try {
    const result = await bridge().runBatch()
    lastRun.value = result
    chatMessages.value.push({
      role: result.code === 0 ? 'system' : 'error',
      text: result.stdout || result.stderr || result.outputText || '批次任務完成'
    })
    await refreshOutputs()
    await refreshRuns()
    await refreshLogs()
  } catch (error: any) {
    console.error('runBatch failed:', error)
    chatMessages.value.push({
      role: 'error',
      text: `${error?.name ?? 'Error'}: ${error?.message ?? error}`
    })
  } finally {
    busy.value = false
    await refreshStatus()
  }
}

async function refreshTasksMeta() {
  try {
    tasksMeta.value = await bridge().listTaskMeta()
  } catch (error: any) {
    console.error('refreshTasksMeta failed:', error)
    showToast(`Task 讀取失敗：${error?.message ?? error}`)
  }
}

async function createNewTask() {
  try {
    const task = await bridge().newTaskMeta()
    task.title = 'New Task'
    task.schedule.date = today()
    selectedTask.value = task
    showToast('已建立新 Task 草稿')
  } catch (error: any) {
    console.error('createNewTask failed:', error)
    showToast(`新增失敗：${error?.message ?? error}`)
  }
}

async function selectTask(task: TaskMeta) {
  const copied = JSON.parse(JSON.stringify(task)) as TaskMeta
  if (!copied.schedule.date) copied.schedule.date = today()
  if (!copied.schedule.daysOfWeek) copied.schedule.daysOfWeek = []
  selectedTask.value = copied
}

async function saveSelectedTask() {
  if (!selectedTask.value) {
    showToast('請先選擇或新增 Task')
    return
  }

  try {
    const plainTask = toPlainTask(selectedTask.value)

    if (!plainTask.id?.trim()) {
      showToast('Task ID 不可為空')
      return
    }

    if (!plainTask.title?.trim()) {
      showToast('Title 不可為空')
      return
    }

    if (!plainTask.prompt?.trim()) {
      showToast('Prompt 不可為空')
      return
    }

    plainTask.schedule = {
      enabled: plainTask.schedule?.enabled ?? false,
      type: plainTask.schedule?.type ?? 'manual',
      date: plainTask.schedule?.date ?? '',
      time: plainTask.schedule?.time ?? '09:00',
      daysOfWeek: plainTask.schedule?.daysOfWeek ?? [],
      repeat: plainTask.schedule?.repeat ?? false
    }

    selectedTask.value = await bridge().saveTaskMeta(plainTask)

    showToast('Task 已儲存')
    await refreshTasksMeta()
  } catch (error: any) {
    console.error('saveSelectedTask failed:', error)
    showToast(`儲存失敗：${error?.message ?? error}`)
  }
}

async function deleteSelectedTask() {
  if (!selectedTask.value) return
  if (!confirm(`確定刪除 ${selectedTask.value.title}？`)) return

  try {
    await bridge().deleteTaskMeta(selectedTask.value.id)
    showToast('Task 已刪除')
    selectedTask.value = null
    await refreshTasksMeta()
  } catch (error: any) {
    console.error('deleteSelectedTask failed:', error)
    showToast(`刪除失敗：${error?.message ?? error}`)
  }
}

async function runSelectedTask() {
  if (!selectedTask.value || busy.value) return

  try {
    await saveSelectedTask()
    if (!selectedTask.value) return

    busy.value = true

    const plainTask = toPlainTask(selectedTask.value)
    const result = await bridge().runTaskMeta(plainTask.id)

    lastRun.value = result

    if (result.code === 0) {
      showToast('Task 執行成功')
    } else {
      console.error('Task run failed:', result.stderr || result.stdout)
      showToast(`Task 執行失敗：${result.stderr || result.stdout || 'unknown error'}`)
    }

    await refreshTasksMeta()
    await refreshRuns()
    await refreshOutputs()
  } catch (error: any) {
    console.error('runSelectedTask failed:', error)
    showToast(`執行失敗：${error?.message ?? error}`)
  } finally {
    busy.value = false
    await refreshStatus()
  }
}

async function createOrUpdateSchedule() {
  if (!selectedTask.value) return

  try {
    await saveSelectedTask()
    if (!selectedTask.value) return

    const plainTask = toPlainTask(selectedTask.value)

    if (!plainTask.schedule.enabled || plainTask.schedule.type === 'manual') {
      showToast('請先啟用排程，並選擇 once / daily / weekly')
      return
    }

    if (!plainTask.schedule.time) {
      showToast('請填寫排程時間')
      return
    }

    if (plainTask.schedule.type === 'once' && !plainTask.schedule.date) {
      showToast('一次性排程需要日期')
      return
    }

    if (plainTask.schedule.type === 'weekly' && (!plainTask.schedule.daysOfWeek || plainTask.schedule.daysOfWeek.length === 0)) {
      showToast('每週排程至少要選一天')
      return
    }

    const result = await bridge().createOrUpdateSchedule(plainTask)

    showToast(`已建立/更新排程：${result.taskName}`)
  } catch (error: any) {
    console.error('createOrUpdateSchedule failed:', error)
    showToast(`建立排程失敗：${error?.message ?? error}`)
  }
}

async function deleteSchedule() {
  if (!selectedTask.value) return

  try {
    const plainTask = toPlainTask(selectedTask.value)
    const result = await bridge().deleteSchedule(plainTask.id)

    if (result.code === 0) {
      showToast(`已刪除排程：${result.taskName}`)
    } else {
      showToast(result.stderr || result.stdout || '刪除排程失敗')
    }
  } catch (error: any) {
    console.error('deleteSchedule failed:', error)
    showToast(`刪除排程失敗：${error?.message ?? error}`)
  }
}

function toggleWeekday(value: string) {
  if (!selectedTask.value) return

  const days = selectedTask.value.schedule.daysOfWeek || []

  if (days.includes(value)) {
    selectedTask.value.schedule.daysOfWeek = days.filter((x) => x !== value)
  } else {
    selectedTask.value.schedule.daysOfWeek = [...days, value]
  }
}

async function refreshOutputs() {
  try {
    outputs.value = await bridge().listOutputs()
  } catch (error: any) {
    console.error('refreshOutputs failed:', error)
    showToast(`Outputs 讀取失敗：${error?.message ?? error}`)
  }
}

async function loadOutput(name: string) {
  try {
    selectedOutputName.value = name
    selectedOutputContent.value = await bridge().readOutputFile(name)
  } catch (error: any) {
    console.error('loadOutput failed:', error)
    showToast(`Output 讀取失敗：${error?.message ?? error}`)
  }
}

async function loadLatestOutput() {
  try {
    selectedOutputName.value = 'output.txt'
    selectedOutputContent.value = await bridge().readOutput()
  } catch (error: any) {
    console.error('loadLatestOutput failed:', error)
    showToast(`output.txt 讀取失敗：${error?.message ?? error}`)
  }
}

async function refreshRuns() {
  try {
    runs.value = await bridge().listRuns()
  } catch (error: any) {
    console.error('refreshRuns failed:', error)
    showToast(`Runs 讀取失敗：${error?.message ?? error}`)
  }
}

async function selectRun(run: RunRecord) {
  try {
    if (!run.fileName) return
    selectedRun.value = await bridge().readRun(run.fileName)
  } catch (error: any) {
    console.error('selectRun failed:', error)
    showToast(`Run 讀取失敗：${error?.message ?? error}`)
  }
}

async function refreshLogs() {
  try {
    logContent.value = await bridge().readLog(selectedLog.value)
  } catch (error: any) {
    console.error('refreshLogs failed:', error)
    showToast(`Log 讀取失敗：${error?.message ?? error}`)
  }
}

async function refreshConfig() {
  try {
    configContent.value = await bridge().readConfig()
  } catch (error: any) {
    console.error('refreshConfig failed:', error)
    showToast(`Config 讀取失敗：${error?.message ?? error}`)
  }
}

async function saveConfig() {
  try {
    await bridge().saveConfig(configContent.value)
    showToast('config.yaml 已儲存')
  } catch (error: any) {
    console.error('saveConfig failed:', error)
    showToast(`Config 儲存失敗：${error?.message ?? error}`)
  }
}

async function openProjectFolder() {
  await bridge().openProjectFolder()
}

async function openOutputsFolder() {
  await bridge().openOutputsFolder()
}

async function openRunsFolder() {
  await bridge().openRunsFolder()
}

async function switchPage(next: Page) {
  page.value = next
  if (next === 'dashboard') {
    await refreshStatus()
    await refreshTasksMeta()
    await refreshRuns()
  }
  if (next === 'tasks') await refreshTasksMeta()
  if (next === 'outputs') await refreshOutputs()
  if (next === 'runs') await refreshRuns()
  if (next === 'logs') await refreshLogs()
  if (next === 'settings') await refreshConfig()
}

onMounted(async () => {
  await refreshStatus()
  await refreshTasksMeta()
  await refreshOutputs()
  await refreshRuns()
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A</div>
        <div>
          <div class="brand-title">Auto GPT</div>
          <div class="brand-subtitle">Local UI</div>
        </div>
      </div>

      <nav class="nav">
        <button :class="{ active: page === 'dashboard' }" @click="switchPage('dashboard')">Dashboard</button>
        <button :class="{ active: page === 'chat' }" @click="switchPage('chat')">Chat</button>
        <button :class="{ active: page === 'tasks' }" @click="switchPage('tasks')">Tasks</button>
        <button :class="{ active: page === 'outputs' }" @click="switchPage('outputs')">Outputs</button>
        <button :class="{ active: page === 'runs' }" @click="switchPage('runs')">Runs</button>
        <button :class="{ active: page === 'logs' }" @click="switchPage('logs')">Logs</button>
        <button :class="{ active: page === 'settings' }" @click="switchPage('settings')">Settings</button>
      </nav>

      <div class="status-card">
        <div class="status-row">
          <span>Python</span>
          <strong :class="status?.pythonExists ? 'ok' : 'bad'">{{ status?.pythonExists ? 'OK' : 'Missing' }}</strong>
        </div>
        <div class="status-row">
          <span>main.py</span>
          <strong :class="status?.mainPyExists ? 'ok' : 'bad'">{{ status?.mainPyExists ? 'OK' : 'Missing' }}</strong>
        </div>
        <div class="status-row">
          <span>task_runner</span>
          <strong :class="status?.taskRunnerExists ? 'ok' : 'bad'">{{ status?.taskRunnerExists ? 'OK' : 'Missing' }}</strong>
        </div>
        <div class="status-row">
          <span>Runner</span>
          <strong :class="status?.runnerLocked ? 'warn' : 'ok'">{{ status?.runnerLocked ? 'Busy' : 'Idle' }}</strong>
        </div>
        <button class="small-btn" @click="refreshStatus">Refresh</button>
      </div>

      <button class="ghost-btn" @click="openProjectFolder">Open Folder</button>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h1>{{ pageTitle }}</h1>
          <p>D:\side_project\auto_gpt</p>
        </div>
        <div v-if="busy" class="running-pill">Running...</div>
      </header>

      <section v-if="page === 'dashboard'" class="page">
        <div class="dashboard-grid">
          <div class="panel">
            <h2>System</h2>
            <div class="kv"><span>Project</span><strong>{{ status?.projectRoot }}</strong></div>
            <div class="kv"><span>Python</span><strong>{{ status?.pythonExists ? 'OK' : 'Missing' }}</strong></div>
            <div class="kv"><span>main.py</span><strong>{{ status?.mainPyExists ? 'OK' : 'Missing' }}</strong></div>
            <div class="kv"><span>task_runner.py</span><strong>{{ status?.taskRunnerExists ? 'OK' : 'Missing' }}</strong></div>
            <div class="kv"><span>Runner</span><strong>{{ status?.runnerLocked ? 'Busy' : 'Idle' }}</strong></div>
          </div>

          <div class="panel">
            <h2>Tasks</h2>
            <div class="big-number">{{ tasksMeta.length }}</div>
            <p class="muted">JSON tasks in tasks_meta/</p>
            <button class="primary-btn" @click="switchPage('tasks')">Manage Tasks</button>
          </div>

          <div class="panel">
            <h2>Runs</h2>
            <div class="big-number">{{ runs.length }}</div>
            <p class="muted">Execution records</p>
            <button class="secondary-btn" @click="switchPage('runs')">View Runs</button>
          </div>
        </div>
      </section>

      <section v-if="page === 'chat'" class="page chat-page">
        <div class="chat-window">
          <div v-if="chatMessages.length === 0" class="empty-state">
            <h2>開始本機 ChatGPT 自動化</h2>
            <p>輸入 prompt 後，會呼叫 main.py，透過 Edge CDP 操作 ChatGPT Web UI。</p>
          </div>

          <div v-for="(msg, index) in chatMessages" :key="index" class="message" :class="msg.role">
            <div class="message-role">{{ msg.role }}</div>
            <pre>{{ msg.text }}</pre>
          </div>
        </div>

        <div class="composer">
          <textarea
            v-model="chatInput"
            placeholder="輸入訊息..."
            :disabled="busy"
            @keydown.ctrl.enter.prevent="sendPrompt"
          />
          <div class="composer-actions">
            <button class="secondary-btn" :disabled="busy" @click="runBatch">Run tasks/</button>
            <button class="primary-btn" :disabled="busy || !chatInput.trim()" @click="sendPrompt">Send</button>
          </div>
        </div>
      </section>

      <section v-if="page === 'tasks'" class="page split-page">
        <div class="panel list-panel">
          <div class="panel-header">
            <h2>Tasks</h2>
            <button class="small-btn" @click="createNewTask">New</button>
          </div>

          <div class="file-list">
            <button
              v-for="task in tasksMeta"
              :key="task.id"
              :class="{ selected: selectedTask?.id === task.id }"
              @click="selectTask(task)"
            >
              <strong>{{ task.title }}</strong>
              <span>{{ task.schedule?.enabled ? `${task.schedule.type} ${task.schedule.time}` : 'manual' }}</span>
              <span>Status: {{ task.lastStatus || 'never' }}</span>
            </button>

            <div v-if="tasksMeta.length === 0" class="muted">尚無 JSON task</div>
          </div>
        </div>

        <div class="panel editor-panel" v-if="selectedTask">
          <div class="form-grid">
            <div class="form-row">
              <label>Title</label>
              <input v-model="selectedTask.title" />
            </div>

            <div class="form-row">
              <label>ID</label>
              <input v-model="selectedTask.id" />
            </div>

            <div class="form-row">
              <label>Conversation</label>
              <select v-model="selectedTask.conversationMode">
                <option value="same">沿用目前對話</option>
                <option value="new">新對話</option>
              </select>
            </div>

            <div class="form-row">
              <label>Enabled</label>
              <select v-model="selectedTask.enabled">
                <option :value="true">啟用</option>
                <option :value="false">停用</option>
              </select>
            </div>
          </div>

          <textarea
            v-model="selectedTask.prompt"
            class="large-editor"
            placeholder="任務 prompt，可使用 {{date}} {{time}} {{datetime}}"
          />

          <div class="schedule-box">
            <h3>Schedule</h3>

            <div class="form-grid">
              <div class="form-row">
                <label>啟用排程</label>
                <select v-model="selectedTask.schedule.enabled">
                  <option :value="false">否</option>
                  <option :value="true">是</option>
                </select>
              </div>

              <div class="form-row">
                <label>類型</label>
                <select v-model="selectedTask.schedule.type">
                  <option value="manual">手動</option>
                  <option value="once">一次性</option>
                  <option value="daily">每日</option>
                  <option value="weekly">每週</option>
                </select>
              </div>

              <div class="form-row" v-if="selectedTask.schedule.type === 'once'">
                <label>日期</label>
                <input v-model="selectedTask.schedule.date" type="date" />
              </div>

              <div class="form-row">
                <label>時間</label>
                <input v-model="selectedTask.schedule.time" type="time" />
              </div>
            </div>

            <div v-if="selectedTask.schedule.type === 'weekly'" class="weekday-row">
              <button
                v-for="d in weekDays"
                :key="d.value"
                :class="{ active: selectedTask.schedule.daysOfWeek?.includes(d.value) }"
                @click="toggleWeekday(d.value)"
              >
                {{ d.label }}
              </button>
            </div>
          </div>

          <div class="actions-row">
            <button class="primary-btn" @click="saveSelectedTask">Save</button>
            <button class="secondary-btn" :disabled="busy" @click="runSelectedTask">Run Now</button>
            <button class="secondary-btn" @click="createOrUpdateSchedule">Create / Update Schedule</button>
            <button class="danger-btn" @click="deleteSchedule">Delete Schedule</button>
            <button class="danger-btn" @click="deleteSelectedTask">Delete Task</button>
          </div>

          <div class="meta-box">
            <div>Last run: {{ formatTime(selectedTask.lastRunAt) }}</div>
            <div>Status: {{ selectedTask.lastStatus }}</div>
            <div v-if="selectedTask.lastRunFile">Run file: {{ selectedTask.lastRunFile }}</div>
          </div>
        </div>

        <div class="panel empty-state" v-else>
          <h2>選擇或新增一個 task</h2>
        </div>
      </section>

      <section v-if="page === 'outputs'" class="page split-page">
        <div class="panel list-panel">
          <div class="panel-header">
            <h2>Outputs</h2>
            <button class="small-btn" @click="refreshOutputs">Refresh</button>
          </div>

          <button class="wide-btn" @click="loadLatestOutput">Load output.txt</button>
          <button class="wide-btn" @click="openOutputsFolder">Open outputs/</button>

          <div class="file-list">
            <button
              v-for="item in outputs"
              :key="item.name"
              :class="{ selected: selectedOutputName === item.name }"
              @click="loadOutput(item.name)"
            >
              <strong>{{ item.name }}</strong>
              <span>{{ formatTime(item.modifiedAt) }}</span>
            </button>
          </div>
        </div>

        <div class="panel output-panel">
          <h2>{{ selectedOutputName || '選擇一個 output' }}</h2>
          <pre class="content-view">{{ selectedOutputContent }}</pre>
        </div>
      </section>

      <section v-if="page === 'runs'" class="page split-page">
        <div class="panel list-panel">
          <div class="panel-header">
            <h2>Runs</h2>
            <button class="small-btn" @click="refreshRuns">Refresh</button>
          </div>

          <button class="wide-btn" @click="openRunsFolder">Open runs/</button>

          <div class="file-list">
            <button
              v-for="run in runs"
              :key="run.id"
              :class="{ selected: selectedRun?.id === run.id }"
              @click="selectRun(run)"
            >
              <strong>{{ run.taskTitle || run.id }}</strong>
              <span>{{ run.status }} · {{ formatTime(run.startedAt) }}</span>
            </button>
          </div>
        </div>

        <div class="panel output-panel" v-if="selectedRun">
          <h2>{{ selectedRun.taskTitle || selectedRun.id }}</h2>
          <div class="kv"><span>Status</span><strong>{{ selectedRun.status }}</strong></div>
          <div class="kv"><span>Started</span><strong>{{ formatTime(selectedRun.startedAt) }}</strong></div>
          <div class="kv"><span>Finished</span><strong>{{ formatTime(selectedRun.finishedAt) }}</strong></div>

          <h3>Output</h3>
          <pre class="content-view">{{ selectedRun.output }}</pre>

          <h3 v-if="selectedRun.stderr">stderr</h3>
          <pre v-if="selectedRun.stderr" class="content-view">{{ selectedRun.stderr }}</pre>
        </div>

        <div class="panel empty-state" v-else>
          <h2>選擇一筆 run</h2>
        </div>
      </section>

      <section v-if="page === 'logs'" class="page">
        <div class="panel">
          <div class="panel-header">
            <h2>Logs</h2>
            <div class="inline-actions">
              <select v-model="selectedLog" @change="refreshLogs">
                <option value="runner.log">runner.log</option>
                <option value="bat.log">bat.log</option>
              </select>
              <button class="small-btn" @click="refreshLogs">Refresh</button>
            </div>
          </div>
          <pre class="log-view">{{ logContent }}</pre>
        </div>
      </section>

      <section v-if="page === 'settings'" class="page">
        <div class="panel">
          <div class="panel-header">
            <h2>config.yaml</h2>
            <div class="inline-actions">
              <button class="small-btn" @click="refreshConfig">Reload</button>
              <button class="primary-btn" @click="saveConfig">Save</button>
            </div>
          </div>
          <textarea v-model="configContent" class="config-editor" spellcheck="false" />
        </div>
      </section>
    </main>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>