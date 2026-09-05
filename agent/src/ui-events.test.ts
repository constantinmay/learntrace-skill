import { expect, it } from 'vitest'
import { UIEventProjector } from './ui-events.js'

it('drops cumulative thinking and tool bodies from every UI event', () => {
  const projector = new UIEventProjector()
  const hidden = 'private-thinking'.repeat(100000)
  expect(projector.project({ type: 'message_update', message: { content: hidden }, assistantMessageEvent: { type: 'thinking_delta', delta: hidden } })).toEqual([])
  const frames = projector.project({ type: 'message_update', message: { content: hidden }, assistantMessageEvent: { type: 'text_delta', delta: '你好', partial: hidden } })
  expect(JSON.stringify(frames)).not.toContain('private-thinking')
  expect(frames[0]?.assistantMessageEvent.delta).toBe('你好')
  const tool = projector.project({ type: 'tool_execution_end', toolCallId: 't1', toolName: 'read', result: hidden, isError: true })
  expect(tool[0]?.isError).toBe(true)
  expect(JSON.stringify(tool).length).toBeLessThan(1000)
})

it('preserves large Unicode text in small frames and does not duplicate final text', () => {
  const projector = new UIEventProjector()
  projector.project({ type: 'message_start', message: { role: 'assistant' } })
  const text = '中文😀\n'.repeat(30000)
  const frames = projector.project({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: text } })
  expect(frames.map(event => event.assistantMessageEvent.delta).join('')).toBe(text)
  expect(frames.every(event => Buffer.byteLength(JSON.stringify(event)) < 64000)).toBe(true)
  expect(projector.project({ type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text }], stopReason: 'stop' } })).toHaveLength(1)
  projector.project({ type: 'message_start', message: { role: 'assistant' } })
  const fallback = projector.project({ type: 'message_end', message: { role: 'assistant', content: [{ type: 'thinking', thinking: 'hidden' }, { type: 'text', text }], stopReason: 'stop' } })
  expect(fallback.slice(0, -1).map(event => event.assistantMessageEvent.delta).join('')).toBe(text)
  expect(projector.project({ type: 'agent_settled', messages: [text] })).toEqual([{ type: 'agent_settled' }])
})

it('retains model errors without forwarding message bodies', () => {
  const projector = new UIEventProjector()
  expect(projector.project({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'HTTP 503', content: 'hidden' } })).toEqual([
    { type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'HTTP 503' } },
  ])
})
