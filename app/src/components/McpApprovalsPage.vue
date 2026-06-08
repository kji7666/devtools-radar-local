<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { McpApproval, McpAuditRecord } from '../types'

const approvals = ref<McpApproval[]>([])
const audit = ref<McpAuditRecord[]>([])
const security = ref<any>(null)
const health = ref<any>(null)

const selectedApproval = ref<McpApproval | null>(null)
const busy = ref(false)
const errorMessage = ref('')
const toast = ref('')

const pendingCount = computed(() => {
  return approvals.value.filter((item) => item.status === 'pending').length
})

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
  }, 2600)
}

function formatJson(value: any) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function shortText(text: string, max = 500) {
  if (!text) return ''
  if (text.length <= max) return text
  return `${text.slice(0, max)}\n\n... truncated ${text.length - max} chars`
}

function formatTime(value: string) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function statusClass(status: string) {
  const s = String(status || '').toLowerCase()

  if (s.includes('success') || s.includes('approved_executed')) return 'ok'
  if (s.includes('blocked') || s.includes('error') || s.includes('denied')) return 'bad'
  if (s.includes('pending') || s.includes('timeout')) return 'warn'

  return ''
}

async function refresh() {
  try {
    busy.value = true
    errorMessage.value = ''

    const [healthResult, securityResult, approvalResult, auditResult] = await Promise.all([
      bridge().apiHealth(),
      bridge().getMcpSecurity(),
      bridge().listMcpApprovals(),
      bridge().getMcpAudit(100)
    ])

    health.value = healthResult
    security.value = securityResult
    approvals.value = approvalResult?.data || []
    audit.value = auditResult?.data || []

    if (selectedApproval.value) {
      const updated = approvals.value.find((item) => item.id === selectedApproval.value?.id)
      selectedApproval.value = updated ? JSON.parse(JSON.stringify(updated)) : null
    }
  } catch (error: any) {
    errorMessage.value = error?.message || String(error)
    showToast(`MCP 讀取失敗：${errorMessage.value}`)
  } finally {
    busy.value = false
  }
}

function selectApproval(item: McpApproval) {
  selectedApproval.value = JSON.parse(JSON.stringify(item))
}

async function approveSelected() {
  if (!selectedApproval.value) {
    showToast('請先選擇一筆 approval')
    return
  }

  if (selectedApproval.value.status !== 'pending') {
    showToast('這筆 approval 不是 pending 狀態')
    return
  }

  const pendingId = selectedApproval.value.id

  const ok = window.confirm(
    `確定要 approve 這個 MCP tool call？\n\n` +
      `ID: ${pendingId}\n` +
      `Tool: ${selectedApproval.value.tool}\n\n` +
      `Approve 後會真的執行工具。`
  )

  if (!ok) return

  try {
    busy.value = true
    errorMessage.value = ''

    const result = await bridge().approveMcpCall(pendingId)

    showToast(`已 approve：${pendingId}`)

    if (result?.approval) {
      selectedApproval.value = {
        ...selectedApproval.value,
        status: result.approval.status ?? 'approved_executed',
        result: result.approval.result ?? '',
        error: result.approval.error ?? '',
        updatedAt: new Date().toISOString()
      }
    }

    await refresh()
  } catch (error: any) {
    errorMessage.value = error?.message || String(error)
    showToast(`Approve 失敗：${errorMessage.value}`)
  } finally {
    busy.value = false
  }
}

async function denySelected() {
  if (!selectedApproval.value) {
    showToast('請先選擇一筆 approval')
    return
  }

  if (selectedApproval.value.status !== 'pending') {
    showToast('這筆 approval 不是 pending 狀態')
    return
  }

  const pendingId = selectedApproval.value.id

  const ok = window.confirm(
    `確定要 deny 這個 MCP tool call？\n\n` +
      `ID: ${pendingId}\n` +
      `Tool: ${selectedApproval.value.tool}`
  )

  if (!ok) return

  try {
    busy.value = true
    errorMessage.value = ''

    await bridge().denyMcpCall(pendingId)

    showToast(`已 deny：${pendingId}`)
    await refresh()
  } catch (error: any) {
    errorMessage.value = error?.message || String(error)
    showToast(`Deny 失敗：${errorMessage.value}`)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await refresh()
})
</script>

<template>
  <section class="mcp-page">
    <div v-if="toast" class="mcp-toast">
      {{ toast }}
    </div>

    <div class="mcp-topbar">
      <div>
        <h1>MCP Approvals</h1>
        <p>管理需要人工確認的 MCP tool calls。</p>
      </div>

      <button class="mcp-btn" :disabled="busy" @click="refresh">
        {{ busy ? 'Loading...' : 'Refresh' }}
      </button>
    </div>

    <div v-if="errorMessage" class="mcp-error">
      {{ errorMessage }}
    </div>

    <div class="mcp-status-grid">
      <div class="mcp-card">
        <span>API</span>
        <strong :class="health?.status === 'ok' ? 'ok' : 'bad'">
          {{ health?.status || 'unknown' }}
        </strong>
      </div>

      <div class="mcp-card">
        <span>Security</span>
        <strong :class="security?.security?.enabled ? 'ok' : 'warn'">
          {{ security?.security?.enabled ? 'Enabled' : 'Disabled' }}
        </strong>
      </div>

      <div class="mcp-card">
        <span>Default Action</span>
        <strong>
          {{ security?.security?.default_action || 'unknown' }}
        </strong>
      </div>

      <div class="mcp-card">
        <span>Pending</span>
        <strong :class="pendingCount > 0 ? 'warn' : 'ok'">
          {{ pendingCount }}
        </strong>
      </div>
    </div>

    <div class="mcp-layout">
      <aside class="mcp-panel mcp-list-panel">
        <div class="mcp-panel-header">
          <h2>Approvals</h2>
          <span>{{ approvals.length }}</span>
        </div>

        <div class="mcp-list">
          <button
            v-for="item in approvals"
            :key="item.id"
            class="mcp-list-item"
            :class="{ selected: selectedApproval?.id === item.id }"
            @click="selectApproval(item)"
          >
            <div class="mcp-list-main">
              <strong>{{ item.tool }}</strong>
              <span :class="statusClass(item.status)">
                {{ item.status }}
              </span>
            </div>

            <small>{{ item.id }}</small>
            <small>{{ formatTime(item.createdAt) }}</small>
          </button>

          <div v-if="approvals.length === 0" class="mcp-empty">
            目前沒有 approval 紀錄。
          </div>
        </div>
      </aside>

      <main class="mcp-panel mcp-detail-panel">
        <template v-if="selectedApproval">
          <div class="mcp-panel-header">
            <div>
              <h2>{{ selectedApproval.tool }}</h2>
              <p>{{ selectedApproval.id }}</p>
            </div>

            <div class="mcp-actions">
              <button
                class="mcp-btn primary"
                :disabled="busy || selectedApproval.status !== 'pending'"
                @click="approveSelected"
              >
                Approve
              </button>

              <button
                class="mcp-btn danger"
                :disabled="busy || selectedApproval.status !== 'pending'"
                @click="denySelected"
              >
                Deny
              </button>
            </div>
          </div>

          <div class="mcp-detail-grid">
            <div>
              <span>Status</span>
              <strong :class="statusClass(selectedApproval.status)">
                {{ selectedApproval.status }}
              </strong>
            </div>

            <div>
              <span>Action</span>
              <strong>{{ selectedApproval.decision?.action }}</strong>
            </div>

            <div>
              <span>Created</span>
              <strong>{{ formatTime(selectedApproval.createdAt) }}</strong>
            </div>

            <div>
              <span>Updated</span>
              <strong>{{ formatTime(selectedApproval.updatedAt) }}</strong>
            </div>
          </div>

          <div class="mcp-section">
            <h3>Reason</h3>
            <pre>{{ selectedApproval.decision?.reason }}</pre>
          </div>

          <div class="mcp-section">
            <h3>Arguments</h3>
            <pre>{{ formatJson(selectedApproval.arguments) }}</pre>
          </div>

          <div v-if="selectedApproval.result" class="mcp-section">
            <h3>Result</h3>
            <pre>{{ selectedApproval.result }}</pre>
          </div>

          <div v-if="selectedApproval.error" class="mcp-section">
            <h3>Error</h3>
            <pre class="danger-box">{{ selectedApproval.error }}</pre>
          </div>
        </template>

        <template v-else>
          <div class="mcp-empty big">
            <h2>選擇一筆 MCP approval</h2>
            <p>
              當 ChatGPT 嘗試執行 write / edit / create / move 等需要確認的 MCP tool，
              會出現在這裡。
            </p>
          </div>
        </template>
      </main>
    </div>

    <section class="mcp-panel mcp-audit-panel">
      <div class="mcp-panel-header">
        <h2>MCP Audit</h2>
        <button class="mcp-btn small" :disabled="busy" @click="refresh">
          Refresh
        </button>
      </div>

      <div class="mcp-audit-list">
        <article
          v-for="item in audit"
          :key="`${item.time}-${item.tool}-${item.status}-${JSON.stringify(item.arguments)}`"
          class="mcp-audit-item"
        >
          <div class="mcp-audit-head">
            <strong>{{ item.tool }}</strong>
            <span :class="statusClass(item.status)">
              {{ item.status }}
            </span>
          </div>

          <small>{{ item.time }}</small>

          <pre>{{ shortText(item.error || item.resultPreview || formatJson(item.arguments), 700) }}</pre>
        </article>

        <div v-if="audit.length === 0" class="mcp-empty">
          尚無 MCP audit 紀錄。
        </div>
      </div>
    </section>
  </section>
</template>