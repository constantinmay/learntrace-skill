import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import { expect, it } from 'vitest'
import { LearnTraceSession } from './session.js'

it('exposes required custom tools in a real Pi session, without calling a model', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'learntrace-sdk-'))
  const events: Record<string, unknown>[] = []
  const session = new LearnTraceSession('tool-check', directory, resolve('../skills/learntrace'), join(directory, 'pi'), event => events.push(event))
  try {
    await session.start({ baseUrl: 'https://example.invalid', apiKey: 'test', modelId: 'test', api: 'openai-completions' })
    const ready = events.find(event => event.type === 'session_ready')
    expect(ready?.active_tools).toEqual(expect.arrayContaining(['run_command', 'write_artifact', 'request_user_input']))
    expect(ready?.active_tools).not.toEqual(expect.arrayContaining(['bash', 'write']))
  } finally {
    session.close()
    await rm(directory, { recursive: true, force: true })
  }
}, 20_000)
