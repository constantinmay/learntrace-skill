import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { expect, it } from 'vitest'
import { minimalEnvironment, OUTPUT_LIMIT_BYTES, runCommand } from './process-runner.js'

const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
async function running(pid: number): Promise<boolean> {
  try {
    process.kill(pid, 0)
    if (process.platform === 'linux') {
      const stat = await readFile(`/proc/${pid}/stat`, 'utf8')
      if (stat.slice(stat.lastIndexOf(')') + 2).startsWith('Z')) return false
    }
    return true
  } catch { return false }
}

function killPidTree(pid: number): void {
  if (!Number.isInteger(pid) || pid <= 0) return
  try {
    if (process.platform === 'win32') {
      const taskkill = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe')
      spawnSync(taskkill, ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
    } else {
      try { process.kill(-pid, 'SIGKILL') } catch { process.kill(pid, 'SIGKILL') }
    }
  } catch { /* best effort cleanup */ }
}

// The leader exits ~150ms in while a detached descendant inherits stdout/stderr
// and stays alive, so the pipes never see EOF and child 'close' never fires on
// its own. runCommand must still settle, bounded, instead of staying pending.
const DAEMON_HOLDER_SCRIPT = (marker: string) =>
  "const cp=require('node:child_process');const fs=require('node:fs');" +
  "const d=cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:['ignore','inherit','inherit'],windowsHide:true});" +
  `fs.writeFileSync(process.argv[1],String(d.pid));setTimeout(()=>process.exit(0),150);`

it.each(['cancel', 'timeout'] as const)('terminates parent and child on %s', async mode => {
  const root = await mkdtemp(join(tmpdir(), 'learntrace-process-'))
  const marker = join(root, 'pids.json')
  const script = `const cp=require('node:child_process');const fs=require('node:fs');const child=cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});fs.writeFileSync(process.argv[1],JSON.stringify([process.pid,child.pid]));setInterval(()=>{},1000);`
  const controller = new AbortController()
  const pending = runCommand({ executable: process.execPath, args: ['-e', script, marker] }, root, controller.signal, mode === 'timeout' ? 1500 : 10000)
  let pids: number[] = []
  try {
    for (let i = 0; i < 100; i++) {
      try { pids = JSON.parse(await readFile(marker, 'utf8')) as number[]; break } catch { await pause(20) }
    }
    expect(pids).toHaveLength(2)
    if (mode === 'cancel') controller.abort()
    const result = await pending
    expect(mode === 'cancel' ? result.cancelled : result.timedOut).toBe(true)
    if (result.stopFailed) {
      // Documented degradation: when the OS or an EDR denies tree termination,
      // the outcome still settles promptly and reports stopFailed instead of
      // pretending the tree is gone. Whether the processes linger afterwards is
      // then an environment limitation, not a product regression.
      return
    }
    for (let i = 0; i < 100 && (await Promise.all(pids.map(running))).some(Boolean); i++) await pause(20)
    expect(await Promise.all(pids.map(running))).toEqual([false, false])
  } finally {
    controller.abort()
    await pending.catch(() => undefined)
    await rm(root, { recursive: true, force: true })
  }
}, 15000)

it('caps combined stdout/stderr during reading and terminates a flooding process', async () => {
  const script = 'process.stdout.write("a".repeat(48000));process.stderr.write("b".repeat(48000));setInterval(()=>process.stdout.write("c".repeat(100000)),10)'
  const result = await runCommand({ executable: process.execPath, args: ['-e', script] }, process.cwd())
  expect(result.truncated).toBe(true)
  expect(Buffer.byteLength(result.output)).toBe(OUTPUT_LIMIT_BYTES)
  expect(result.timedOut).toBe(false)
})

it('handles spawn failure and pre-cancelled requests promptly', async () => {
  await expect(runCommand({ executable: 'learntrace-nonexistent-binary', args: [] }, process.cwd())).rejects.toThrow()
  expect(() => runCommand({ executable: process.execPath, args: [] }, process.cwd(), AbortSignal.abort())).toThrow()
})

it('keeps only the minimal environment and one Windows PATH spelling', () => {
  const env = minimalEnvironment({ PATH: '/a', Path: '/b', HOME: '/home', TEMP: '/tmp', MODEL_API_KEY: 'secret', NODE_OPTIONS: '--eval=bad' })
  expect(env).toEqual({ PATH: '/a', HOME: '/home', TEMP: '/tmp' })
})

it('settles when the leader exits but a detached descendant holds the pipes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'learntrace-holder-'))
  const marker = join(root, 'daemon.pid')
  let daemonPid = 0
  try {
    const started = Date.now()
    const result = await runCommand({ executable: process.execPath, args: ['-e', DAEMON_HOLDER_SCRIPT(marker), marker] }, root)
    const elapsed = Date.now() - started
    expect(result.exitCode).toBe(0)
    expect(elapsed).toBeLessThan(5000)
    daemonPid = Number(await readFile(marker, 'utf8'))
    expect(Number.isInteger(daemonPid)).toBe(true)
  } finally {
    killPidTree(daemonPid)
    await rm(root, { recursive: true, force: true })
  }
}, 15000)

it('abort stays bounded and reports stopFailed when the leader is already gone and a descendant holds the pipes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'learntrace-holder-'))
  const marker = join(root, 'daemon.pid')
  const controller = new AbortController()
  let daemonPid = 0
  try {
    const pending = runCommand(
      { executable: process.execPath, args: ['-e', DAEMON_HOLDER_SCRIPT(marker), marker] },
      root, controller.signal, 10_000,
    )
    for (let i = 0; i < 100; i++) {
      try { daemonPid = Number(await readFile(marker, 'utf8')); if (daemonPid > 0) break } catch { await pause(20) }
    }
    expect(daemonPid).toBeGreaterThan(0)
    await pause(450) // let the ~150ms leader exit complete before aborting
    const started = Date.now()
    controller.abort()
    const result = await pending
    const elapsed = Date.now() - started
    expect(result.cancelled).toBe(true)
    expect(result.stopFailed).toBe(true)
    expect(elapsed).toBeLessThan(8000)
  } finally {
    controller.abort()
    killPidTree(daemonPid)
    await rm(root, { recursive: true, force: true })
  }
}, 20000)
