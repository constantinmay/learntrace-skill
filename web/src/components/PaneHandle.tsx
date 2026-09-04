export function PaneHandle({ side, onResize }: {
  side: 'left' | 'right'
  onResize: (delta: number) => void
}) {
  return <div
    className={`pane-handle ${side}`}
    role="separator"
    aria-orientation="vertical"
    aria-label={side === 'left' ? '调整会话栏宽度' : '调整报告栏宽度'}
    onPointerDown={event => {
      let previous = event.clientX
      const target = event.currentTarget
      target.setPointerCapture(event.pointerId)
      const move = (next: PointerEvent) => {
        onResize(next.clientX - previous)
        previous = next.clientX
      }
      const finish = () => {
        target.removeEventListener('pointermove', move)
        target.removeEventListener('pointerup', finish)
        target.removeEventListener('pointercancel', finish)
      }
      target.addEventListener('pointermove', move)
      target.addEventListener('pointerup', finish)
      target.addEventListener('pointercancel', finish)
    }}
  />
}
