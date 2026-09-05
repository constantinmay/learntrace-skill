import { defineTool } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'
import { QuestionBridge } from './question-tool.js'
import { runCommand, type CommandSpec } from './process-runner.js'
import { realpathSync } from 'node:fs'
import { basename, dirname, join, resolve, sep } from 'node:path'

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

// Positional and option shapes mirror the real argparse definitions in
// src/learntrace/cli.py and src/learntrace/archive.py: options may appear
// anywhere before '--', '--' ends option parsing, and repeated options are
// legal. The guard parses argv the same way the CLI will, so it cannot be
// bypassed by reordering arguments or by a '--' separator the way fixed
// argv-index checks can.
type GrammarPositional = { role: 'project' | 'repoPath' | 'plain' | 'input'; optional?: boolean }
type GrammarOption = { name: string; kind: 'flag' | 'value'; role?: 'plain' | 'input' | 'output' }
type CommandGrammar = { positionals: GrammarPositional[]; options: GrammarOption[] }
type BoundPath = { label: string; value: string }
type ParsedCommand = { project?: string; repoPaths: string[]; inputs: BoundPath[]; outputs: BoundPath[] }

const OUTPUT_SHORT: GrammarOption = { name: '-o', kind: 'value', role: 'output' }
const OUTPUT_LONG: GrammarOption = { name: '--output', kind: 'value', role: 'output' }
const PARSE_OPTION_SPECS: GrammarOption[] = [
  { name: '--document', kind: 'value', role: 'input' },
  { name: '--test-log', kind: 'value', role: 'input' },
  { name: '--max-commits', kind: 'value' },
  { name: '--author', kind: 'value' },
  { name: '--no-git', kind: 'flag' },
  { name: '--find-copies-harder', kind: 'flag' },
  OUTPUT_SHORT, OUTPUT_LONG,
]

const GRAMMARS: Record<string, CommandGrammar> = {
  discover: { positionals: [{ role: 'project' }], options: [] },
  parse: { positionals: [{ role: 'project' }], options: PARSE_OPTION_SPECS },
  run: {
    positionals: [{ role: 'project' }],
    options: [...PARSE_OPTION_SPECS, { name: '--confirmations', kind: 'value', role: 'input' }],
  },
  archive: {
    positionals: [{ role: 'project', optional: true }],
    options: [
      { name: '--trace-result', kind: 'value', role: 'input' },
      { name: '--snapshot', kind: 'value', role: 'input' },
      { name: '--confirmations', kind: 'value', role: 'input' },
      OUTPUT_SHORT, OUTPUT_LONG,
      { name: '--records-output', kind: 'value', role: 'output' },
      { name: '--questions-output', kind: 'value', role: 'output' },
    ],
  },
  'git-index': { positionals: [{ role: 'project' }], options: [OUTPUT_SHORT, OUTPUT_LONG] },
  'git-tree': { positionals: [{ role: 'project' }, { role: 'plain' }], options: [OUTPUT_SHORT, OUTPUT_LONG] },
  'git-file': {
    positionals: [{ role: 'project' }, { role: 'plain' }, { role: 'repoPath' }],
    options: [
      { name: '--lines', kind: 'value' }, { name: '--bytes', kind: 'value' },
      OUTPUT_SHORT, OUTPUT_LONG,
    ],
  },
  'git-worktree': {
    positionals: [{ role: 'project' }],
    options: [{ name: '--max-chars', kind: 'value' }, OUTPUT_SHORT, OUTPUT_LONG],
  },
  'git-evidence': {
    positionals: [{ role: 'project' }, { role: 'plain' }],
    options: [
      { name: '--path', kind: 'value', role: 'input' },
      { name: '--max-chars', kind: 'value' },
      { name: '-o', kind: 'value', role: 'output' },
      { name: '--output-dir', kind: 'value', role: 'output' },
    ],
  },
  'verify-narrative': { positionals: [{ role: 'input' }, { role: 'input' }], options: [] },
  'render-narrative': {
    positionals: [{ role: 'input' }, { role: 'input' }],
    options: [{ name: '--variant', kind: 'value' }, OUTPUT_SHORT, OUTPUT_LONG],
  },
}

function canonicalPath(value: string): string {
  const resolved = resolve(value)
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

function pathInside(scope: string, target: string): boolean {
  const scopeAbs = canonicalPath(scope)
  const targetAbs = canonicalPath(target)
  if (targetAbs === scopeAbs) return true
  return targetAbs.startsWith(scopeAbs.endsWith(sep) ? scopeAbs : `${scopeAbs}${sep}`)
}

// Resolve the real target of a path, walking up to the deepest existing
// ancestor. Lexical path.resolve() cannot see Windows junctions or symlinks;
// this exposes them so an output like .learntrace/x cannot silently land in an
// external directory that was never approved.
function realTargetOf(value: string): string {
  const abs = resolve(value)
  try { return canonicalPath(realpathSync.native(abs)) } catch { /* walk up */ }
  const tail: string[] = []
  let head = abs
  for (;;) {
    const parent = dirname(head)
    if (parent === head) return canonicalPath(abs)
    tail.unshift(basename(head))
    head = parent
    try { return canonicalPath(resolve(realpathSync.native(head), ...tail)) } catch { /* keep walking */ }
  }
}

function parseLearntraceArgs(subcommand: string, args: readonly string[]): { ok: boolean; reason?: string; parsed?: ParsedCommand } {
  const grammar = GRAMMARS[subcommand]
  if (!grammar) return { ok: false, reason: `不支持的子命令：${subcommand}` }
  const optionMap = new Map(grammar.options.map(option => [option.name, option]))
  const positionals: string[] = []
  const parsed: ParsedCommand = { repoPaths: [], inputs: [], outputs: [] }
  let afterDash = false
  for (let i = 1; i < args.length; i += 1) {
    const token = args[i]!
    if (!afterDash && token === '--') { afterDash = true; continue }
    if (!afterDash && token.startsWith('-') && token !== '-') {
      const eq = token.indexOf('=')
      const name = eq === -1 ? token : token.slice(0, eq)
      if (name === '-h' || name === '--help') continue
      const option = optionMap.get(name)
      if (!option) return { ok: false, reason: `无法解析的选项：${token}` }
      if (option.kind === 'flag') continue
      const value = eq === -1 ? args[i + 1] : token.slice(eq + 1)
      if (value === undefined || value.startsWith('-')) return { ok: false, reason: `选项 ${name} 缺少取值。` }
      if (eq === -1) i += 1
      if (option.role === 'input') parsed.inputs.push({ label: name, value })
      else if (option.role === 'output') parsed.outputs.push({ label: name, value })
      continue
    }
    positionals.push(token)
  }
  const required = grammar.positionals.filter(positional => !positional.optional).length
  if (positionals.length < required) return { ok: false, reason: '缺少位置参数（项目目录或路径）。' }
  if (positionals.length > grammar.positionals.length) {
    return { ok: false, reason: `多余的位置参数：${positionals[grammar.positionals.length]}` }
  }
  for (let index = 0; index < positionals.length; index += 1) {
    const spec = grammar.positionals[index]!
    const value = positionals[index]!
    if (spec.role === 'project') parsed.project = value
    else if (spec.role === 'repoPath') parsed.repoPaths.push(value)
    else if (spec.role === 'input') parsed.inputs.push({ label: '位置路径', value })
  }
  return { ok: true, parsed }
}

// Default outputs, when the corresponding explicit output option is absent,
// mirror the CLI: archive writes <project>/learning-record.md; parse/run and the
// git evidence commands write below <project>/.learntrace.
const ARTIFACT_WRITING_COMMANDS = new Set(['parse', 'run', 'git-index', 'git-tree', 'git-file', 'git-worktree', 'git-evidence'])

function scopeViolation(command: CommandSpec, cwd: string): string | undefined {
  const { args } = command
  const subcommand = args[0] ?? ''
  const parsedResult = parseLearntraceArgs(subcommand, args)
  if (!parsedResult.ok) return parsedResult.reason
  const parsed = parsedResult.parsed!
  const scopeLex = canonicalPath(resolve(cwd))
  const scopeReal = realTargetOf(cwd)
  const artifactLex = canonicalPath(join(cwd, '.learntrace'))
  const lex = (value: string) => canonicalPath(resolve(cwd, value))
  const real = (value: string) => realTargetOf(resolve(cwd, value))

  const project = parsed.project ?? (subcommand === 'archive' ? '.' : undefined)
  if (project !== undefined) {
    if (subcommand === 'archive') {
      if (!pathInside(scopeLex, lex(project)) || !pathInside(scopeReal, real(project))) {
        return `archive 记录目录超出当前项目：${project}`
      }
    } else if (real(project) !== scopeReal) {
      return `项目目录超出当前会话：${project}`
    }
  }
  for (const repoPath of parsed.repoPaths) {
    if (!pathInside(scopeLex, lex(repoPath)) || !pathInside(scopeReal, real(repoPath))) {
      return `git-file 路径超出当前项目：${repoPath}`
    }
  }
  for (const bound of parsed.inputs) {
    if (!pathInside(scopeLex, lex(bound.value)) || !pathInside(scopeReal, real(bound.value))) {
      return `${bound.label} 指向当前项目之外：${bound.value}`
    }
  }
  for (const bound of parsed.outputs) {
    if (!pathInside(artifactLex, lex(bound.value))) {
      return `输出路径超出会话产物目录（.learntrace）：${bound.value}`
    }
    if (!pathInside(scopeReal, real(bound.value))) {
      return `输出路径经链接解析后超出当前项目：${bound.value}`
    }
  }
  const hasOutput = parsed.outputs.length > 0
  const artifactRoot = resolve(cwd, '.learntrace')
  const implied: string[] = []
  if (subcommand === 'archive') {
    if (!hasOutput) implied.push(resolve(cwd, project ?? '.', 'learning-record.md'))
  } else if (ARTIFACT_WRITING_COMMANDS.has(subcommand)) {
    if (subcommand === 'parse') {
      if (!hasOutput) implied.push(resolve(artifactRoot, 'task2-result.json'))
    } else if (subcommand === 'git-index') {
      if (!hasOutput) implied.push(resolve(artifactRoot, 'evidence', 'git', 'history.jsonl'))
    } else if (subcommand === 'git-worktree') {
      if (!hasOutput) implied.push(resolve(artifactRoot, 'evidence', 'git', 'worktree.json'))
    } else {
      // run always writes below .learntrace; git-tree/git-file/git-evidence use
      // dynamic default names under .learntrace, so validate the directory.
      if (subcommand !== 'run' || !hasOutput) implied.push(artifactRoot)
      if (subcommand === 'run' && !hasOutput) implied.push(resolve(artifactRoot, 'task2-result.json'))
    }
  }
  for (const path of implied) {
    if (!pathInside(scopeReal, realTargetOf(path))) {
      return `默认输出经链接解析后超出当前项目：${path}`
    }
  }
  return undefined
}

export function classifyCommand(command: CommandSpec, cwd?: string): { verdict: GuardVerdict; reason?: string } {
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
      if (cwd !== undefined) {
        const violation = scopeViolation(command, cwd)
        if (violation !== undefined) return { verdict: 'approve', reason: violation }
      }
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
      const classified = classifyCommand(command, cwd)
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
      const stopNote = result.stopFailed
        ? '；注意：未能确认其进程树已完全终止（可能被系统权限或安全软件拦截），如有残留进程请手动结束'
        : '，命令及其进程树已终止'
      if (result.truncated) throw new Error(`输出已截断（stdout/stderr 合计超过64 KiB）${stopNote}；请缩小范围或分页。\n${result.output}`)
      if (result.timedOut) throw new Error(`命令超时${stopNote}。\n${result.output}`)
      if (result.exitCode !== 0) throw new Error(`命令执行失败（退出码 ${result.exitCode ?? '未知'}）：${result.output}`)
      return { content: [{ type: 'text', text: result.output || '命令已完成，无输出。' }], details: { approved: true, exitCode: result.exitCode, truncated: false } }
    },
  })
}
