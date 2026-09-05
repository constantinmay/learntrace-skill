import { defineTool } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'
import { QuestionBridge } from './question-tool.js'
import { runCommand, type CommandSpec } from './process-runner.js'

export type GuardVerdict = 'allow' | 'approve' | 'deny'

// Match complete argv, never a whole Git subcommand family. Other forms can
// write refs/config/files or invoke repository-configured helpers.
const GIT_QUERIES = new Set([
  ['--version'], ['status'], ['status', '--short'], ['status', '--porcelain'],
  ['branch', '--show-current'], ['remote', '-v'], ['rev-parse', 'HEAD'],
  ['rev-parse', '--show-toplevel'],
].map(args => JSON.stringify(args)))

const PARSE_OPTIONS = ['--document', '--test-log', '--no-git', '--max-commits', '--find-copies-harder', '--author']
const SAFE_LEARNTRACE_OPTIONS: Record<string, readonly string[]> = {
  discover: [],
  parse: [...PARSE_OPTIONS, '-o', '--output'],
  run: [...PARSE_OPTIONS, '--confirmations'],
  archive: ['--trace-result', '--snapshot', '--confirmations', '-o', '--output', '--records-output', '--questions-output'],
  'git-index': ['-o', '--output'],
  'git-tree': ['-o', '--output'],
  'git-file': ['--lines', '--bytes', '-o', '--output'],
  'git-worktree': ['--max-chars', '-o', '--output'],
  'git-evidence': ['--path', '--max-chars', '-o', '--output-dir'],
  'verify-narrative': [],
  'render-narrative': ['-o', '--output', '--variant'],
}

export function classifyCommand(command: CommandSpec): { verdict: GuardVerdict; reason?: string } {
  const { executable, args } = command
  if (!executable || executable.includes('\0') || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
    return { verdict: 'deny', reason: '可执行文件与参数必须有效，不能包含空字节。' }
  }
  if (executable === 'git' && GIT_QUERIES.has(JSON.stringify(args))) return { verdict: 'allow' }
  if (executable === 'learntrace') {
    if (args.length === 1 && ['--version', '--help', '-h'].includes(args[0]!)) return { verdict: 'allow' }
    const subcommand = args[0] ?? ''
    const options = Object.hasOwn(SAFE_LEARNTRACE_OPTIONS, subcommand)
      ? SAFE_LEARNTRACE_OPTIONS[subcommand] : undefined
    // Unknown/abbreviated options require approval, including argparse's
    // abbreviated authorization flags. Export/authorization commands are not
    // in this allowlist at all.
    if (options && args.slice(1).every(arg => !arg.startsWith('-') || ['--help', '-h', '--', ...options].includes(arg.split('=')[0]!))) {
      return { verdict: 'allow' }
    }
    return { verdict: 'approve', reason: '此 LearnTrace 调用包含授权、轨迹导出或未列入自动放行范围的操作，需要明确批准。' }
  }
  return { verdict: 'approve', reason: '此命令不属于已核对的精确查询形式，需要明确批准。' }
}

const schema = Type.Object({
  executable: Type.String({ minLength: 1, maxLength: 4096, description: '可执行文件名，例如 learntrace 或 git；不包含命令参数' }),
  args: Type.Array(Type.String({ maxLength: 16384 }), { maxItems: 256, description: '每项是一个原始参数。路径中有空格也保持一个参数，不添加 shell 引号。' }),
})

export function createCommandTool(bridge: QuestionBridge, cwd: string) {
  return defineTool<typeof schema, { approved: boolean; exitCode?: number | null; truncated?: boolean }>({
    name: 'run_command', label: '执行命令',
    description: '通过 executable 和 args 数组运行一个命令，不经过 shell。只自动放行指定 LearnTrace 工作流和精确 Git 查询；授权、导出或其他调用需要用户批准。',
    promptSnippet: '需要执行命令时使用 run_command 的 executable 和 args 数组',
    promptGuidelines: [
      '例如 learntrace --version 应传 executable="learntrace", args=["--version"]。不要使用旧的 command 字符串参数。',
      '不要为路径或参数添加 shell 引号、不要拼接多条命令。每个参数原样放进数组。',
      '授权和敏感操作会请求明确批准；不要通过其他命令或写文件绕过批准。',
      '输出达到64 KiB会终止命令并标明截断。优先使用 LearnTrace 分页回读，不能把截断结果当作完整证据。',
    ],
    parameters: schema,
    async execute(_toolCallId, params, signal) {
      // The same immutable argv is classified, displayed for approval and run.
      const command = Object.freeze({ executable: params.executable, args: Object.freeze([...params.args]) })
      const classified = classifyCommand(command)
      if (classified.verdict === 'deny') throw new Error(classified.reason)
      if (classified.verdict === 'approve') {
        const answer = await bridge.ask({
          question: `是否允许执行以下命令？\n工作目录：${cwd}\n${JSON.stringify(command, null, 2)}`,
          options: ['允许执行', '拒绝'], allowMultiple: false, allowText: false, skippable: true,
          ...(classified.reason ? { reason: classified.reason } : {}),
        }, signal)
        if (answer !== '允许执行') return { content: [{ type: 'text', text: '用户未批准，该命令未执行。' }], details: { approved: false } }
      }
      signal?.throwIfAborted()
      const result = await runCommand(command, cwd, signal)
      signal?.throwIfAborted()
      if (result.truncated) throw new Error(`输出已截断（stdout/stderr 合计超过64 KiB），命令及其进程树已终止；请缩小范围或分页。\n${result.output}`)
      if (result.timedOut) throw new Error(`命令超时，命令及其进程树已终止。\n${result.output}`)
      if (result.exitCode !== 0) throw new Error(`命令执行失败（退出码 ${result.exitCode ?? '未知'}）：${result.output}`)
      return { content: [{ type: 'text', text: result.output || '命令已完成，无输出。' }], details: { approved: true, exitCode: result.exitCode, truncated: false } }
    },
  })
}
