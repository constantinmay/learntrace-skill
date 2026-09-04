import type { UIEvent } from './types'

export type ConversationRow =
  | { kind: 'message'; key: string; text: string; role: string }
  | { kind: 'event'; key: string; event: UIEvent }

const HIDDEN_EVENTS = new Set([
  'agent_update',
  'analysis_started',
  'artifact_updated',
  'compatibility_diagnostic',
  'config_updated',
  'default_permission',
  'request_answered',
  'session_completed',
  'session_config',
  'session_state',
  'user_input_completed',
])

function activityLabel(title: string): string {
  const value = title.toLowerCase()
  if (value.includes('discover')) return '正在了解项目范围'
  if (value.includes('git-index') || value.includes('git-tree') || value.includes('git log')) return '正在整理开发历史'
  if (value.includes('git-file') || value.includes('git-evidence') || value.includes('read')) return '正在核对项目材料'
  if (value.includes('verify') || value.includes('render')) return '正在生成并校验学习档案'
  if (value.includes('learntrace run') || value.includes('archive')) return '正在整理学习证据'
  return '正在调查项目证据'
}

export function currentActivity(events: UIEvent[]): string | null {
  const states = new Map<string, { title: string; active: boolean }>()
  for (const event of events) {
    if (!event.type.startsWith('tool_')) continue
    const update = event.payload.update as { toolCallId?: string; title?: string; status?: string } | undefined
    const id = String(update?.toolCallId ?? `tool-${event.sequence}`)
    const status = event.type === 'tool_completed'
      ? 'completed'
      : String(update?.status ?? 'in_progress').toLowerCase()
    states.set(id, {
      title: String(update?.title ?? ''),
      active: ['in_progress', 'pending', 'running'].includes(status),
    })
  }
  const active = [...states.values()].filter(item => item.active).at(-1)
  return active ? activityLabel(active.title) : null
}

export function buildConversationRows(events: UIEvent[]): ConversationRow[] {
  const rows: ConversationRow[] = []
  for (const event of events) {
    if (HIDDEN_EVENTS.has(event.type) || event.type.startsWith('tool_')) continue
    const update = event.payload.update as { content?: { text?: string }; messageId?: string } | undefined
    if (event.type === 'message_delta') {
      const text = String(event.payload.text ?? update?.content?.text ?? '')
      if (!text) continue
      const key = `message-${String(event.payload.message_id ?? update?.messageId ?? `delta-${event.sequence}`)}`
      const previous = rows.at(-1)
      if (previous?.kind === 'message' && previous.key === key) previous.text += text
      else rows.push({ kind: 'message', key, text, role: String(event.payload.role ?? 'agent') })
      continue
    }
    if (event.type === 'message_completed') {
      const text = String(event.payload.text ?? '')
      if (text) rows.push({ kind: 'message', key: `completed-${event.sequence}`, text, role: String(event.payload.role ?? 'user') })
      continue
    }
    rows.push({ kind: 'event', key: `event-${event.sequence}`, event })
  }
  return rows
}
