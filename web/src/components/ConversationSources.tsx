import { useState } from 'react'
import type { ConversationSource, ConversationSourceType } from '../types'

const sourceNames: Record<ConversationSourceType, string> = {
  opencode: 'OpenCode',
  'claude-code': 'Claude Code',
  codex: 'Codex',
}

export function ConversationSources({ sources, busy, onSelect, onAddPath, onRemove }: {
  sources: ConversationSource[]
  busy: boolean
  onSelect: (sourceType: ConversationSourceType) => Promise<void>
  onAddPath: (sourceType: ConversationSourceType, path: string) => void
  onRemove: (path: string) => void
}) {
  const [sourceType, setSourceType] = useState<ConversationSourceType>('opencode')
  const [path, setPath] = useState('')

  const addPath = () => {
    const value = path.trim()
    if (!value) return
    onAddPath(sourceType, value)
    setPath('')
  }

  return <section className="conversation-sources">
    <header>
      <div><span className="eyebrow">可选资料</span><h2>AI 协作对话</h2></div>
      <span>{sources.length ? `已添加 ${sources.length} 份` : '未添加'}</span>
    </header>
    <p>添加你明确愿意用于本次分析的历史会话导出。LearnTrace 不会扫描 Agent 的本地目录，也不会导入当前产品对话。</p>
    <div className="source-picker">
      <select value={sourceType} onChange={event => setSourceType(event.target.value as ConversationSourceType)}>
        {Object.entries(sourceNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
      </select>
      <input value={path} onChange={event => setPath(event.target.value)} placeholder="粘贴 .json 或 .jsonl 导出路径" />
      <button type="button" onClick={addPath} disabled={!path.trim()}>添加</button>
      <button type="button" onClick={() => void onSelect(sourceType)} disabled={busy}>选择文件…</button>
    </div>
    {!!sources.length && <div className="source-list">{sources.map(source => <div key={`${source.source_type}:${source.path}`}>
      <span>{sourceNames[source.source_type]}</span>
      <p title={source.path}>{source.path}</p>
      <button type="button" onClick={() => onRemove(source.path)} aria-label={`移除 ${source.path}`}>×</button>
    </div>)}</div>}
  </section>
}
