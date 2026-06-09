import { app, BrowserWindow, ipcMain, shell } from 'electron'
import path from 'node:path'
import fs from 'node:fs/promises'
import fsSync from 'node:fs'
import { spawn } from 'node:child_process'

const PROJECT_ROOT = 'D:\\side_project\\auto_gpt'
const PYTHON_EXE = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
const MAIN_PY = path.join(PROJECT_ROOT, 'main.py')
const TASK_RUNNER_PY = path.join(PROJECT_ROOT, 'task_runner.py')

const TASKS_DIR = path.join(PROJECT_ROOT, 'tasks')
const TASKS_META_DIR = path.join(PROJECT_ROOT, 'tasks_meta')
const OUTPUTS_DIR = path.join(PROJECT_ROOT, 'outputs')
const RUNS_DIR = path.join(PROJECT_ROOT, 'runs')
const LOGS_DIR = path.join(PROJECT_ROOT, 'logs')
const SCREENSHOTS_DIR = path.join(PROJECT_ROOT, 'screenshots')

const CONFIG_PATH = path.join(PROJECT_ROOT, 'config.yaml')
const OUTPUT_PATH = path.join(PROJECT_ROOT, 'output.txt')
const LOCK_PATH = path.join(PROJECT_ROOT, '.runner.lock')
const API_BASE_URL = 'http://127.0.0.1:8788'

type RunResult = {
  code: number
  stdout: string
  stderr: string
  outputText: string
}

type TaskSchedule = {
  enabled: boolean
  type: 'manual' | 'once' | 'daily' | 'weekly'
  date: string
  time: string
  daysOfWeek: string[]
  repeat: boolean
}

type TaskMeta = {
  id: string
  title: string
  prompt: string
  enabled: boolean
  conversationMode: 'same' | 'new'
  schedule: TaskSchedule
  createdAt: string
  updatedAt: string
  lastRunAt: string
  lastStatus: string
  lastRunFile: string
  lastOutputPreview?: string
  lastError?: string
}

function ensureDirs() {
  for (const dir of [
    TASKS_DIR,
    TASKS_META_DIR,
    OUTPUTS_DIR,
    RUNS_DIR,
    LOGS_DIR,
    SCREENSHOTS_DIR
  ]) {
    if (!fsSync.existsSync(dir)) {
      fsSync.mkdirSync(dir, { recursive: true })
    }
  }
}

function nowIso() {
  return new Date().toISOString()
}

function safeId(text: string): string {
  const cleaned = text
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '_')
    .trim()

  return cleaned || `task_${Date.now()}`
}

function assertSafeFileName(filename: string, ext: string) {
  if (!filename.endsWith(ext)) throw new Error(`Only ${ext} files are allowed`)
  if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
    throw new Error('Invalid filename')
  }
}

function taskPathById(id: string) {
  return path.join(TASKS_META_DIR, `${safeId(id)}.json`)
}

function windowsDateFromIsoDate(date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return date
  const [, yyyy, mm, dd] = match
  return `${mm}/${dd}/${yyyy}`
}

function scheduleTaskName(taskId: string) {
  return `AutoGPT_${safeId(taskId)}`
}

async function readOutputText(): Promise<string> {
  try {
    return await fs.readFile(OUTPUT_PATH, 'utf-8')
  } catch {
    return ''
  }
}

function runProcess(exe: string, args: string[], cwd = PROJECT_ROOT): Promise<RunResult> {
  return new Promise((resolve) => {
    let stdout = ''
    let stderr = ''

    const child = spawn(exe, args, {
      cwd,
      windowsHide: false,
      shell: false
    })

    child.stdout.on('data', (data) => {
      stdout += data.toString()
    })

    child.stderr.on('data', (data) => {
      stderr += data.toString()
    })

    child.on('error', async (error) => {
      resolve({
        code: 1,
        stdout,
        stderr: `${stderr}\n${error.name}: ${error.message}`,
        outputText: await readOutputText()
      })
    })

    child.on('close', async (code) => {
      resolve({
        code: code ?? 0,
        stdout,
        stderr,
        outputText: await readOutputText()
      })
    })
  })
}

function runPython(args: string[]): Promise<RunResult> {
  if (!fsSync.existsSync(PYTHON_EXE)) {
    return Promise.resolve({
      code: 1,
      stdout: '',
      stderr: `Python executable not found: ${PYTHON_EXE}`,
      outputText: ''
    })
  }

  return runProcess(PYTHON_EXE, args)
}

async function listTextFiles(dir: string) {
  ensureDirs()

  const files = await fs.readdir(dir, { withFileTypes: true })
  const result = []

  for (const file of files) {
    if (!file.isFile()) continue
    if (!file.name.endsWith('.txt') && !file.name.endsWith('.md')) continue

    const fullPath = path.join(dir, file.name)
    const stat = await fs.stat(fullPath)

    result.push({
      name: file.name,
      size: stat.size,
      modifiedAt: stat.mtime.toISOString()
    })
  }

  result.sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt))
  return result
}

async function listJsonFiles(dir: string) {
  ensureDirs()

  const files = await fs.readdir(dir, { withFileTypes: true })
  const result = []

  for (const file of files) {
    if (!file.isFile()) continue
    if (!file.name.endsWith('.json')) continue

    const fullPath = path.join(dir, file.name)
    const stat = await fs.stat(fullPath)

    result.push({
      name: file.name,
      size: stat.size,
      modifiedAt: stat.mtime.toISOString()
    })
  }

  result.sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt))
  return result
}

async function readJsonFile(filePath: string) {
  return JSON.parse(await fs.readFile(filePath, 'utf-8'))
}

async function writeJsonFile(filePath: string, data: unknown) {
  await fs.writeFile(filePath, JSON.stringify(data, null, 2), 'utf-8')
}

function defaultTask(): TaskMeta {
  const id = `task_${Date.now()}`
  const now = nowIso()

  return {
    id,
    title: 'New Task',
    prompt: '',
    enabled: true,
    conversationMode: 'same',
    schedule: {
      enabled: false,
      type: 'manual',
      date: '',
      time: '09:00',
      daysOfWeek: [],
      repeat: false
    },
    createdAt: now,
    updatedAt: now,
    lastRunAt: '',
    lastStatus: 'never',
    lastRunFile: ''
  }
}

function getPreloadPath() {
  return path.join(app.getAppPath(), 'dist-electron', 'preload.js')
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 980,
    minHeight: 680,
    title: 'Auto GPT Local UI',
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: getPreloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  const devUrl = process.env.VITE_DEV_SERVER_URL

  if (devUrl) {
    win.loadURL(devUrl)
  } else {
    win.loadFile(path.join(app.getAppPath(), 'dist', 'index.html'))
  }
}

app.whenReady().then(() => {
  ensureDirs()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

ipcMain.handle('system:getStatus', async () => {
  return {
    projectRoot: PROJECT_ROOT,
    pythonExe: PYTHON_EXE,
    pythonExists: fsSync.existsSync(PYTHON_EXE),
    mainPy: MAIN_PY,
    mainPyExists: fsSync.existsSync(MAIN_PY),
    taskRunnerPy: TASK_RUNNER_PY,
    taskRunnerExists: fsSync.existsSync(TASK_RUNNER_PY),
    configPath: CONFIG_PATH,
    configExists: fsSync.existsSync(CONFIG_PATH),
    outputPath: OUTPUT_PATH,
    outputExists: fsSync.existsSync(OUTPUT_PATH),
    runnerLocked: fsSync.existsSync(LOCK_PATH)
  }
})

ipcMain.handle('runner:runPrompt', async (_event, prompt: string) => {
  if (!prompt || !prompt.trim()) throw new Error('Prompt is empty')
  return await runPython([MAIN_PY, '--prompt-text', prompt])
})

ipcMain.handle('runner:runBatch', async () => {
  return await runPython([MAIN_PY, '--batch'])
})

ipcMain.handle('runner:readOutput', async () => {
  return await readOutputText()
})

ipcMain.handle('tasks:list', async () => {
  return await listTextFiles(TASKS_DIR)
})

ipcMain.handle('tasks:read', async (_event, filename: string) => {
  assertSafeFileName(filename, '.txt')
  return await fs.readFile(path.join(TASKS_DIR, filename), 'utf-8')
})

ipcMain.handle('tasks:save', async (_event, filename: string, content: string) => {
  const safeName = safeId(filename.endsWith('.txt') ? filename : `${filename}.txt`)
  assertSafeFileName(safeName, '.txt')
  await fs.writeFile(path.join(TASKS_DIR, safeName), content ?? '', 'utf-8')
  return { name: safeName }
})

ipcMain.handle('tasks:delete', async (_event, filename: string) => {
  assertSafeFileName(filename, '.txt')
  await fs.unlink(path.join(TASKS_DIR, filename))
  return true
})

ipcMain.handle('taskMeta:new', async () => {
  return defaultTask()
})

ipcMain.handle('taskMeta:list', async () => {
  ensureDirs()
  const files = await listJsonFiles(TASKS_META_DIR)
  const tasks = []

  for (const file of files) {
    try {
      const data = await readJsonFile(path.join(TASKS_META_DIR, file.name))
      tasks.push({
        ...data,
        fileName: file.name,
        modifiedAt: file.modifiedAt
      })
    } catch {
      // ignore broken json
    }
  }

  tasks.sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))
  return tasks
})

ipcMain.handle('taskMeta:read', async (_event, id: string) => {
  const fullPath = taskPathById(id)
  return await readJsonFile(fullPath)
})

ipcMain.handle('taskMeta:save', async (_event, task: TaskMeta) => {
  const now = nowIso()
  const id = task.id || `task_${Date.now()}`

  const normalized: TaskMeta = {
    ...defaultTask(),
    ...task,
    id,
    updatedAt: now,
    createdAt: task.createdAt || now,
    schedule: {
      ...defaultTask().schedule,
      ...(task.schedule || {})
    }
  }

  const fullPath = taskPathById(id)
  await writeJsonFile(fullPath, normalized)

  return normalized
})

ipcMain.handle('taskMeta:delete', async (_event, id: string) => {
  const fullPath = taskPathById(id)
  if (fsSync.existsSync(fullPath)) await fs.unlink(fullPath)
  return true
})

ipcMain.handle('taskMeta:run', async (_event, id: string) => {
  const fullPath = taskPathById(id)
  if (!fsSync.existsSync(fullPath)) {
    return {
      code: 1,
      stdout: '',
      stderr: `Task JSON not found: ${fullPath}`,
      outputText: ''
    }
  }

  return await runPython([TASK_RUNNER_PY, '--task-json', fullPath])
})

ipcMain.handle('schedule:createOrUpdate', async (_event, task: TaskMeta) => {
  const savedTask = task
  const taskId = safeId(savedTask.id)
  const taskJsonPath = taskPathById(taskId)
  const taskName = scheduleTaskName(taskId)
  const schedule = savedTask.schedule

  if (!schedule.enabled || schedule.type === 'manual') {
    throw new Error('Schedule is not enabled')
  }

  await writeJsonFile(taskJsonPath, savedTask)

  const taskCommand = `"${PYTHON_EXE}" "${TASK_RUNNER_PY}" --task-json "${taskJsonPath}"`
  const args = [
    '/create',
    '/tn',
    taskName,
    '/tr',
    taskCommand,
    '/f'
  ]

  if (schedule.type === 'once') {
    if (!schedule.date) throw new Error('一次性排程需要日期')
    args.push('/sc', 'once', '/sd', windowsDateFromIsoDate(schedule.date), '/st', schedule.time || '09:00')
  } else if (schedule.type === 'daily') {
    args.push('/sc', 'daily', '/st', schedule.time || '09:00')
  } else if (schedule.type === 'weekly') {
    const days = schedule.daysOfWeek && schedule.daysOfWeek.length
      ? schedule.daysOfWeek.join(',')
      : 'MON'

    args.push('/sc', 'weekly', '/d', days, '/st', schedule.time || '09:00')
  } else {
    throw new Error(`Unsupported schedule type: ${schedule.type}`)
  }

  const result = await runProcess('schtasks', args)

  if (result.code !== 0) {
    throw new Error(result.stderr || result.stdout || '建立排程失敗')
  }

  return {
    taskName,
    stdout: result.stdout,
    stderr: result.stderr
  }
})

ipcMain.handle('schedule:delete', async (_event, taskId: string) => {
  const taskName = scheduleTaskName(taskId)
  const result = await runProcess('schtasks', ['/delete', '/tn', taskName, '/f'])

  return {
    code: result.code,
    taskName,
    stdout: result.stdout,
    stderr: result.stderr
  }
})

ipcMain.handle('schedule:runNow', async (_event, taskId: string) => {
  const taskName = scheduleTaskName(taskId)
  const result = await runProcess('schtasks', ['/run', '/tn', taskName])

  return {
    code: result.code,
    taskName,
    stdout: result.stdout,
    stderr: result.stderr
  }
})

ipcMain.handle('outputs:list', async () => {
  return await listTextFiles(OUTPUTS_DIR)
})

ipcMain.handle('outputs:read', async (_event, filename: string) => {
  if (
    !filename.endsWith('.txt') &&
    !filename.endsWith('.md')
  ) {
    throw new Error('Only .txt and .md files are allowed')
  }

  if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
    throw new Error('Invalid filename')
  }

  return await fs.readFile(path.join(OUTPUTS_DIR, filename), 'utf-8')
})

ipcMain.handle('runs:list', async () => {
  const files = await listJsonFiles(RUNS_DIR)
  const runs = []

  for (const file of files) {
    try {
      const data = await readJsonFile(path.join(RUNS_DIR, file.name))
      runs.push({
        ...data,
        fileName: file.name,
        modifiedAt: file.modifiedAt
      })
    } catch {
      // ignore
    }
  }

  runs.sort((a, b) => String(b.startedAt || '').localeCompare(String(a.startedAt || '')))
  return runs
})

ipcMain.handle('runs:read', async (_event, filename: string) => {
  assertSafeFileName(filename, '.json')
  return await readJsonFile(path.join(RUNS_DIR, filename))
})

ipcMain.handle('logs:read', async () => {
  const rootDir = path.resolve(app.getAppPath(), '..')
  const logsDir = path.join(rootDir, 'logs')

  const logFiles = [
    'mcp_audit.log',
    'mcp_pending.log',
    'mcp_errors.log',
    'mcp_stderr.log',
    'mcp_security.log',
    'mcp_stdout_invalid.log'
  ]

  const parts: string[] = []

  for (const file of logFiles) {
    const filePath = path.join(logsDir, file)

    try {
      const text = await fs.readFile(filePath, 'utf-8')
      const lines = text.split(/\r?\n/)
      const tail = lines.slice(-120).join('\n')

      parts.push(`===== ${file} =====\n${tail}`)
    } catch {
      parts.push(`===== ${file} =====\n<no log file>`)
    }
  }

  return parts.join('\n\n')
})

ipcMain.handle('config:read', async () => {
  try {
    return await fs.readFile(CONFIG_PATH, 'utf-8')
  } catch {
    return ''
  }
})

ipcMain.handle('config:save', async (_event, content: string) => {
  await fs.writeFile(CONFIG_PATH, content ?? '', 'utf-8')
  return true
})

ipcMain.handle('shell:openProjectFolder', async () => {
  await shell.openPath(PROJECT_ROOT)
  return true
})

ipcMain.handle('shell:openOutputsFolder', async () => {
  await shell.openPath(OUTPUTS_DIR)
  return true
})

ipcMain.handle('shell:openRunsFolder', async () => {
  await shell.openPath(RUNS_DIR)
  return true
})

async function apiJson(pathname: string, options: RequestInit = {}) {
  const url = `${API_BASE_URL}${pathname}`

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...(options.headers || {})
    }
  })

  const text = await response.text()

  let data: any = null

  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }

  if (!response.ok) {
    throw new Error(
      data?.detail ||
      data?.error?.message ||
      data?.message ||
      `API request failed: ${response.status} ${response.statusText}`
    )
  }

  return data
}

ipcMain.handle('api:health', async () => {
  return await apiJson('/health')
})

ipcMain.handle('mcp:security', async () => {
  return await apiJson('/v1/mcp/security')
})

ipcMain.handle('mcp:audit', async (_event, limit: number = 50) => {
  return await apiJson(`/v1/mcp/audit?limit=${encodeURIComponent(String(limit))}`)
})

ipcMain.handle('mcp:approvals:list', async () => {
  return await apiJson('/v1/mcp/approvals')
})

ipcMain.handle('mcp:approvals:approve', async (_event, pendingId: string) => {
  return await apiJson(`/v1/mcp/approvals/${encodeURIComponent(pendingId)}/approve`, {
    method: 'POST'
  })
})

ipcMain.handle('mcp:approvals:deny', async (_event, pendingId: string) => {
  return await apiJson(`/v1/mcp/approvals/${encodeURIComponent(pendingId)}/deny`, {
    method: 'POST'
  })
})

ipcMain.handle('mcp:config:get', async () => {
  return await apiJson('/v1/mcp/config')
})

ipcMain.handle('mcp:config:save', async (_event, config: any) => {
  return await apiJson('/v1/mcp/config', {
    method: 'PUT',
    body: JSON.stringify({ config })
  })
})

ipcMain.handle('mcp:servers:list', async () => {
  return await apiJson('/v1/mcp/servers')
})

ipcMain.handle('mcp:servers:reload', async () => {
  return await apiJson('/v1/mcp/reload', {
    method: 'POST'
  })
})

ipcMain.handle('mcp:security:save', async (_event, security: any) => {
  return await apiJson('/v1/mcp/security', {
    method: 'PUT',
    body: JSON.stringify({ security })
  })
})

ipcMain.handle('mcp:security:reload', async () => {
  return await apiJson('/v1/mcp/security/reload', {
    method: 'POST'
  })
})

ipcMain.handle('mcp:tools:list', async () => {
  return await apiJson('/v1/mcp/tools')
})

ipcMain.handle('mcp:tool-snapshots:list', async () => {
  return await apiJson('/v1/mcp/tool-snapshots')
})

ipcMain.handle('mcp:tool-snapshots:approve', async (_event, toolName: string) => {
  return await apiJson(`/v1/mcp/tool-snapshots/${encodeURIComponent(toolName)}/approve`, {
    method: 'POST'
  })
})