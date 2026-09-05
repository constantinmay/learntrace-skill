import { mkdtemp, mkdir, readFile, realpath, rm, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, it } from 'vitest'
import { createArtifactTool } from './artifact-tool.js'

const directories: string[] = []
const context = {} as Parameters<ReturnType<typeof createArtifactTool>['execute']>[4]
async function workspace() {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'learntrace-artifact-')))
  directories.push(root)
  return root
}
afterEach(async () => { for (const root of directories.splice(0)) await rm(root, { recursive: true, force: true }) })

it('writes and revises JSON only inside the current session', async () => {
  const root = await workspace()
  const tool = createArtifactTool(root, 'session-1')
  for (const content of ['{"version":1}', '{"version":2}']) {
    const result = await tool.execute('call', { filename: 'narrative-payload.json', content }, undefined, undefined, context)
    expect(result.details.path).toBe(join(root, '.learntrace', 'ui-sessions', 'session-1', 'narrative-payload.json'))
    expect(JSON.parse(await readFile(result.details.path, 'utf8'))).toEqual(JSON.parse(content))
  }
  await expect(tool.execute('call', { filename: '../source.ts' as 'narrative-payload.json', content: '{}' }, undefined, undefined, context)).rejects.toThrow(/只能/)
  await expect(tool.execute('call', { filename: 'narrative-payload.json', content: 'invalid' }, undefined, undefined, context)).rejects.toThrow()
})

it('rejects directory links and cancelled writes', async () => {
  const root = await workspace()
  const outside = await workspace()
  await symlink(outside, join(root, '.learntrace'), 'junction')
  await expect(createArtifactTool(root, 'session-1').execute('call', { filename: 'narrative-payload.json', content: '{}' }, undefined, undefined, context)).rejects.toThrow(/链接/)
  const plain = await workspace()
  await mkdir(join(plain, '.learntrace'))
  await expect(createArtifactTool(plain, 'session-1').execute('call', { filename: 'narrative-payload.json', content: '{}' }, AbortSignal.abort(), undefined, context)).rejects.toThrow()
})
