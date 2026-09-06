import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Artifact, EvidenceReadback, Session } from '../types'
import { MarkdownView } from './MarkdownView'

function ReportContent({ sessionId, artifact }: { sessionId: string; artifact: Artifact }) {
  const requestKey = `${sessionId}:${artifact.path}:${artifact.fingerprint}`
  const [result, setResult] = useState({ key: '', markdown: '', error: '' })
  const [evidence, setEvidence] = useState<{ loading: boolean; item: EvidenceReadback | null; error: string }>({ loading: false, item: null, error: '' })

  const openEvidence = (citationId: string) => {
    setEvidence({ loading: true, item: null, error: '' })
    void api.evidence(sessionId, citationId)
      .then(item => setEvidence({ loading: false, item, error: '' }))
      .catch(reason => setEvidence({ loading: false, item: null, error: reason instanceof Error ? reason.message : String(reason) }))
  }

  useEffect(() => {
    const controller = new AbortController()
    void fetch(`/api/v1/sessions/${sessionId}/artifacts/${artifact.path}`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`报告读取失败（HTTP ${response.status}）`)
        return response.text()
      })
      .then(markdown => setResult({ key: requestKey, markdown, error: '' }))
      .catch(reason => {
        if (reason instanceof Error && reason.name !== 'AbortError') {
          setResult({ key: requestKey, markdown: '', error: reason.message })
        }
      })
    return () => controller.abort()
  }, [sessionId, artifact.path, artifact.fingerprint, requestKey])

  if (result.key !== requestKey) return <div className="report-loading">正在读取报告…</div>
  if (result.error) return <div className="failure">{result.error}</div>
  return <><article className="report"><MarkdownView onCitation={openEvidence}>{result.markdown}</MarkdownView></article>
    {(evidence.loading || evidence.item || evidence.error) && <aside className="evidence-readback" aria-live="polite">
      <header><div><span className="eyebrow">原始证据</span><strong>{evidence.item?.citation_id ?? '正在定位…'}</strong></div><button className="icon-button" onClick={() => setEvidence({ loading: false, item: null, error: '' })}>×</button></header>
      {evidence.loading && <p>正在从本地审计档案回读…</p>}
      {evidence.error && <p className="failure">{evidence.error}</p>}
      {evidence.item && <><p className="evidence-source">来源：{evidence.item.source}</p><pre>{JSON.stringify(evidence.item.record, null, 2)}</pre><small>需要查看源码时，可根据记录中的 revision、path 和行号继续回读。</small></>}
    </aside>}
  </>
}

export function ReportPanel({ session, artifacts, onClose }: {
  session: Session | null
  artifacts: Artifact[]
  onClose: () => void
}) {
  const final = artifacts.find(item => item.kind === 'final_report' && item.status === 'verified')
  const invalid = artifacts.find(item => item.kind === 'final_report' && item.status === 'invalid')
  const working = artifacts.find(item => item.kind === 'working_report')
  const intermediate = artifacts.find(item => item.kind === 'intermediate_record' && item.status !== 'preexisting')
  const visible = final ?? working ?? intermediate
  const title = final ? '学习档案' : working ? '正在形成的档案' : intermediate ? '学习记录' : '报告将在这里出现'

  return <aside className="evidence-panel">
    <header className="report-header">
      <div><span className="eyebrow">LearnTrace Report</span><h2>{title}</h2></div>
      <div className="report-header-actions">{visible && <span className={`report-state ${final ? 'verified' : ''}`}>{final ? '已校验' : '持续更新'}</span>}<button className="icon-button" onClick={onClose} aria-label="收起报告">×</button></div>
    </header>
    {invalid?.details.violations?.length && <div className="failure">{invalid.details.violations.join('；')}</div>}
    {session && visible
      ? <ReportContent sessionId={session.id} artifact={visible} />
      : <div className="report-placeholder"><span>⌁</span><h3>调查尚未开始</h3><p>报告会随着证据调查逐步呈现，不需要等待最后一次性打开文件。</p></div>}
    {!!artifacts.length && <details className="artifact-files">
      <summary>报告文件与审计材料</summary>
      <div className="artifact-list">{artifacts.map(item => <a key={item.path} href={`/api/v1/sessions/${session?.id}/artifacts/${item.path}`} target="_blank" rel="noreferrer">
        <span>{item.kind === 'intermediate_record' ? '中间记录' : item.kind === 'audit_archive' ? '审计档案' : item.kind === 'pending_questions' ? '待确认内容' : item.kind === 'final_report' ? '最终档案' : '分析产物'}</span>
        <small>{item.path} · {Math.max(1, Math.ceil(item.size / 1024))} KB{item.status === 'preexisting' ? ' · 分析前已有' : ''}</small>
      </a>)}</div>
    </details>}
  </aside>
}
