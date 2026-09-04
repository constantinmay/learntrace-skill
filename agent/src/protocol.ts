export type ApiProtocol = 'anthropic-messages' | 'openai-completions' | 'openai-responses'

export type RuntimeConfig = {
  baseUrl: string
  apiKey: string
  modelId: string
  api: ApiProtocol
  thinkingLevel?: 'off' | 'minimal' | 'low' | 'medium' | 'high'
}

export type ServiceCommand = {
  id: string
  type: 'probe' | 'start' | 'prompt' | 'answer' | 'configure' | 'abort' | 'close'
  sessionId: string
  cwd?: string
  skillPath?: string
  sessionDir?: string
  resume?: boolean
  config?: RuntimeConfig
  text?: string
  requestId?: string
  value?: unknown
}

export type ServiceMessage =
  | { type: 'response'; id: string; success: boolean; data?: unknown; error?: string }
  | { type: 'event'; sessionId: string; event: Record<string, unknown> }

export function assertRuntimeConfig(value: RuntimeConfig): void {
  const url = new URL(value.baseUrl)
  if (url.protocol !== 'https:' && !['localhost', '127.0.0.1', '::1'].includes(url.hostname)) {
    throw new Error('API 地址必须使用 HTTPS；只有本机服务可以使用 HTTP。')
  }
  if (!value.apiKey.trim()) throw new Error('API Key 不能为空。')
  if (!value.modelId.trim()) throw new Error('模型 ID 不能为空。')
  if (!['anthropic-messages', 'openai-completions', 'openai-responses'].includes(value.api)) {
    throw new Error('不支持的 API 协议。')
  }
}
