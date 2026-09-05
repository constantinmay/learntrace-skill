import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { api } from './api'
import { ChatWorkspace } from './components/ChatWorkspace'
import { ModelSettings } from './components/ModelSettings'
import { PaneHandle } from './components/PaneHandle'
import { ProjectLauncher } from './components/ProjectLauncher'
import { ReportPanel } from './components/ReportPanel'
import { SectionErrorBoundary } from './components/SectionErrorBoundary'
import { Sidebar } from './components/Sidebar'
import { UsageGuide } from './components/UsageGuide'
import { useSessionData } from './useSessionData'
import type { ConversationSource, ConversationSourceType, ModelConnectionStatus, RuntimeConfig, Session } from './types'

const defaultRuntime: RuntimeConfig = {
  base_url: '', api_key: '', model_id: '', api_protocol: '', thinking_level: 'medium',
}

export default function App() {
  const [loading, setLoading] = useState(true)
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(defaultRuntime)
  const [hasSavedKey, setHasSavedKey] = useState(false)
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)
  const [modelError, setModelError] = useState('')
  const [modelStatus, setModelStatus] = useState<ModelConnectionStatus>('unverified')
  const [testingModel, setTestingModel] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [sidebarWidth, setSidebarWidth] = useState(268)
  const [reportWidth, setReportWidth] = useState(560)
  const [projectDir, setProjectDir] = useState('')
  const [conversationSources, setConversationSources] = useState<ConversationSource[]>([])
  const [directoryHelp, setDirectoryHelp] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState<Session | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const updateSession = useCallback((session: Session) => {
    setActive(current => current?.id === session.id ? session : current)
    setSessions(items => items.map(item => item.id === session.id ? session : item))
  }, [])
  const patchSession = useCallback((id: string, changes: Partial<Session>) => {
    setActive(current => current?.id === id ? { ...current, ...changes } : current)
    setSessions(items => items.map(item => item.id === id ? { ...item, ...changes } : item))
  }, [])
  const revealReport = useCallback(() => setReportOpen(true), [])
  const reportSessionError = useCallback((message: string) => setError(message), [])
  const { events, artifacts, pending, configOptions, setData, connection } = useSessionData(
    active, patchSession, updateSession, revealReport, reportSessionError,
  )

  const openModelSettings = () => {
    setModelError('')
    setModelSettingsOpen(true)
  }

  useEffect(() => {
    const url = new URL(window.location.href)
    const launchToken = url.searchParams.get('launch_token')
    url.searchParams.delete('launch_token')
    history.replaceState({}, '', url.pathname + url.search)
    let disposed = false
    const bootstrap = launchToken ? api.bootstrap(launchToken).catch(() => undefined) : Promise.resolve(undefined)
    void bootstrap.then(() => Promise.all([api.modelConfig(), api.sessions()]))
      .then(([saved, sessionItems]) => {
        if (disposed) return
        setRuntimeConfig(value => ({ ...value, ...saved, api_key: '' }))
        setHasSavedKey(saved.has_api_key)
        if (saved.credential_error) setError(saved.credential_error)
        setSessions(sessionItems)
        const remembered = window.localStorage.getItem('learntrace.active-session')
        const restored = sessionItems.find(item => item.id === remembered)
        if (restored?.mode === 'live') {
          setActive(restored)
          setProjectDir(restored.project)
        } else if (restored) {
          setProjectDir(restored.project)
          window.localStorage.removeItem('learntrace.active-session')
        }
        setReportOpen(window.localStorage.getItem('learntrace.report-open') === '1')
      })
      .catch(reason => {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [])

  useEffect(() => {
    window.localStorage.setItem('learntrace.report-open', reportOpen ? '1' : '0')
  }, [reportOpen])

  const chooseSession = (session: Session | null) => {
    setError('')
    setActive(session)
    if (session) {
      setProjectDir(session.project)
      window.localStorage.setItem('learntrace.active-session', session.id)
    } else {
      window.localStorage.removeItem('learntrace.active-session')
      setReportOpen(false)
      setConversationSources([])
    }
  }

  const deleteSession = async (id: string) => {
    if (!confirm('删除这次会话及其会话目录中的产物？')) return
    try {
      await api.deleteSession(id)
      setSessions(items => items.filter(item => item.id !== id))
      if (active?.id === id) chooseSession(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const saveModelConfig = async () => {
    setSaving(true)
    setModelError('')
    try {
      const saved = await api.saveModelConfig(runtimeConfig)
      setHasSavedKey(saved.has_api_key)
      setRuntimeConfig(value => ({ ...value, api_key: '' }))
      setModelStatus('unverified')
      setModelSettingsOpen(false)
    } catch (reason) {
      setModelError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const probeModelConfig = async () => {
    setTestingModel(true)
    setModelError('')
    setModelStatus('testing')
    try {
      await api.probeModelConfig(runtimeConfig)
      setModelStatus('connected')
    } catch (reason) {
      setModelStatus('failed')
      setModelError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setTestingModel(false)
    }
  }

  const createSession = async () => {
    const selectedProject = projectDir.trim()
    if (!selectedProject) {
      setError('请先输入要分析的本地项目目录。')
      return
    }
    if (!hasSavedKey || !runtimeConfig.base_url || !runtimeConfig.model_id || !runtimeConfig.api_protocol) {
      setError('请先配置模型连接。')
      openModelSettings()
      return
    }
    setSaving(true)
    setError('')
    try {
      const created = await api.createSession(selectedProject, conversationSources)
      setSessions(items => [created, ...items])
      chooseSession(created)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const chooseProjectDirectory = async () => {
    setError('')
    try {
      const result = await api.selectProject()
      if (result.selected) setProjectDir(result.path)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const selectConversationSources = async (sourceType: ConversationSourceType) => {
    setError('')
    try {
      const selected = await api.selectConversationSources(sourceType)
      if (selected.selected) {
        setConversationSources(current => {
          const known = new Set(current.map(item => item.path.toLocaleLowerCase()))
          return [...current, ...selected.sources.filter(item => !known.has(item.path.toLocaleLowerCase()))]
        })
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const send = async (text: string) => {
    if (!active?.can_send) throw new Error('这是一条只读历史记录。')
    setError('')
    try {
      await api.send(active.id, text)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      setError(message)
      throw reason
    }
  }

  const resumeSession = async () => {
    if (!active) return
    setSaving(true)
    setError('')
    try {
      const resumed = await api.resumeSession(active.id)
      setSessions(items => items.map(item => item.id === resumed.id ? resumed : item))
      chooseSession(resumed)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const answer = async (requestId: string, value: unknown) => {
    if (active?.can_send) await api.answer(active.id, requestId, value)
  }

  const configure = async (id: string, value: string | boolean) => {
    if (!active?.can_send) return
    await api.configure(active.id, id, value)
    setData(current => ({
      ...current,
      configOptions: current.configOptions.map(item => item.id === id ? { ...item, currentValue: value } : item),
    }))
  }

  if (loading) return <main className="launch-shell">
    <div className="brand"><span className="logo">L</span><span>LearnTrace</span></div>
    <section className="agent-detection"><div className="detection-pulse" /><span className="eyebrow">本地环境</span><h1>正在启动 LearnTrace</h1><p>内置 Agent 正在准备，不会扫描本机的其他开发工具。</p></section>
  </main>

  const projectName = active?.project.split(/[\\/]/).filter(Boolean).at(-1) ?? '当前项目'
  const showReport = Boolean(active && reportOpen)
  const workspaceStyle = {
    '--left-pane': `${sidebarOpen ? sidebarWidth : 54}px`,
    '--report-pane': `${reportWidth}px`,
  } as CSSProperties

  return <>
  <main className={`workspace ${showReport ? 'report-open' : 'report-closed'}`} style={workspaceStyle}>
    <Sidebar
      open={sidebarOpen} sessions={sessions} activeId={active?.id}
      onToggle={() => setSidebarOpen(value => !value)}
      onNew={() => { chooseSession(null); void chooseProjectDirectory() }}
      onSelect={chooseSession} onDelete={id => void deleteSession(id)}
    />
    <PaneHandle side="left" onResize={delta => {
      if (sidebarOpen) setSidebarWidth(value => Math.max(210, Math.min(420, value + delta)))
    }} />

    {active ? <ChatWorkspace key={active.id}
      session={active} projectName={projectName} events={events} pending={pending}
      configOptions={configOptions} artifactCount={artifacts.length} connection={connection} error={error}
      reportOpen={reportOpen} onToggleReport={() => setReportOpen(value => !value)}
      onModelSettings={openModelSettings}
      onNewSession={() => {
        const project = active.project
        const needsConfiguration = ['agent_auth_required', 'agent_config_invalid', 'agent_model_invalid', 'agent_runtime_missing'].includes(active.error_code ?? '')
        chooseSession(null)
        setProjectDir(project)
        if (needsConfiguration) openModelSettings()
      }}
      onResume={() => void resumeSession()}
      onStop={async () => {
        await api.cancel(active.id)
        const fresh = await api.snapshot(active.id)
        updateSession(fresh.session)
        setData(current => current.sessionId === active.id ? { ...current, pending: fresh.pending_requests } : current)
      }}
      onDelete={() => void deleteSession(active.id)}
      onStart={() => void send('请按 LearnTrace Skill 开始分析这个项目。')}
      onRetry={() => void send('刚才的模型请求没有完成，请从中断处继续。')}
      onSend={send} onAnswer={answer} onConfigure={configure}
    /> : <ProjectLauncher
      projectDir={projectDir} directoryHelp={directoryHelp} hasSavedKey={hasSavedKey}
      creating={saving} error={error} conversationSources={conversationSources} onProjectChange={setProjectDir}
      onToggleHelp={() => setDirectoryHelp(value => !value)}
      onChooseDirectory={() => void chooseProjectDirectory()} onOpen={() => void createSession()}
      onSelectConversationSources={selectConversationSources}
      onAddConversationPath={(sourceType, path) => setConversationSources(current => [...current.filter(item => item.path.toLocaleLowerCase() !== path.toLocaleLowerCase()), { path, source_type: sourceType, authorization: 'task3_parse', state: 'authorized', size: 0 }])}
      onRemoveConversationSource={path => setConversationSources(current => current.filter(item => item.path !== path))}
      onModelSettings={openModelSettings}
    />}

    {active && reportOpen && <>
      <PaneHandle side="right" onResize={delta => setReportWidth(value => Math.max(390, Math.min(860, value - delta)))} />
      <SectionErrorBoundary key={`report-${active.id}`} title="报告">
        <ReportPanel session={active} artifacts={artifacts} onClose={() => setReportOpen(false)} />
      </SectionErrorBoundary>
    </>}

    {modelSettingsOpen && <ModelSettings
      value={runtimeConfig} hasSavedKey={hasSavedKey} saving={saving} testing={testingModel}
      status={modelStatus} error={modelError}
      onChange={value => { setRuntimeConfig(value); setModelStatus('unverified') }}
      onClose={() => setModelSettingsOpen(false)} onSave={() => void saveModelConfig()}
      onProbe={() => void probeModelConfig()}
    />}
  </main>
  <button className="guide-trigger" onClick={() => setGuideOpen(true)} aria-label="打开 LearnTrace 使用指南" title="使用指南">?</button>
  {guideOpen && <UsageGuide onClose={() => setGuideOpen(false)} />}
  </>
}
