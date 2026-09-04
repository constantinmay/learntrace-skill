// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Conversation } from './components/Conversation'
import { ChatWorkspace } from './components/ChatWorkspace'
import type { UIEvent } from './types'

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
      onDelete={vi.fn()} onStart={vi.fn()} onRetry={vi.fn()}
      onSend={vi.fn(async () => undefined)} onAnswer={vi.fn(async () => undefined)}
      onConfigure={vi.fn(async () => undefined)}
    />)

    expect(screen.queryByText('重新尝试')).toBeNull()
    expect(screen.getByRole('button', { name: '恢复并继续' })).toBeTruthy()
  })
})
