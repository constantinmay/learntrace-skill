import { createInterface } from 'node:readline'
import type { RuntimeConfig, ServiceCommand, ServiceMessage } from './protocol.js'
import { assertRuntimeConfig } from './protocol.js'
import { LearnTraceSession } from './session.js'
import { probeRuntimeModel } from './model.js'

const sessions = new Map<string, LearnTraceSession>()

function send(message: ServiceMessage): void {
  process.stdout.write(`${JSON.stringify(message)}\n`)
}

function reply(id: string, data?: unknown): void {
  send({ type: 'response', id, success: true, ...(data === undefined ? {} : { data }) })
}

function fail(id: string, error: unknown): void {
  send({
    type: 'response',
    id,
    success: false,
    error: error instanceof Error ? error.message : String(error),
  })
}

async function handle(command: ServiceCommand): Promise<void> {
  if (!command.id || !command.sessionId) throw new Error('命令缺少 id 或 sessionId。')
  if (command.type === 'probe') {
    if (!command.config) throw new Error('连接测试缺少模型配置。')
    assertRuntimeConfig(command.config)
    await probeRuntimeModel(command.config)
    reply(command.id, { connected: true })
    return
  }
  if (command.type === 'start') {
    if (!command.cwd || !command.skillPath || !command.sessionDir || !command.config) {
      throw new Error('启动参数不完整。')
    }
    assertRuntimeConfig(command.config)
    if (sessions.has(command.sessionId)) throw new Error('会话已经存在。')
    const session = new LearnTraceSession(
      command.sessionId,
      command.cwd,
      command.skillPath,
      command.sessionDir,
      event => send({ type: 'event', sessionId: command.sessionId, event }),
    )
    try {
      await session.start(command.config, Boolean(command.resume))
      sessions.set(command.sessionId, session)
      reply(command.id, { runtime: 'embedded-pi-sdk' })
    } catch (error) {
      session.close()
      throw error
    }
    return
  }
  const session = sessions.get(command.sessionId)
  if (!session) throw new Error('Agent 会话不存在或需要重新配置 API。')
  if (command.type === 'prompt') {
    // Model turns may take minutes. Acknowledge before starting so the HTTP
    // request is not tied to the turn duration; progress continues as events.
    reply(command.id)
    void session.prompt(command.text ?? '').catch(error => {
      send({
        type: 'event',
        sessionId: command.sessionId,
        event: {
          type: 'runtime_error',
          message: error instanceof Error ? error.message : String(error),
        },
      })
    })
    return
  } else if (command.type === 'answer') {
    session.answer(command.requestId ?? '', command.value)
  } else if (command.type === 'configure') {
    const level = command.config?.thinkingLevel
    if (!level) throw new Error('仅支持修改思考强度；更换 API 或模型请新建会话。')
    session.setThinking(level)
  } else if (command.type === 'abort') {
    await session.abort()
  } else if (command.type === 'close') {
    session.close()
    sessions.delete(command.sessionId)
  }
  reply(command.id)
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
lines.on('line', line => {
  let command: ServiceCommand
  try {
    command = JSON.parse(line) as ServiceCommand
  } catch {
    return
  }
  void handle(command).catch(error => fail(command.id, error))
})

async function shutdown(): Promise<void> {
  for (const session of sessions.values()) session.close()
  sessions.clear()
  process.exit(0)
}

process.once('SIGINT', () => void shutdown())
process.once('SIGTERM', () => void shutdown())
