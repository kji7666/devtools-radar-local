export {}

export type FileItem = {
  name: string
  size: number
  modifiedAt: string
}

export type RunResult = {
  code: number
  stdout: string
  stderr: string
  outputText: string
}

export type SystemStatus = {
  projectRoot: string
  pythonExe: string
  pythonExists: boolean
  mainPy: string
  mainPyExists: boolean
  taskRunnerPy: string
  taskRunnerExists: boolean
  configPath: string
  configExists: boolean
  outputPath: string
  outputExists: boolean
  runnerLocked: boolean
}

export type TaskSchedule = {
  enabled: boolean
  type: 'manual' | 'once' | 'daily' | 'weekly'
  date: string
  time: string
  daysOfWeek: string[]
  repeat: boolean
}

export type TaskMeta = {
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
  fileName?: string
  modifiedAt?: string
}

export type RunRecord = {
  id: string
  taskId: string
  taskTitle: string
  taskPath: string
  status: string
  startedAt: string
  finishedAt: string
  exitCode: number
  stdout: string
  stderr: string
  output: string
  fileName?: string
  modifiedAt?: string
}

declare global {
  interface Window {
    autoGpt?: {
      ping: () => string

      getStatus: () => Promise<SystemStatus>

      runPrompt: (prompt: string) => Promise<RunResult>
      runBatch: () => Promise<RunResult>
      readOutput: () => Promise<string>

      listTasks: () => Promise<FileItem[]>
      readTask: (filename: string) => Promise<string>
      saveTask: (filename: string, content: string) => Promise<{ name: string }>
      deleteTask: (filename: string) => Promise<boolean>

      newTaskMeta: () => Promise<TaskMeta>
      listTaskMeta: () => Promise<TaskMeta[]>
      readTaskMeta: (id: string) => Promise<TaskMeta>
      saveTaskMeta: (task: TaskMeta) => Promise<TaskMeta>
      deleteTaskMeta: (id: string) => Promise<boolean>
      runTaskMeta: (id: string) => Promise<RunResult>

      createOrUpdateSchedule: (task: TaskMeta) => Promise<{ taskName: string; stdout: string; stderr: string }>
      deleteSchedule: (taskId: string) => Promise<{ code: number; taskName: string; stdout: string; stderr: string }>
      runScheduledTaskNow: (taskId: string) => Promise<{ code: number; taskName: string; stdout: string; stderr: string }>

      listOutputs: () => Promise<FileItem[]>
      readOutputFile: (filename: string) => Promise<string>

      listRuns: () => Promise<RunRecord[]>
      readRun: (filename: string) => Promise<RunRecord>

      readLog: (name: string) => Promise<string>

      readConfig: () => Promise<string>
      saveConfig: (content: string) => Promise<boolean>

      openProjectFolder: () => Promise<boolean>
      openOutputsFolder: () => Promise<boolean>
      openRunsFolder: () => Promise<boolean>
      getMcpConfig: () => Promise<any>
      saveMcpConfig: (config: any) => Promise<any>
      listMcpServers: () => Promise<any>
      reloadMcpServers: () => Promise<any>
      saveMcpSecurity: (security: any) => Promise<any>
      reloadMcpSecurity: () => Promise<any>
      getMcpTools: () => Promise<any>
      readLogs: () => Promise<string>
      listMcpToolSnapshots: () => Promise<{ object: string; data: McpToolSnapshot[]; count: number }>
      approveMcpToolSnapshot: (toolName: string) => Promise<any>
    }
  }
}

export type McpApproval = {
  id: string
  tool: string
  arguments: Record<string, any>
  decision: {
    allowed: boolean
    action: string
    reason: string
    requires_confirmation: boolean
  }
  status: string
  createdAt: string
  updatedAt: string
  result: string
  error: string
}

export type McpAuditRecord = {
  time: string
  tool: string
  arguments: Record<string, any>
  decision: any
  status: string
  resultPreview: string
  error: string
}

declare global {
  interface Window {
    autoGpt: {
      [key: string]: any

      apiHealth: () => Promise<any>

      getMcpSecurity: () => Promise<any>
      getMcpAudit: (limit?: number) => Promise<{
        object: string
        data: McpAuditRecord[]
        count: number
      }>
      listMcpApprovals: () => Promise<{
        object: string
        data: McpApproval[]
        count: number
      }>
      approveMcpCall: (pendingId: string) => Promise<any>
      denyMcpCall: (pendingId: string) => Promise<any>
    }
  }
}

export type McpToolSnapshot = {
  api_tool_name: string
  server: string
  name: string
  description: string
  description_hash: string
  schema_hash: string
  schema: Record<string, any>
  status: string
  first_seen: string
  last_seen: string
  approved: boolean
  approved_at: string
  changes: any[]
}

export type McpApproval = {
  id: string
  tool: string
  arguments: Record<string, any>
  decision: {
    allowed: boolean
    action: string
    reason: string
    requires_confirmation: boolean
  }
  review?: any
  status: string
  createdAt: string
  updatedAt: string
  result: string
  error: string
}

export {}