import type { Session } from '../types'

const stateLabel: Record<string, string> = {
  created: '正在启动',
  ready: '可以继续',
  running: '正在分析',
  failed: '需要处理',
  cancelled: '已暂停',
  interrupted: '已中断',
  archived: '仅可查看',
}

export function Sidebar({ open, sessions, activeId, onToggle, onNew, onSelect, onDelete }: {
  open: boolean
  sessions: Session[]
  activeId?: string
  onToggle: () => void
  onNew: () => void
  onSelect: (session: Session) => void
  onDelete: (id: string) => void
}) {
  return <nav className={`sidebar ${open ? '' : 'collapsed'}`}>
    <div className="brand-stack">
      <div className="brand"><span className="logo">L</span><span>LearnTrace</span></div>
      <small>从真实过程，看见学习</small>
    </div>
    <button className="sidebar-toggle" onClick={onToggle} aria-label={open ? '收起会话栏' : '展开会话栏'}>{open ? '‹' : '›'}</button>
    <div className="sidebar-content">
      <button className="new" onClick={onNew}>＋ 打开项目</button>
      <span className="nav-label">最近会话</span>
      {sessions.map(item => <div key={item.id} className={`session-link ${activeId === item.id ? 'active' : ''}`}>
        <button className="session-link-main" onClick={() => onSelect(item)}>
          <strong>{item.title}</strong>
          <small>{stateLabel[item.state] ?? item.state}</small>
        </button>
        <button className="session-delete" onClick={() => onDelete(item.id)} aria-label={`删除会话 ${item.title}`} title="删除会话">×</button>
      </div>)}
      <div className="runtime-badge"><span /><div><strong>LearnTrace Agent</strong><small>Pi SDK 内置引擎</small></div></div>
    </div>
  </nav>
}
