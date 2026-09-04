import { ConversationSources } from './ConversationSources'
import type { ConversationSource, ConversationSourceType } from '../types'

export function ProjectLauncher({ projectDir, directoryHelp, hasSavedKey, creating, error, conversationSources, onProjectChange, onToggleHelp, onChooseDirectory, onSelectConversationSources, onAddConversationPath, onRemoveConversationSource, onOpen, onModelSettings }: {
  projectDir: string
  directoryHelp: boolean
  hasSavedKey: boolean
  creating: boolean
  error: string
  conversationSources: ConversationSource[]
  onProjectChange: (path: string) => void
  onToggleHelp: () => void
  onChooseDirectory: () => void
  onSelectConversationSources: (sourceType: ConversationSourceType) => Promise<void>
  onAddConversationPath: (sourceType: ConversationSourceType, path: string) => void
  onRemoveConversationSource: (path: string) => void
  onOpen: () => void
  onModelSettings: () => void
}) {
  return <section className="main-panel project-setup-panel">
    <header className="topbar">
      <div><span className="eyebrow">新的项目</span><strong>准备开始</strong></div>
      <button className="model-settings-button" onClick={onModelSettings}>模型设置</button>
    </header>
    <section className="project-setup">
      <span className="eyebrow">项目目录</span>
      <h1>选择一个项目，开始对话。</h1>
      <p>打开项目不会立即读取内容或调用模型。进入后，由你的第一条消息决定要调查什么。</p>
      <form className="project-entry" onSubmit={event => { event.preventDefault(); onOpen() }}>
        <label>
          <span>本地项目目录 <button type="button" className="help-button" onClick={onToggleHelp} aria-expanded={directoryHelp}>?</button></span>
          <div className="path-input"><input autoFocus value={projectDir} onChange={event => onProjectChange(event.target.value)} placeholder="选择或粘贴本地项目路径" /><button type="button" onClick={onChooseDirectory}>选择…</button></div>
          {directoryHelp && <small className="directory-help">选择包含代码和 Git 历史的本地项目根目录。选择操作只返回路径；打开项目后也不会自动读取内容。</small>}
        </label>
        <button className="primary enter" disabled={creating}>{creating ? '正在打开…' : '打开项目'} <span>→</span></button>
      </form>
      <ConversationSources
        sources={conversationSources} busy={creating}
        onSelect={onSelectConversationSources} onAddPath={onAddConversationPath}
        onRemove={onRemoveConversationSource}
      />
      <button className={`model-entry ${hasSavedKey ? 'configured' : ''}`} onClick={onModelSettings}><span>{hasSavedKey ? '模型配置已保存' : '配置模型 API'}</span><small>{hasSavedKey ? '首次开始对话时验证连接 · →' : '开始对话前需要配置 · →'}</small></button>
      {error && <p className="failure">{error}</p>}
    </section>
  </section>
}
