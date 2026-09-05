import { spawn } from 'node:child_process'
import { defineTool } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'
import { QuestionBridge } from './question-tool.js'

export type GuardVerdict = 'allow' | 'approve' | 'deny'

const ALLOWED_BINARIES = new Set(['learntrace', 'git'])

const READONLY_GIT_SUBCOMMANDS = new Set([
  'status',
  'log',
  'show',
  'diff',
  'blame',
  'ls-files',
  'ls-tree',
  'rev-parse',
  'describe',
  'branch',
  'tag',
  'remote',
  'help',
  '--version',
])

const SHELL_METACHARACTERS = /[;&|<>$`\n\r]/

export function tokenizeCommand(command: string): string[] | null {
  if (SHELL_METACHARACTERS.test(command)) return null
  const tokens = command.trim().split(/\s+/).filter(Boolean)
  return tokens.length > 0 ? tokens : null
}

export function classifyCommand(command: string): { verdict: GuardVerdict; reason?: string } {
  const argv = tokenizeCommand(command)
  if (!argv) {
    return {
      verdict: 'deny',
      reason: '命令包含 shell 元字符；LearnTrace 不会以 shell 方式拼接执行命令。',
    }
  }
  const binary = argv[0] as string
  const subcommand = argv[1]
  if (binary === 'learntrace') return { verdict: 'allow' }
  if (binary === 'git') {
    if (subcommand === undefined || READONLY_GIT_SUBCOMMANDS.has(subcommand)) {
      return { verdict: 'allow' }
    }
    return { verdict: 'approve', reason: 'git 写操作需要你在界面中明确批准。' }
  }
  if (!ALLOWED_BINARIES.has(binary)) {
    return { verdict: 'approve', reason: `命令 ${binary} 不在自动放行列表，需要你在界面中明确批准。` }
  }
  return { verdict: 'allow' }
}

const ENV_ALLOWLIST = new Set([
  'PATH',
  'PATHEXT',
  'SYSTEMROOT',
  'WINDIR',
  'HOME',
  'USERPROFILE',
  'TEMP',
  'TMP',
  'COMSPEC',
  'HOMEDRIVE',
  'USERNAME',
])

export function minimalEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined && ENV_ALLOWLIST.has(key.toUpperCase())) result[key] = value
  }
  if (result.PATH === undefined && result.Path !== undefined) result.PATH = result.Path
  return result
}

type RunOutcome = { exitCode: number | null; output: string }

function runCommand(argv: string[], cwd: string, timeoutMs = 120_000): Promise<RunOutcome> {
  return new Promise(resolve => {
    const binary = argv[0] as string
    const child = spawn(binary, argv.slice(1), {
      cwd,
      env: minimalEnvironment(process.env),
      shell: false,
      windowsHide: true,
    })
    const chunks: Buffer[] = []
    const timer = setTimeout(() => child.kill(), timeoutMs)
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk))
    child.stderr.on('data', (chunk: Buffer) => chunks.push(chunk))
    child.on('error', error => {
      clearTimeout(timer)
      resolve({ exitCode: null, output: `无法执行命令：${error.message}` })
    })
    child.on('close', (exitCode, signal) => {
      clearTimeout(timer)
      const output = Buffer.concat(chunks).toString('utf8').trim()
      const signalNote = signal ? `（被信号 ${signal} 终止）` : ''
      resolve({ exitCode, output: `${output}${signalNote}` })
    })
  })
}

function truncate(text: string, limit = 24_000): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}\n…（输出已截断）`
}

const schema = Type.Object({
  command: Type.String({ description: '要在当前项目中执行的命令，不包含 shell 运算符' }),
})

export function createCommandTool(bridge: QuestionBridge, cwd: string) {
  return defineTool<typeof schema, { approved?: boolean; exitCode?: number | null }>({
    name: 'run_command',
    label: '执行命令',
    description:
      '在 LearnTrace 当前项目中执行命令。learntrace 与只读 git 命令自动放行；其他命令必须先获得用户批准。',
    promptSnippet: '需要运行命令时使用 run_command',
    promptGuidelines: [
      '运行项目命令一律使用 run_command，不要假定存在 bash 工具。',
      'learntrace 与只读 git（status/log/show/diff 等）可自动放行。',
      '写操作或未列出的命令会自动请求用户批准；等待批准结果后再继续。',
      '绝不用 shell 运算符拼接多条命令；一次只执行一条。',
    ],
    parameters: schema,
    async execute(_toolCallId, params, signal) {
      const command = String(params.command ?? '').trim()
      const classified = classifyCommand(command)
      if (classified.verdict === 'deny') {
        return {
          content: [{ type: 'text', text: `已阻止执行：${classified.reason}` }],
          details: { approved: false },
        }
      }
      let approved = classified.verdict === 'allow'
      if (!approved) {
        const answer = await bridge.ask({
          question: `是否允许执行以下命令？\n\n${command}`,
          options: ['允许执行', '拒绝'],
          allowMultiple: false,
          allowText: false,
          skippable: true,
          ...(classified.reason ? { reason: classified.reason } : {}),
        }, signal)
        approved = String(answer ?? '').startsWith('允许')
        if (!approved) {
          return {
            content: [{ type: 'text', text: '用户拒绝了该命令的执行。' }],
            details: { approved: false },
          }
        }
      }
      const outcome = await runCommand(tokenizeCommand(command) ?? [], cwd)
      const head = outcome.exitCode === 0 ? '' : `（退出码 ${outcome.exitCode ?? '未知'}）`
      return {
        content: [{ type: 'text', text: truncate(outcome.output || `命令已执行${head}，无输出。`) }],
        details: { approved: true, exitCode: outcome.exitCode },
      }
    },
  })
}