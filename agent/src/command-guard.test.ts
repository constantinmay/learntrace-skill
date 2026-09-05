import { expect, it } from 'vitest'
import { QuestionBridge, type UserQuestion } from './question-tool.js'
import { classifyCommand, createCommandTool } from './command-guard.js'
import { join, resolve, sep } from 'node:path'

const TEST_CWD = process.platform === 'win32' ? 'C:\\learn\\project' : '/learn/project'
const OUTSIDE_ABS = resolve(TEST_CWD, '..', 'other-project')
const ESCAPE_REL = `..${sep}other-project`
const OUTSIDE_FILE = resolve(TEST_CWD, '..', 'outside.txt')
const OUTSIDE_OUT = resolve(TEST_CWD, '..', 'report.md')


it.each([
  ['git', ['branch', 'injected-review']], ['git', ['branch', '-D', 'main']],
  ['git', ['tag', 'injected-review']], ['git', ['remote', 'add', 'leak', 'https://example.com/x']],
  ['git', ['status', '--short', '--untracked-files=all']],
  ['git', ['log', '--output=source.txt']], ['git', ['show', '--ext-diff']],
  ['git', ['-c', 'alias.x=!malicious', 'x']],
  ['learntrace', ['authorize-full-read', '.', '--session-export', 'secret', '--source', 'codex', '--authorized-at', 'now']],
  ['learntrace', ['run', '.', '--authorized', '--opencode-export', 'secret']],
  ['learntrace', ['run', '.', '--auth', '--opencode-export=secret']],
  ['learntrace', ['export-evidence', '.', '--session-export', 'secret', '--authorization', 'full']],
  ['learntrace', ['adapt', 'secret', '--authorized']],
  ['learntrace', ['ui', '.']], ['learntrace', ['unknown', '.']],
  ['learntrace', ['constructor', '--authorized']], ['learntrace', ['toString']],
  ['learntrace', ['run', '.', '--unknown']], ['node', ['-e', 'process.exit()']],
] as [string, string[]][])('requires explicit approval: %s %j', (executable, args) => {
  expect(classifyCommand({ executable, args }).verdict).toBe('approve')
})

it.each([
  ['git', ['status']], ['git', ['status', '--short']], ['git', ['branch', '--show-current']],
  ['git', ['remote', '-v']], ['git', ['--version']],
  ['learntrace', ['--version']], ['learntrace', ['discover', '.']],
  ['learntrace', ['run', '.', '--author', 'UI Demo', '--document', 'README.md']],
  ['learntrace', ['git-file', '.', 'HEAD', 'README.md', '--lines', '1:20']],
  ['learntrace', ['render-narrative', 'payload.json', 'archive.json', '--output', join('.learntrace', 'ui-sessions', 'session-1', 'report.md'), '--variant', 'submitted']],
] as [string, string[]][])('allows the known workflow: %s %j', (executable, args) => {
  expect(classifyCommand({ executable, args }, TEST_CWD).verdict).toBe('allow')
})


it.each([
  ['discover', [OUTSIDE_ABS]],
  ['discover', [ESCAPE_REL]],
  ['parse', [OUTSIDE_ABS, '--document', 'README.md']],
  ['git-file', [OUTSIDE_ABS, 'HEAD', 'secrets.py']],
  ['git-file', ['.', 'HEAD', ESCAPE_REL]],
  ['run', ['.', '--document', OUTSIDE_FILE]],
  ['run', ['.', '--confirmations', OUTSIDE_FILE]],
  ['parse', ['.', '-o', OUTSIDE_OUT]],
  ['archive', ['.learntrace', '--snapshot', OUTSIDE_FILE]],
  ['render-narrative', ['payload.json', 'archive.json', '-o', 'README.md']],
] as [string, string[]][])('approves learntrace paths outside the session project: %j', (subcommand, tail) => {
  expect(classifyCommand({ executable: 'learntrace', args: [subcommand, ...tail] }, TEST_CWD).verdict).toBe('approve')
})

if (process.platform === 'win32') {
  it.each([['C:\\other-project'], ['..\\other-project']])('approves windows out-of-scope project root: %s', project => {
    expect(classifyCommand({ executable: 'learntrace', args: ['discover', project] }, TEST_CWD).verdict).toBe('approve')
  })
}
it('denies invalid executable/arguments', () => {
  expect(classifyCommand({ executable: '', args: [] }).verdict).toBe('deny')
  expect(classifyCommand({ executable: 'git', args: ['status\0'] }).verdict).toBe('deny')
})

it('approval and spawn use one immutable argv without interpreting shell syntax', async () => {
  let question: UserQuestion | undefined
  const bridge = new QuestionBridge(item => { question = item })
  const tool = createCommandTool(bridge, process.cwd())
  const literals = ['hello world', 'E:\\course project', '"quoted"', '', 'a;b', '$(echo bad)', 'x|y', '中文']
  const params = { executable: process.execPath, args: ['-e', 'console.log(JSON.stringify(process.argv.slice(1)))', '--', ...literals] }
  const pending = tool.execute('argv', params, undefined, undefined, {} as Parameters<typeof tool.execute>[4])
  expect(question).toBeDefined()
  expect(question!.question).toContain(JSON.stringify(params, null, 2))
  params.args.splice(0, params.args.length, '-e', 'throw new Error("changed")')
  bridge.answer(question!.requestId, '允许执行')
  const result = await pending
  expect(result.content[0]).toMatchObject({ type: 'text', text: JSON.stringify(literals) + '\n' })
})

it.each(['拒绝', null, '允许执行但没有正式批准', ['允许执行']])('does not execute without exact approval: %j', async answer => {
  const bridge = new QuestionBridge(question => queueMicrotask(() => bridge.answer(question.requestId, answer)))
  const tool = createCommandTool(bridge, process.cwd())
  const result = await tool.execute('deny', { executable: 'does-not-exist', args: [] }, undefined, undefined, {} as Parameters<typeof tool.execute>[4])
  expect(result.details.approved).toBe(false)
})
