import { InMemoryCredentialStore } from '@earendil-works/pi-ai'
import { ModelRuntime } from '@earendil-works/pi-coding-agent'
import type { RuntimeConfig } from './protocol.js'

export async function createRuntimeModel(config: RuntimeConfig) {
  const runtime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(),
    modelsPath: null,
    refreshOnCreate: false,
  })
  const providerId = `learntrace-${crypto.randomUUID()}`
  runtime.registerProvider(providerId, {
    name: 'LearnTrace API',
    baseUrl: config.baseUrl,
    api: config.api,
    apiKey: config.apiKey,
    authHeader: config.api !== 'anthropic-messages',
    models: [{
      id: config.modelId,
      name: config.modelId,
      reasoning: config.thinkingLevel !== undefined && config.thinkingLevel !== 'off',
      input: ['text'],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 200_000,
      maxTokens: 16_384,
      ...(config.api.startsWith('openai-') ? {
        compat: { supportsDeveloperRole: false, supportsReasoningEffort: false },
      } : {}),
    }],
  })
  const model = runtime.getModel(providerId, config.modelId)
  if (!model) throw new Error(`无法注册模型 ${config.modelId}`)
  return { runtime, model }
}

export async function probeRuntimeModel(config: RuntimeConfig): Promise<void> {
  const { runtime, model } = await createRuntimeModel(config)
  const response = await runtime.completeSimple(
    model,
    {
      systemPrompt: 'This is a connection check. Reply with OK only.',
      messages: [{ role: 'user', content: 'OK', timestamp: Date.now() }],
    },
    {
      apiKey: config.apiKey,
      maxTokens: 4,
      timeoutMs: 20_000,
      maxRetries: 0,
    },
  )
  if (response.stopReason === 'error' || response.stopReason === 'aborted') {
    throw new Error(response.errorMessage || '模型连接测试失败。')
  }
}
