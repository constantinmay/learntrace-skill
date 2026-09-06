import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

class PageErrorBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: '' }

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('LearnTrace UI render failed', error, info.componentStack)
  }

  render() {
    if (this.state.error) return <main className="page-error"><h1>界面渲染失败</h1><p>{this.state.error}</p><button onClick={() => window.location.reload()}>重新加载界面</button></main>
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(<StrictMode><PageErrorBoundary><App /></PageErrorBoundary></StrictMode>)
