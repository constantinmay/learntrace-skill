import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
  SettingsManager,
  type AgentSession,
  type AgentSessionEvent,
} from '@earendil-works/pi-coding-agent'
import { createRuntimeModel } from './model.js'
import type { RuntimeConfig } from './protocol.js'
import { createQuestionTool, QuestionBridge } from './question-tool.js'

type Publish = (event: Record<string, unknown>) => void

export class LearnTraceSession {
  private session: AgentSession | undefined
  private readonly questions: QuestionBridge
  private unsubscribe: (() => void) | undefined

  constructor(
    readonly id: string,
    private readonly cwd: string,
    private readonly skillPath: string,
    private readonly sessionDir: string,
    private readonly publish: Publish,
  ) {
    this.questions = new QuestionBridge(question => {
      this.publish({
        type: 'user_input_requested',
        request_id: question.requestId,
        question: question.question,
        options: question.options,
        allow_multiple: question.allowMultiple,
        allow_text: question.allowText,
        skippable: question.skippable,
        reason: question.reason,
      })
    })
  }

  async start(config: RuntimeConfig, resume = false): Promise<void> {
    const { runtime, model } = await createRuntimeModel(config)
    const settings = SettingsManager.inMemory({
      defaultProvider: model.provider,
      defaultModel: model.id,
      defaultThinkingLevel: config.thinkingLevel ?? 'medium',
      enableAnalytics: false,
      enableInstallTelemetry: false,
    }, { projectTrusted: true })
    const loader = new DefaultResourceLoader({
      cwd: this.cwd,
      agentDir: this.sessionDir,
      settingsManager: settings,
      additionalSkillPaths: [this.skillPath],
      noExtensions: true,
      noPromptTemplates: true,
      noThemes: true,
      noContextFiles: true,
    })
    await loader.reload()
    const loaded = loader.getSkills().skills
    if (!loaded.some(skill => skill.name === 'learntrace')) {
      throw new Error('内置 LearnTrace Skill 加载失败。')
    }
    let sessionManager: SessionManager
    if (resume) {
      const previous = (await SessionManager.list(this.cwd, this.sessionDir))
        .find(item => item.id === this.id)
      if (!previous) throw new Error('找不到可恢复的 Pi Agent 会话记录。')
      sessionManager = SessionManager.open(previous.path)
    } else {
      sessionManager = SessionManager.create(this.cwd, this.sessionDir, { id: this.id })
    }
    const created = await createAgentSession({
      cwd: this.cwd,
      agentDir: this.sessionDir,
      modelRuntime: runtime,
      model,
      thinkingLevel: config.thinkingLevel ?? 'medium',
      resourceLoader: loader,
      settingsManager: settings,
      sessionManager,
      tools: ['read', 'bash', 'grep', 'find', 'ls', 'request_user_input'],
      customTools: [createQuestionTool(this.questions)],
    })
    this.session = created.session
    this.session.setSessionName('LearnTrace 分析')
    this.unsubscribe = this.session.subscribe(event => this.onEvent(event))
    this.publish({
      type: 'session_ready',
      resumed: resume,
      model: { provider: model.provider, id: model.id, name: model.name },
      thinking_level: this.session.thinkingLevel,
      thinking_options: this.session.getAvailableThinkingLevels(),
    })
  }

  private onEvent(event: AgentSessionEvent): void {
    this.publish(event as unknown as Record<string, unknown>)
  }

  async prompt(text: string): Promise<void> {
    if (!this.session) throw new Error('Agent 会话尚未启动。')
    await this.session.prompt(text, {
      expandPromptTemplates: true,
      ...(this.session.isStreaming ? { streamingBehavior: 'steer' as const } : {}),
    })
  }

  async runSkill(instruction: string): Promise<void> {
    await this.prompt(`/skill:learntrace\n${instruction}`)
  }

  answer(requestId: string, value: unknown): void {
    if (!this.questions.answer(requestId, value)) throw new Error('问题已回答或不存在。')
  }

  setThinking(level: 'off' | 'minimal' | 'low' | 'medium' | 'high'): void {
    if (!this.session) throw new Error('Agent 会话尚未启动。')
    this.session.setThinkingLevel(level)
  }

  async abort(): Promise<void> {
    await this.session?.abort()
  }

  close(): void {
    this.questions.close()
    this.unsubscribe?.()
    this.session?.dispose()
  }
}
