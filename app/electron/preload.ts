import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('autoGpt', {
  ping: () => 'pong',

  getStatus: () => ipcRenderer.invoke('system:getStatus'),

  runPrompt: (prompt: string) => ipcRenderer.invoke('runner:runPrompt', prompt),
  runBatch: () => ipcRenderer.invoke('runner:runBatch'),
  readOutput: () => ipcRenderer.invoke('runner:readOutput'),

  listTasks: () => ipcRenderer.invoke('tasks:list'),
  readTask: (filename: string) => ipcRenderer.invoke('tasks:read', filename),
  saveTask: (filename: string, content: string) => ipcRenderer.invoke('tasks:save', filename, content),
  deleteTask: (filename: string) => ipcRenderer.invoke('tasks:delete', filename),

  newTaskMeta: () => ipcRenderer.invoke('taskMeta:new'),
  listTaskMeta: () => ipcRenderer.invoke('taskMeta:list'),
  readTaskMeta: (id: string) => ipcRenderer.invoke('taskMeta:read', id),
  saveTaskMeta: (task: any) => ipcRenderer.invoke('taskMeta:save', task),
  deleteTaskMeta: (id: string) => ipcRenderer.invoke('taskMeta:delete', id),
  runTaskMeta: (id: string) => ipcRenderer.invoke('taskMeta:run', id),

  createOrUpdateSchedule: (task: any) => ipcRenderer.invoke('schedule:createOrUpdate', task),
  deleteSchedule: (taskId: string) => ipcRenderer.invoke('schedule:delete', taskId),
  runScheduledTaskNow: (taskId: string) => ipcRenderer.invoke('schedule:runNow', taskId),

  listOutputs: () => ipcRenderer.invoke('outputs:list'),
  readOutputFile: (filename: string) => ipcRenderer.invoke('outputs:read'),

  listRuns: () => ipcRenderer.invoke('runs:list'),
  readRun: (filename: string) => ipcRenderer.invoke('runs:read', filename),

  readLog: (name: string) => ipcRenderer.invoke('logs:read', name),

  readConfig: () => ipcRenderer.invoke('config:read'),
  saveConfig: (content: string) => ipcRenderer.invoke('config:save', content),

  openProjectFolder: () => ipcRenderer.invoke('shell:openProjectFolder'),
  openOutputsFolder: () => ipcRenderer.invoke('shell:openOutputsFolder'),
  openRunsFolder: () => ipcRenderer.invoke('shell:openRunsFolder'),
  apiHealth: () => ipcRenderer.invoke('api:health'),

  getMcpSecurity: () => ipcRenderer.invoke('mcp:security'),
  getMcpAudit: (limit?: number) => ipcRenderer.invoke('mcp:audit', limit),
  listMcpApprovals: () => ipcRenderer.invoke('mcp:approvals:list'),
  approveMcpCall: (pendingId: string) => ipcRenderer.invoke('mcp:approvals:approve', pendingId),
  denyMcpCall: (pendingId: string) => ipcRenderer.invoke('mcp:approvals:deny', pendingId),
  getMcpConfig: () => ipcRenderer.invoke('mcp:config:get'),
  saveMcpConfig: (config: any) => ipcRenderer.invoke('mcp:config:save', config),
  listMcpServers: () => ipcRenderer.invoke('mcp:servers:list'),
  reloadMcpServers: () => ipcRenderer.invoke('mcp:servers:reload'),
})