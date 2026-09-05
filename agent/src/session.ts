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
import { createCommandTool } from './command-guard.js'
import { createArtifactTool } from './artifact-tool.js'
import { UIEventProjector } from './ui-events.js'
import { createQuestionTool, QuestionBridge } from './question-tool.js'

type Publish = (event: Record<string, unknown>) => void

export class LearnTraceSession {
  private session: AgentSession | undefined
  private readonly questions: QuestionBridge
  private unsubscribe: (() => void) | undefined
  private readonly uiEvents = new UIEventProjector()

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
    }, { projectTrusted: false })
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
      tools: ['read', 'grep', 'find', 'ls', 'run_command', 'write_artifact', 'request_user_input'],
      customTools: [createCommandTool(this.questions, this.cwd), createArtifactTool(this.cwd, this.id), createQuestionTool(this.questions)],
    })
    this.session = created.session
    for (const name of ['run_command', 'write_artifact', 'request_user_input']) {
      if (!this.session.getActiveToolNames().includes(name)) throw new Error(`Agent 必需工具未启用：${name}`)
    }
    this.session.setSessionName('LearnTrace 分析')
    this.unsubscribe = this.session.subscribe(event => this.onEvent(event))
    this.publish({
      type: 'session_ready',
      resumed: resume,
      model: { provider: model.provider, id: model.id, name: model.name },
      thinking_level: this.session.thinkingLevel,
      thinking_options: this.session.getAvailableThinkingLevels(),
      active_tools: this.session.getActiveToolNames(),
    })
  }

  private onEvent(event: AgentSessionEvent): void {
    for (const update of this.uiEvents.project(event)) this.publish(update)
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
