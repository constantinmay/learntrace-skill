import { describe, expect, it } from 'vitest'
import { assertRuntimeConfig, type RuntimeConfig } from './protocol.js'

const valid: RuntimeConfig = {
  baseUrl: 'https://api.example.com',
  apiKey: 'secret',
  modelId: 'example-model',
  api: 'openai-completions',
  thinkingLevel: 'medium',
}

describe('runtime configuration', () => {
  it('accepts a complete HTTPS model configuration', () => {
    expect(() => assertRuntimeConfig(valid)).not.toThrow()
  })

  it('rejects an insecure remote endpoint', () => {
    expect(() => assertRuntimeConfig({ ...valid, baseUrl: 'http://api.example.com' }))
      .toThrow(/HTTPS/)
  })

  it('allows HTTP for a local model server', () => {
    expect(() => assertRuntimeConfig({ ...valid, baseUrl: 'http://127.0.0.1:11434' }))
      .not.toThrow()
  })

  it('requires both a key and model id', () => {
    expect(() => assertRuntimeConfig({ ...valid, apiKey: '' })).toThrow(/API Key/)
    expect(() => assertRuntimeConfig({ ...valid, modelId: '' })).toThrow(/模型 ID/)
  })
})
