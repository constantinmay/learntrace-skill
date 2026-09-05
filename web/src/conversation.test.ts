import { describe, expect, it } from 'vitest'
import { buildConversationRows, currentActivity } from './conversation'
import type { UIEvent } from './types'

function event(sequence: number, type: string, update: Record<string, unknown>): UIEvent {
  return { session_id: 'session', sequence, type, created_at: '2026-01-01T00:00:00Z', payload: { update } }
}

describe('buildConversationRows', () => {
  it('joins streamed chunks from the same Pi message', () => {
    const rows = buildConversationRows([
      event(1, 'message_delta', { messageId: 'm1', content: { text: '你好' } }),
      event(2, 'message_delta', { messageId: 'm1', content: { text: '，世界' } }),
    ])
    expect(rows).toEqual([{ kind: 'message', key: 'message-m1', role: 'agent', text: '你好，世界' }])
  })

  it('keeps tool execution out of the conversation', () => {
    const rows = buildConversationRows([
      event(1, 'tool_started', { toolCallId: 't1', status: 'in_progress' }),
      event(2, 'tool_updated', { toolCallId: 't1', status: 'completed' }),
    ])
    expect(rows).toEqual([])
  })

  it('shows only the currently running activity', () => {
    const events = [
      event(1, 'tool_started', { toolCallId: 't1', title: 'learntrace git-index C:\\project', status: 'in_progress' }),
      event(2, 'tool_updated', { toolCallId: 't1', title: 'learntrace git-index C:\\project', status: 'completed' }),
      event(3, 'tool_started', { toolCallId: 't2', title: 'learntrace git-file C:\\project', status: 'in_progress' }),
    ]
    expect(currentActivity(events)).toBe('正在核对项目材料')
    expect(currentActivity([...events, event(4, 'tool_completed', { toolCallId: 't2', status: 'completed' })])).toBeNull()
    expect(currentActivity([...events, { ...event(4, 'session_state', {}), payload: { state: 'cancelled' } }])).toBeNull()
  })
})
