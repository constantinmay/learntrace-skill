import type { ModelConnectionStatus, RuntimeConfig } from '../types'

const statusText: Record<ModelConnectionStatus, string> = {
  unverified: '尚未验证', testing: '正在验证…', connected: '连接成功', failed: '连接失败',
}

export function ModelSettings({ value, hasSavedKey, saving, testing, status, error, onChange, onClose, onSave, onProbe }: {
  value: RuntimeConfig
  hasSavedKey: boolean
  saving: boolean
  testing: boolean
  status: ModelConnectionStatus
  error: string
  onChange: (value: RuntimeConfig) => void
  onClose: () => void
  onSave: () => void
  onProbe: () => void
}) {
  const ready = Boolean(
    value.base_url.trim()
    && value.model_id.trim()
    && value.api_protocol
    && (hasSavedKey || value.api_key.trim()),
  )
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
      <header><div><span className="eyebrow">模型连接</span><h2 id="model-settings-title">接入你的 API</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭">×</button></header>
      <p>这些配置只用于启动 LearnTrace 内置 Agent。保存只检查格式；“测试连接”会产生一次极小的模型请求。</p>
      <div className={`connection-status ${status}`}><i />{statusText[status]}</div>
      <form className="api-setup" onSubmit={event => { event.preventDefault(); onSave() }}>
        <label className="wide"><span>API 地址 <abbr title="模型服务提供的基础 URL，例如 https://api.example.com">?</abbr></span><input type="url" required value={value.base_url} onChange={event => onChange({ ...value, base_url: event.target.value })} placeholder="https://…" /></label>
        <label><span>模型 ID <abbr title="模型服务中显示的完整模型名称">?</abbr></span><input required value={value.model_id} onChange={event => onChange({ ...value, model_id: event.target.value })} placeholder="输入模型 ID" /></label>
        <label><span>接口协议 <abbr title="选择模型服务实际兼容的请求格式">?</abbr></span><select required value={value.api_protocol} onChange={event => onChange({ ...value, api_protocol: event.target.value as RuntimeConfig['api_protocol'] })}><option value="" disabled>请选择</option><option value="anthropic-messages">Anthropic Messages</option><option value="openai-completions">OpenAI Chat Completions</option><option value="openai-responses">OpenAI Responses</option></select></label>
        <label><span>思考强度</span><select value={value.thinking_level} onChange={event => onChange({ ...value, thinking_level: event.target.value as RuntimeConfig['thinking_level'] })}><option value="off">off</option><option value="minimal">minimal</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
        <label className="wide"><span>API Key {hasSavedKey && <small>已在本机安全保存</small>}</span><input type="password" required={!hasSavedKey} value={value.api_key} onChange={event => onChange({ ...value, api_key: event.target.value })} placeholder={hasSavedKey ? '留空则继续使用已保存的 Key' : '输入 API Key'} /></label>
        <small className="key-note wide">密钥由后端写入系统凭据存储；前端不保存密钥，也不会把密钥写入项目目录。</small>
        {error && <p className="failure wide" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>取消</button>
          <button type="button" className="secondary" disabled={saving || testing || !ready} onClick={onProbe}>{testing ? '验证中…' : '测试连接'}</button>
          <button className="primary" disabled={saving || testing || !ready}>{saving ? '保存中…' : '保存设置'}</button>
        </div>
      </form>
    </section>
  </div>
}
