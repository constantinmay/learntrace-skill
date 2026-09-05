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

describe('model base URL host allowlist', () => {
  const allowed = [
    'https://api.example.com',
    'https://[::1]:11434',
    'http://127.0.0.1:11434',
    'http://localhost:11434',
    'http://[::1]:11434',
  ]
  it.each(allowed)('accepts %s', url => {
    expect(() => assertRuntimeConfig({ ...valid, baseUrl: url })).not.toThrow()
  })

  const rejected = [
    'http://api.example.com',
    'http://127.0.0.1.evil.com',
    'http://127.0.0.1@evil.com',
    'http://localhost.attacker.test',
    'https://user:pass@api.example.com',
    'ws://example.com',
  ]
  it.each(rejected)('rejects %s', url => {
    expect(() => assertRuntimeConfig({ ...valid, baseUrl: url })).toThrow(/HTTPS|用户名|密码/)
  })
})