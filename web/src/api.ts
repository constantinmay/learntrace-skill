import type { Artifact, ConversationSource, ConversationSourceType, EvidenceReadback, RuntimeConfig, Session, SessionSnapshot, StoredRuntimeConfig, UIEvent } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } })
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail?.message ?? body.detail?.code ?? `HTTP ${response.status}`) }
  return response.json() as Promise<T>
}

export const api = {
  modelConfig: () => request<StoredRuntimeConfig>('/api/v1/model-config'),
  saveModelConfig: (config: RuntimeConfig) => request<StoredRuntimeConfig>('/api/v1/model-config', { method: 'PUT', body: JSON.stringify({ ...config, api_key: config.api_key || null }) }),
  probeModelConfig: (config: RuntimeConfig) => request<{ status: 'connected'; message: string }>('/api/v1/model-config/probe', { method: 'POST', body: JSON.stringify({ ...config, api_key: config.api_key || null }) }),
  selectProject: () => request<{ selected: boolean; path: string }>('/api/v1/projects/select', { method: 'POST' }),
  sessions: () => request<Session[]>('/api/v1/sessions'),
  snapshot: (id: string) => request<SessionSnapshot>(`/api/v1/sessions/${id}/snapshot`),
  selectConversationSources: (sourceType: ConversationSourceType) => request<{ selected: boolean; sources: ConversationSource[] }>('/api/v1/conversation-sources/select', { method: 'POST', body: JSON.stringify({ source_type: sourceType }) }),
  createSession: (projectDir: string, conversationSources: ConversationSource[]) => request<Session>('/api/v1/sessions', { method: 'POST', body: JSON.stringify({ project_dir: projectDir, conversation_sources: conversationSources.map(({ path, source_type }) => ({ path, source_type })) }) }),
  resumeSession: (id: string) => request<Session>(`/api/v1/sessions/${id}/resume`, { method: 'POST' }),
  send: (id: string, text: string) => request<{ accepted: boolean }>(`/api/v1/sessions/${id}/messages`, { method: 'POST', body: JSON.stringify({ text }) }),
  configure: (id: string, configId: string, value: string | boolean) => request<{ accepted: boolean }>(`/api/v1/sessions/${id}/config`, { method: 'POST', body: JSON.stringify({ config_id: configId, value }) }),
  answer: (id: string, requestId: string, value: unknown) => request<{ accepted: boolean }>(`/api/v1/sessions/${id}/answers/${requestId}`, { method: 'POST', body: JSON.stringify({ value }) }),
  cancel: (id: string) => request<{ cancelled: boolean }>(`/api/v1/sessions/${id}/cancel`, { method: 'POST' }),
  deleteSession: (id: string) => request<{ deleted: boolean }>(`/api/v1/sessions/${id}`, { method: 'DELETE' }),
  artifacts: (id: string) => request<Artifact[]>(`/api/v1/sessions/${id}/artifacts`),
  evidence: (id: string, citationId: string) => request<EvidenceReadback>(`/api/v1/sessions/${id}/evidence/${encodeURIComponent(citationId)}`),
  eventStream: (id: string, after: number, onEvent: (event: UIEvent) => void, onOpen?: () => void, onError?: () => void) => {
    const stream = new EventSource(`/api/v1/sessions/${id}/events?after=${after}`)
    const names = ['session_state','session_config','session_resumed','analysis_started','message_delta','message_completed','tool_started','tool_updated','tool_completed','default_permission','permission_requested','user_input_requested','user_input_completed','artifact_updated','session_completed','session_failed','request_answered','agent_update','config_updated','compatibility_diagnostic']
    names.forEach(name => stream.addEventListener(name, raw => onEvent(JSON.parse((raw as MessageEvent).data) as UIEvent)))
    stream.onopen = () => onOpen?.()
    stream.onerror = () => onError?.()
    return stream
  },
}
