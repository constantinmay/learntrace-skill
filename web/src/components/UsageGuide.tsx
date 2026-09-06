export function UsageGuide({ onClose }: { onClose: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="guide-dialog" role="dialog" aria-modal="true" aria-labelledby="usage-guide-title">
      <header>
        <div><span className="eyebrow">使用指南</span><h2 id="usage-guide-title">从项目材料形成学习档案</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="关闭使用指南">×</button>
      </header>
      <ol className="guide-steps">
        <li><strong>配置模型</strong><p>填写 API 地址、模型 ID、接口协议和 API Key。密钥保存在系统凭据库，不写入项目。</p></li>
        <li><strong>选择项目</strong><p>选择包含源码、Git 历史、文档和测试材料的本地项目根目录。</p></li>
        <li><strong>添加对话资料（可选）</strong><p>如果希望分析 AI 协作过程，可逐项添加 OpenCode、Claude Code 或 Codex 的 JSON/JSONL 会话导出。</p></li>
        <li><strong>开始调查</strong><p>打开项目后点击 LearnTrace Start，或直接说明希望 Agent 调查的问题。</p></li>
        <li><strong>作出确认</strong><p>Agent需要授权、范围选择或反思信息时，页面会显示独立的问题卡片。</p></li>
        <li><strong>查看档案</strong><p>右侧报告会读取后端保存的 Markdown 并持续更新；服务重启后可以恢复同一份 Pi 会话上下文，旧报告也会保留该次分析的快照。</p></li>
      </ol>
      <aside className="guide-note"><strong>资料边界</strong><p>未添加 AI 对话资料时，LearnTrace 只依据项目证据工作，不会自行扫描其他 Agent 的本地记录，也不会把当前产品对话作为历史资料。</p></aside>
    </section>
  </div>
}
