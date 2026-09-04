import { useEffect, useRef } from 'react'
import { buildConversationRows, currentActivity } from '../conversation'
import type { UIEvent } from '../types'
import { MarkdownView } from './MarkdownView'

export function Conversation({ events, onStart, readOnly }: {
  events: UIEvent[]
  onStart: () => void
  readOnly: boolean
}) {
  const rows = buildConversationRows(events)
  const activity = currentActivity(events)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const marker = bottom.current
    if (marker && typeof marker.scrollIntoView === 'function') {
      marker.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [rows.length, activity])

  return <div className="conversation">
    {!rows.length && !activity && <div className="empty">
      <div className="trace-mark">⌁</div>
      <h2>从这个项目开始对话</h2>
      <p>{readOnly ? '这次历史会话没有留下可显示的对话内容。' : '由你决定要调查什么；开始后，Agent 才会按 LearnTrace Skill 进入项目。'}</p>
      {!readOnly && <button className="learntrace-start" onClick={onStart}><span>LearnTrace</span> Start <i>→</i></button>}
    </div>}
    {rows.map(row => {
      if (row.kind === 'message') return <article className={`message ${row.role}`} key={row.key}>
        <MarkdownView>{row.text}</MarkdownView>
      </article>
      if (row.event.type === 'session_failed') return <div className="failure" key={row.key}>
        {String(row.event.payload.message ?? 'Agent 运行失败')}
      </div>
      return null
    })}
    {activity && <div className="current-activity" aria-live="polite"><span />{activity}<i>…</i></div>}
    <div ref={bottom} />
  </div>
}
