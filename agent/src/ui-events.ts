// SDK updates include the cumulative assistant message, thinking and tool
// results. The UI transport needs only text deltas and lifecycle metadata.
type Event = Record<string, any>
const short = (value: unknown, length = 1024) => typeof value === 'string' ? value.slice(0, length) : ''

export class UIEventProjector {
  private streamed = false

  private text(text: string): Event[] {
    const events: Event[] = []
    // Array.from preserves Unicode code points across frame boundaries.
    const chars = Array.from(text)
    for (let offset = 0; offset < chars.length; offset += 2048) {
      events.push({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: chars.slice(offset, offset + 2048).join('') } })
    }
    if (events.length) this.streamed = true
    return events
  }

  project(event: Event): Event[] {
    const type = event.type
    if (type === 'message_start' && event.message?.role === 'assistant') {
      this.streamed = false
      return [{ type, message: { role: 'assistant' } }]
    }
    if (type === 'message_update') {
      const update = event.assistantMessageEvent
      return update?.type === 'text_delta' ? this.text(String(update.delta ?? '')) : []
    }
    if (type === 'message_end' && event.message?.role === 'assistant') {
      const message = event.message
      const fallback = !this.streamed && message.stopReason !== 'error'
        ? (typeof message.content === 'string' ? message.content : (message.content ?? []).filter((item: Event) => item.type === 'text').map((item: Event) => item.text).join(''))
        : ''
      return [...this.text(fallback), { type, message: { role: 'assistant', stopReason: short(message.stopReason), errorMessage: short(message.errorMessage, 4096) } }]
    }
    if (['tool_execution_start', 'tool_execution_update', 'tool_execution_end'].includes(type)) {
      const args: Event = {}
      for (const key of ['path', 'file_path', 'command', 'filename', 'pattern']) {
        if (event.args?.[key]) args[key] = short(event.args[key], 240)
      }
      if (event.args?.executable) {
        args.executable = short(event.args.executable, 240)
        args.args = Array.isArray(event.args.args) ? event.args.args.slice(0, 12).map((arg: unknown) => short(arg, 240)) : []
      }
      return [{ type, toolCallId: short(event.toolCallId), toolName: short(event.toolName), args, isError: Boolean(event.isError) }]
    }
    if (['agent_start', 'agent_settled', 'compaction_start'].includes(type)) return [{ type }]
    if (type === 'auto_retry_start') return [{ type, attempt: event.attempt, maxAttempts: event.maxAttempts, delayMs: event.delayMs, errorMessage: short(event.errorMessage) }]
    return []
  }
}
