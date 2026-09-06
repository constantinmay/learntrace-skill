// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Conversation } from './components/Conversation'
import { ChatWorkspace } from './components/ChatWorkspace'
import type { UIEvent } from './types'

afterEach(cleanup)

function delta(sequence: number, text: string): UIEvent {
  return {
    session_id: 'history',
    sequence,
    created_at: '2026-09-03T00:00:00Z',
    type: 'message_delta',
    payload: { role: 'agent', text, message_id: 'assistant-history' },
  }
}

describe('historical conversation rendering', () => {
  it('stops a running session and allows retry when stopping fails', async () => {
    const onStop = vi.fn().mockRejectedValueOnce(new Error('连接中断')).mockResolvedValue(undefined)
    const props = {
      session: { id: 'live', project: 'C:\\project', title: 'project', state: 'running', mode: 'live' as const, can_send: true, created_at: '', updated_at: '' },
      projectName: 'project', events: [], pending: [], configOptions: [], artifactCount: 0,
      connection: 'live', error: '', reportOpen: false,
      onToggleReport: vi.fn(), onModelSettings: vi.fn(), onNewSession: vi.fn(), onResume: vi.fn(),
      onDelete: vi.fn(), onStart: vi.fn(), onRetry: vi.fn(), onStop,
      onSend: vi.fn(), onAnswer: vi.fn(), onConfigure: vi.fn(),
    }
    const { rerender } = render(<ChatWorkspace {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '停止分析' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('连接中断'))
    fireEvent.click(screen.getByRole('button', { name: '停止分析' }))
    await waitFor(() => expect(onStop).toHaveBeenCalledTimes(2))
    rerender(<ChatWorkspace {...props} session={{ ...props.session, state: 'cancelled' }} />)
    expect(screen.queryByRole('button', { name: '停止分析' })).toBeNull()
    expect(screen.getByText('已暂停')).toBeTruthy()
  })
  it('renders a persisted streamed GFM response without crashing', () => {
    const markdown = [
      '**Git 范围**',
      '',
      '| 作者 | 提交 |',
      '| --- | ---: |',
      '| Liushenwuzhu-Alpaca | 46 |',
      '',
      '- `README.md`',
      '- `src/agent/knowledge/`',
    ].join('\n')
    const events = [...markdown].map((text, index) => delta(index + 1, text))

    render(<Conversation events={events} onStart={vi.fn()} readOnly />)

    expect(screen.getByText('Git 范围')).toBeTruthy()
    expect(screen.getByText('Liushenwuzhu-Alpaca')).toBeTruthy()
  })

  it('offers a new analysis instead of retrying a dead runtime', () => {
    render(<ChatWorkspace
      session={{
        id: 'history', project: 'C:\\project', title: 'project', state: 'failed',
        mode: 'history', can_send: false, error_code: 'agent_unavailable',
        error_message: 'Request timed out.', created_at: '', updated_at: '',
      }}
      projectName="project" events={[]} pending={[]} configOptions={[]}
      artifactCount={0} connection="history" error="" reportOpen={false}
      onToggleReport={vi.fn()} onModelSettings={vi.fn()} onNewSession={vi.fn()}
      onResume={vi.fn()}
      onStop={vi.fn(async () => undefined)}
      onDelete={vi.fn()} onStart={vi.fn()} onRetry={vi.fn()}
      onSend={vi.fn(async () => undefined)} onAnswer={vi.fn(async () => undefined)}
      onConfigure={vi.fn(async () => undefined)}
    />)

    expect(screen.queryByText('重新尝试')).toBeNull()
    expect(screen.getByRole('button', { name: '恢复并继续' })).toBeTruthy()
  })
})
