import { useEffect, useState } from 'react'
import { api } from './api'
import type { Artifact, PendingRequest, Session, UIEvent } from './types'

export type ConfigOption = {
  id: string
  name?: string
  type: 'select' | 'boolean'
  currentValue?: string | boolean
  options?: Array<{ value: string; name?: string }>
}

type SessionData = {
  sessionId: string | null
  events: UIEvent[]
  artifacts: Artifact[]
  pending: PendingRequest[]
  configOptions: ConfigOption[]
  connection: 'idle' | 'loading' | 'live' | 'history' | 'disconnected'
}

const emptyData: SessionData = {
  sessionId: null,
  events: [],
  artifacts: [],
  pending: [],
  configOptions: [],
  connection: 'idle',
}

function configFrom(events: UIEvent[]): ConfigOption[] {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'session_config' && Array.isArray(event.payload.configOptions)) {
      return event.payload.configOptions as ConfigOption[]
    }
  }
  return []
}

function appendEvent(events: UIEvent[], event: UIEvent): UIEvent[] {
  if (events.some(item => item.sequence === event.sequence)) return events
  return [...events, event].sort((left, right) => left.sequence - right.sequence)
}

export function useSessionData(
  active: Session | null,
  patchSession: (id: string, changes: Partial<Session>) => void,
  updateSession: (session: Session) => void,
  reportArtifact: () => void,
  reportError: (message: string) => void,
) {
  const [data, setData] = useState<SessionData>(emptyData)
  const activeId = active?.id ?? null
  const activeMode = active?.mode ?? null

  useEffect(() => {
    if (!activeId) return

    let disposed = false
    let stream: EventSource | null = null

    const load = async () => {
      try {
        const snapshot = await api.snapshot(activeId)
        if (disposed) return
        updateSession(snapshot.session)
        setData({
          sessionId: activeId,
          events: snapshot.events,
          artifacts: snapshot.artifacts,
          pending: snapshot.pending_requests,
          configOptions: configFrom(snapshot.events),
          connection: snapshot.session.mode,
        })

        if (snapshot.session.mode !== 'live') return
        const after = snapshot.events.at(-1)?.sequence ?? 0
        stream = api.eventStream(
          activeId,
          after,
          event => {
            if (disposed) return
            setData(current => {
              if (current.sessionId !== activeId) return current
              const events = appendEvent(current.events, event)
              return {
                ...current,
                events,
                configOptions: event.type === 'session_config' ? configFrom(events) : current.configOptions,
              }
            })
            if (event.type.endsWith('_requested') || event.type === 'request_answered') {
              void api.snapshot(activeId).then(fresh => {
                if (!disposed) setData(value => value.sessionId === activeId ? {
                  ...value,
                  pending: fresh.pending_requests,
                  artifacts: fresh.artifacts,
                } : value)
              }).catch(reason => {
                if (!disposed) reportError(reason instanceof Error ? reason.message : String(reason))
              })
            }
            if (event.type === 'artifact_updated' || event.type === 'session_completed') {
              void api.artifacts(activeId).then(artifacts => {
                if (!disposed) setData(value => value.sessionId === activeId ? { ...value, artifacts } : value)
              }).catch(reason => {
                if (!disposed) reportError(reason instanceof Error ? reason.message : String(reason))
              })
            }
            if (event.type === 'session_state' && typeof event.payload.state === 'string') {
              const state = String(event.payload.state)
              patchSession(activeId, {
                state,
                ...(state === 'running' ? { error_code: null, error_message: null } : {}),
              })
            }
            if (event.type === 'analysis_started') {
              patchSession(activeId, { analysis_started: true })
            }
            if (event.type === 'session_failed') {
              patchSession(activeId, {
                state: 'failed',
                error_code: String(event.payload.code ?? 'agent_prompt_failed'),
                error_message: String(event.payload.message ?? '模型请求失败。'),
              })
            }
            if (event.type === 'session_completed') {
              patchSession(activeId, { state: 'ready', error_code: null, error_message: null })
            }
            if (event.type === 'artifact_updated' && ['working_report', 'final_report'].includes(String(event.payload.kind ?? ''))) {
              reportArtifact()
            }
          },
          () => setData(current => current.sessionId === activeId ? { ...current, connection: 'live' } : current),
          () => setData(current => current.sessionId === activeId ? { ...current, connection: 'disconnected' } : current),
        )
      } catch (reason) {
        if (!disposed) reportError(reason instanceof Error ? reason.message : String(reason))
      }
    }

    void load()
    return () => {
      disposed = true
      stream?.close()
    }
  }, [activeId, activeMode, patchSession, reportArtifact, reportError, updateSession])

  const visible = data.sessionId === activeId ? data : { ...emptyData, connection: activeId ? 'loading' as const : 'idle' as const }
  return { ...visible, setData }
}
