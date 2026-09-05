import { spawn, type ChildProcess } from 'node:child_process'
import { join } from 'node:path'

export const OUTPUT_LIMIT_BYTES = 64 * 1024
export type CommandSpec = { executable: string; args: readonly string[] }
export type RunOutcome = { exitCode: number | null; output: string; truncated: boolean; cancelled: boolean; timedOut: boolean }

const ENV_ALLOWLIST = new Set([
  'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'HOME', 'USERPROFILE',
  'TEMP', 'TMP', 'COMSPEC', 'HOMEDRIVE', 'USERNAME',
])

export function minimalEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined && ENV_ALLOWLIST.has(key.toUpperCase())) result[key] = value
  }
  if (result.Path !== undefined) {
    result.PATH ??= result.Path
    delete result.Path
  }
  return result
}

async function terminateTree(child: ChildProcess): Promise<void> {
  if (!child.pid) return
  if (process.platform === 'win32') {
    const taskkill = join(process.env.SystemRoot ?? 'C:\Windows', 'System32', 'taskkill.exe')
    const alreadyExited = () => child.exitCode !== null || child.signalCode !== null
    await new Promise<void>((resolve, reject) => {
      let stderr = ''
      const killer = spawn(taskkill, ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] })
      killer.stderr?.on('data', chunk => { stderr += chunk.toString('utf8') })
      killer.on('error', reject)
      killer.on('close', async code => {
        if (code === 0 || alreadyExited()) return resolve()
        // taskkill can report failure because the tree already exited between
        // the kill request and its exit-code read; only give up once the
        // child confirms it is really gone.
        if (await waitForExit(child, 2000)) return resolve()
        const reason = stderr.trim() || `taskkill exited with code ${code}`
        reject(new Error(`无法终止命令进程树：${reason}`))
      })
    })
  } else {
    try { process.kill(-child.pid, 'SIGKILL') }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error }
  }
}

function waitForExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve(true)
    const timer = setTimeout(() => { child.removeListener('exit', onExit); resolve(false) }, timeoutMs)
    const onExit = () => { clearTimeout(timer); resolve(true) }
    child.once('exit', onExit)
  })
}
export function runCommand(command: CommandSpec, cwd: string, signal?: AbortSignal, timeoutMs = 120_000): Promise<RunOutcome> {
  signal?.throwIfAborted()
  return new Promise((resolve, reject) => {
    const child = spawn(command.executable, [...command.args], {
      cwd, env: minimalEnvironment(process.env), shell: false, windowsHide: true,
      detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'],
    })
    const buffer = Buffer.alloc(OUTPUT_LIMIT_BYTES)
    let size = 0
    let truncated = false
    let cancelled = false
    let timedOut = false
    let stopping: Promise<void> | undefined
    let stopError: unknown
    let spawnError: Error | undefined
    const stop = () => {
      clearTimeout(timer)
      stopping ??= terminateTree(child).catch(error => { stopError = error; child.kill('SIGKILL') })
    }
    const abort = () => { cancelled = true; stop() }
    const timer = setTimeout(() => { timedOut = true; stop() }, timeoutMs)
    signal?.addEventListener('abort', abort, { once: true })
    const collect = (chunk: Buffer) => {
      const retained = Math.min(chunk.length, OUTPUT_LIMIT_BYTES - size)
      chunk.copy(buffer, size, 0, retained)
      size += retained
      if (retained < chunk.length) { truncated = true; stop() }
    }
    child.stdout!.on('data', collect)
    child.stderr!.on('data', collect)
    child.on('error', error => { spawnError = error })
    child.on('close', exitCode => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
      void (async () => {
        await stopping
        if (stopError) throw stopError
        if (spawnError) throw spawnError
        resolve({ exitCode, output: buffer.subarray(0, size).toString('utf8'), truncated, cancelled, timedOut })
      })().catch(reject)
    })
    if (signal?.aborted) abort()
  })
}
