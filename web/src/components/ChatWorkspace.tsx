import { useState, type FormEvent } from 'react'
import { Conversation } from './Conversation'
import { SectionErrorBoundary } from './SectionErrorBoundary'
import type { ConfigOption } from '../useSessionData'
import type { PendingRequest, Session, UIEvent } from '../types'

const stateLabel: Record<string, string> = {
  created: '正在启动', ready: '可以继续', running: '正在分析', failed: '需要处理',
  cancelled: '已暂停', interrupted: '已中断', archived: '仅可查看',
}

function QuestionCard({ request, onAnswer }: {
  request: PendingRequest
  onAnswer: (requestId: string, answer: unknown) => Promise<void>
}) {
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const payload = request.payload
  const question = String(payload.question ?? '需要你的确认')
  const reason = payload.reason ? String(payload.reason) : ''
  const options = Array.isArray(payload.options) ? payload.options.map(String) : []
  const allowText = payload.allow_text !== false
  const multiline = Boolean(payload.multiline)

  const submit = async (answer: unknown) => {
    if (submitting) return
    setSubmitting(true)
    try {
      await onAnswer(request.request_id, answer)
    } finally {
      setSubmitting(false)
    }
  }

  return <section className="question-card" aria-live="polite">
    <div className="question-heading">
      <span className="question-mark">?</span>
      <div><span className="eyebrow">需要你的决定</span><h3>{question}</h3></div>
    </div>
    {reason && reason !== question && <p>{reason}</p>}
    {!!options.length && <div className="choice-list">
      {options.map(option => <button disabled={submitting} key={option} onClick={() => void submit(option)}>{option}</button>)}
    </div>}
    {allowText && <form className="answer-box" onSubmit={event => { event.preventDefault(); if (value.trim()) void submit(value.trim()) }}>
      {multiline
        ? <textarea rows={4} value={value} onChange={event => setValue(event.target.value)} placeholder="用自己的话写下来…" />
        : <input value={value} onChange={event => setValue(event.target.value)} placeholder="或者输入你的回答…" />}
      <button disabled={submitting || !value.trim()}>提交</button>
    </form>}
  </section>
}

function SessionConfig({ options, onConfigure }: {
  options: ConfigOption[]
  onConfigure: (id: string, value: string | boolean) => Promise<void>
}) {
  return <div className="session-config">{options.map(option => <label key={option.id}>
    <span>{option.name ?? option.id}</span>
    {option.type === 'boolean'
      ? <input type="checkbox" checked={Boolean(option.currentValue)} onChange={event => void onConfigure(option.id, event.target.checked)} />
      : <select value={String(option.currentValue ?? '')} onChange={event => void onConfigure(option.id, event.target.value)}>
        {(option.options ?? []).map(item => <option key={item.value} value={item.value}>{item.name ?? item.value}</option>)}
      </select>}
  </label>)}</div>
}

export function ChatWorkspace({ session, projectName, events, pending, configOptions, artifactCount, connection, error, reportOpen, onToggleReport, onModelSettings, onNewSession, onResume, onDelete, onStart, onRetry, onSend, onAnswer, onConfigure }: {
  session: Session
  projectName: string
  events: UIEvent[]
  pending: PendingRequest[]
  configOptions: ConfigOption[]
  artifactCount: number
  connection: string
  error: string
  reportOpen: boolean
  onToggleReport: () => void
  onModelSettings: () => void
  onNewSession: () => void
  onResume: () => void
  onDelete: () => void
  onStart: () => void
  onRetry: () => void
  onSend: (text: string) => Promise<void>
  onAnswer: (requestId: string, answer: unknown) => Promise<void>
  onConfigure: (id: string, value: string | boolean) => Promise<void>
}) {
  const [message, setMessage] = useState('')
  const submitMessage = async (event: FormEvent) => {
    event.preventDefault()
    const text = message.trim()
    if (!text) return
    setMessage('')
    try {
      await onSend(text)
    } catch {
      setMessage(text)
    }
  }

  const configurationFailure = [
    'agent_auth_required', 'agent_config_invalid', 'agent_model_invalid', 'agent_runtime_missing',
  ].includes(session.error_code ?? '')
  const retryableFailure = session.state === 'failed' && !configurationFailure && session.can_send
  const canCompose = session.can_send && !configurationFailure

  return <section className="main-panel">
    <header className="topbar">
      <div><span className="eyebrow">{projectName}</span><strong>{session.title}</strong></div>
      <span className="source-status">{session.conversation_sources?.length
        ? `AI 对话资料 · ${session.conversation_sources.length} 份`
        : '仅项目资料'}</span>
      {session.can_send && <SessionConfig options={configOptions} onConfigure={onConfigure} />}
      <button className={`report-toggle ${reportOpen ? 'active' : ''}`} onClick={onToggleReport}>报告{artifactCount ? ` · ${artifactCount}` : ''}</button>
      <button className="model-settings-button" onClick={onModelSettings}>模型设置</button>
      <span className={`state ${session.state}`}><i />{stateLabel[session.state] ?? session.state}</span>
      {connection === 'disconnected' && <span className="connection-warning">实时连接中断，正在重连</span>}
      <button className="quiet-danger" onClick={onDelete}>删除</button>
    </header>
    {error && <div className="workspace-error failure" role="alert">{error}</div>}
    {session.state === 'failed' && <div className="failure-actions">
      <div><strong>{configurationFailure ? '模型配置需要调整' : '本次模型请求没有完成'}</strong><p>{session.error_message}</p></div>
      {retryableFailure && <button className="secondary" onClick={onRetry}>重新尝试</button>}
      <button className="secondary" onClick={onModelSettings}>模型设置</button>
      {configurationFailure && <button className="primary" onClick={onResume}>应用新配置并恢复</button>}
    </div>}
    <SectionErrorBoundary key={`chat-${session.id}`} title="对话">
      <Conversation events={events} onStart={onStart} readOnly={!session.can_send} />
    </SectionErrorBoundary>
    {canCompose && <div className="questions">{pending.map(item => <QuestionCard key={item.request_id} request={item} onAnswer={onAnswer} />)}</div>}
    {canCompose ? <form className="composer" onSubmit={submitMessage}>
      <textarea value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() }
      }} placeholder={session.analysis_started ? '继续追问、补充信息，或要求核对证据…' : '例如：分析这个项目的开发与学习过程…'} rows={2} />
      <button disabled={!message.trim()}>发送</button>
    </form> : !session.can_send ? <div className="history-readonly"><span>历史记录</span><p>这次会话保留用于查看。恢复会重新加载 Pi Agent 的会话记录；如果项目或会话文件已不存在，也可以基于原项目重新分析。</p><div className="history-actions"><button className="primary" onClick={onResume}>恢复并继续</button><button className="secondary" onClick={onNewSession}>新建分析</button></div></div> : null}
  </section>
}
