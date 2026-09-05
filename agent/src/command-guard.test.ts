import { describe, expect, it } from 'vitest'
import {
  classifyCommand,
  minimalEnvironment,
  tokenizeCommand,
} from './command-guard.js'

describe('tokenizeCommand', () => {
  it('splits a plain command on whitespace', () => {
    expect(tokenizeCommand('learntrace task3_parse repo')).toEqual([
      'learntrace',
      'task3_parse',
      'repo',
    ])
  })

  it('trims leading and trailing whitespace', () => {
    expect(tokenizeCommand('  git status  ')).toEqual(['git', 'status'])
  })

  it('returns null for empty or whitespace-only input', () => {
    expect(tokenizeCommand('')).toBeNull()
    expect(tokenizeCommand('   ')).toBeNull()
  })

  it.each([';', '&', '|', '<', '>', '$', '`', '\n', '\r'])(
    'rejects the shell metacharacter %j',
    meta => {
      expect(tokenizeCommand(`learntrace run${meta}rm -rf /`)).toBeNull()
      expect(tokenizeCommand(`learntrace run ${meta} rm -rf /`)).toBeNull()
    },
  )

  it('rejects chained and substituted commands', () => {
    expect(tokenizeCommand('learntrace task3_parse x && rm -rf /')).toBeNull()
    expect(tokenizeCommand('echo hi > out.txt')).toBeNull()
    expect(tokenizeCommand('git commit -m "$(whoami)"')).toBeNull()
  })
})

describe('classifyCommand', () => {
  it('auto-allows the learntrace CLI', () => {
    expect(classifyCommand('learntrace task3_parse ./repo')).toEqual({
      verdict: 'allow',
    })
    expect(classifyCommand('learntrace')).toEqual({ verdict: 'allow' })
  })

  it('auto-allows read-only git commands', () => {
    for (const command of [
      'git status',
      'git log --oneline -5',
      'git show HEAD',
      'git diff --stat',
      'git blame README.md',
      'git ls-files',
      'git rev-parse --abbrev-ref HEAD',
      'git branch',
      'git remote -v',
      'git --version',
    ]) {
      expect(classifyCommand(command).verdict).toBe('allow')
    }
  })

  it('requests approval for mutating git commands', () => {
    for (const command of ['git push origin main', 'git commit -m "x"', 'git reset --hard HEAD']) {
      expect(classifyCommand(command).verdict).toBe('approve')
    }
  })

  it('requests approval for unknown binaries', () => {
    for (const command of [
      'python analyze.py',
      'bash -c whoami',
      'npm install',
      'rm -rf /tmp/scratch',
      'powershell Remove-Item -Recurse .',
    ]) {
      const result = classifyCommand(command)
      expect(result.verdict).toBe('approve')
      expect(result.reason).toBeTruthy()
    }
  })

  it('denies commands containing shell metacharacters', () => {
    const result = classifyCommand('learntrace run x && python -c "import os; os.system(\'x\')"')
    expect(result.verdict).toBe('deny')
    expect(result.reason).toBeTruthy()
  })

  it('denies empty or whitespace-only commands', () => {
    expect(classifyCommand('').verdict).toBe('deny')
    expect(classifyCommand('   ').verdict).toBe('deny')
  })
})

describe('minimalEnvironment', () => {
  it('keeps only the allowlisted variables', () => {
    const filtered = minimalEnvironment({
      PATH: '/usr/bin:/bin',
      HOME: '/home/test',
      TEMP: '/tmp',
      LLM_API_KEY: 'secret',
      AWS_SECRET_ACCESS_KEY: 'secret',
      PIP_INDEX_URL: 'https://internal.example',
      NODE_OPTIONS: '--max-old-space-size=4096',
    } as NodeJS.ProcessEnv)
    expect(filtered.PATH).toBe('/usr/bin:/bin')
    expect(filtered.HOME).toBe('/home/test')
    expect(filtered.TEMP).toBe('/tmp')
    expect(filtered.LLM_API_KEY).toBeUndefined()
    expect(filtered.AWS_SECRET_ACCESS_KEY).toBeUndefined()
    expect(filtered.PIP_INDEX_URL).toBeUndefined()
    expect(filtered.NODE_OPTIONS).toBeUndefined()
  })

  it('matches allowlist keys case-insensitively', () => {
    const filtered = minimalEnvironment({ Path: '/x', Temp: '/y' } as NodeJS.ProcessEnv)
    expect(filtered.Path).toBe('/x')
    expect(filtered.Temp).toBe('/y')
  })

  it('promotes Path to PATH only when PATH is missing', () => {
    const filtered = minimalEnvironment({ Path: '/usr/bin' } as NodeJS.ProcessEnv)
    expect(filtered.PATH).toBe('/usr/bin')
  })

  it('keeps PATH when both PATH and Path are present', () => {
    const filtered = minimalEnvironment({ PATH: '/a', Path: '/b' } as NodeJS.ProcessEnv)
    expect(filtered.PATH).toBe('/a')
    expect(filtered.Path).toBe('/b')
  })

  it('omits undefined values', () => {
    const filtered = minimalEnvironment({ PATH: undefined, HOME: '/home' } as NodeJS.ProcessEnv)
    expect(filtered.PATH).toBeUndefined()
    expect(filtered.HOME).toBe('/home')
  })
})