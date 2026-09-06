import { Component, type ErrorInfo, type ReactNode } from 'react'

export class SectionErrorBoundary extends Component<
  { children: ReactNode; title: string },
  { error: string }
> {
  state = { error: '' }

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(`LearnTrace ${this.props.title} render failed`, error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return <section className="section-error">
        <strong>{this.props.title}暂时无法显示</strong>
        <p>{this.state.error}</p>
        <button onClick={() => this.setState({ error: '' })}>重新渲染</button>
      </section>
    }
    return this.props.children
  }
}
