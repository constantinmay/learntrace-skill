import { defineTool } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'

export type UserQuestion = {
  requestId: string
  question: string
  options: string[]
  allowMultiple: boolean
  allowText: boolean
  skippable: boolean
  reason?: string
}

type PendingQuestion = {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
}

export class QuestionBridge {
  private readonly pending = new Map<string, PendingQuestion>()

  constructor(private readonly publish: (question: UserQuestion) => void) {}

  ask(question: Omit<UserQuestion, 'requestId'>, signal?: AbortSignal): Promise<unknown> {
    const requestId = crypto.randomUUID()
    return new Promise((resolve, reject) => {
      const abort = () => {
        this.pending.delete(requestId)
        reject(new Error('用户问题已取消。'))
      }
      if (signal?.aborted) return abort()
      signal?.addEventListener('abort', abort, { once: true })
      this.pending.set(requestId, {
        resolve: value => {
          signal?.removeEventListener('abort', abort)
          resolve(value)
        },
        reject,
      })
      this.publish({ requestId, ...question })
    })
  }

  answer(requestId: string, value: unknown): boolean {
    const item = this.pending.get(requestId)
    if (!item) return false
    this.pending.delete(requestId)
    item.resolve(value)
    return true
  }

  close(): void {
    for (const item of this.pending.values()) item.reject(new Error('Agent 会话已关闭。'))
    this.pending.clear()
  }
}

const schema = Type.Object({
  question: Type.String({ description: '一个清晰、独立的问题' }),
  options: Type.Optional(Type.Array(Type.String(), { description: '已知的回答选项' })),
  allowMultiple: Type.Optional(Type.Boolean()),
  allowText: Type.Optional(Type.Boolean()),
  skippable: Type.Optional(Type.Boolean()),
  reason: Type.Optional(Type.String({ description: '为什么需要用户回答' })),
})

export function createQuestionTool(bridge: QuestionBridge) {
  return defineTool<typeof schema, { skipped: boolean; answer?: unknown }>({
    name: 'request_user_input',
    label: '询问用户',
    description: '当 LearnTrace 需要授权、澄清或反思时，向用户提出一个问题并等待回答。',
    promptSnippet: '向用户提出一个聚焦的问题并等待回答',
    promptGuidelines: [
      '涉及 LearnTrace 授权、澄清和反思时使用 request_user_input，不要把整份问卷写进普通对话。',
      '每次只问一个决定；答案已知时提供简短选项。',
    ],
    parameters: schema,
    async execute(_toolCallId, params, signal) {
      const answer = await bridge.ask({
        question: params.question,
        options: params.options ?? [],
        allowMultiple: params.allowMultiple ?? false,
        allowText: params.allowText ?? !params.options?.length,
        skippable: params.skippable ?? true,
        ...(params.reason ? { reason: params.reason } : {}),
      }, signal)
      if (answer === null || answer === undefined || answer === '') {
        return { content: [{ type: 'text', text: '用户选择暂不回答。' }], details: { skipped: true } }
      }
      const text = Array.isArray(answer) ? answer.map(String).join('；') : String(answer)
      return {
        content: [{ type: 'text', text: `用户原话：${text}` }],
        details: { skipped: false, answer },
      }
    },
  })
}
