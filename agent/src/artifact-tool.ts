import { lstat, mkdir, realpath, rename, unlink, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { defineTool } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'

const filenames = ['narrative-payload.json', 'student-confirmations.json'] as const
const schema = Type.Object({
  filename: Type.Union(filenames.map(name => Type.Literal(name))),
  content: Type.String({ description: '完整 JSON 文本；学生陈述只能使用用户原话' }),
})

export function createArtifactTool(cwd: string, sessionId: string) {
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) throw new Error('无效的会话目录。')
  return defineTool<typeof schema, { path: string }>({
    name: 'write_artifact',
    label: '保存报告材料',
    description: '只在本次会话产物目录保存叙事 JSON 或学生确认 JSON，不修改项目源码。返回可交给 LearnTrace CLI 的绝对路径。',
    promptSnippet: '使用 write_artifact 保存 narrative-payload.json 或 student-confirmations.json',
    promptGuidelines: [
      '先读取 Skill 的 narrative-payload.md 契约，再保存完整叙事 JSON；可再次调用来修正同一文件。',
      '最终 Markdown 必须由 run_command 调用 learntrace verify-narrative 和 render-narrative 校验、生成。',
      '学生确认只能记录用户实际回答；缺失的反思保持未记录，生成 working 报告。',
    ],
    parameters: schema,
    async execute(_id, params, signal) {
      if (!filenames.includes(params.filename)) throw new Error('只能写入本次会话的报告材料。')
      JSON.parse(params.content)
      signal?.throwIfAborted()
      // Reject linked directories before descending, including Windows junctions.
      let directory = await realpath(cwd)
      for (const part of ['.learntrace', 'ui-sessions', sessionId]) {
        directory = join(directory, part)
        await mkdir(directory).catch(error => {
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
        })
        const info = await lstat(directory)
        if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('产物目录不能是链接或普通文件。')
      }
      const target = join(directory, params.filename)
      const temporary = join(directory, `.${randomUUID()}.tmp`)
      try {
        await writeFile(temporary, params.content + '\n', { encoding: 'utf8', flag: 'wx', ...(signal ? { signal } : {}) })
        signal?.throwIfAborted()
        await rename(temporary, target)
      } finally {
        await unlink(temporary).catch(error => {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
        })
      }
      return { content: [{ type: 'text', text: `已保存：${target}` }], details: { path: target } }
    },
  })
}
