import { spawn, type ChildProcess } from 'node:child_process'
import { join } from 'node:path'

export const OUTPUT_LIMIT_BYTES = 64 * 1024
// After the leader exits, stdout/stderr normally close within milliseconds.
// A detached descendant that inherited the pipes can keep them open forever, so
// 'close' alone must not be the only settle signal; this grace period bounds it.
const EXIT_DRAIN_MS = 800
export type CommandSpec = { executable: string; args: readonly string[] }
export type RunOutcome = {
  exitCode: number | null
  output: string
  truncated: boolean
  cancelled: boolean
  timedOut: boolean
  /** True when tree termination could not be confirmed (denied/blocked by the OS); a controlled degradation result. */
  stopFailed?: boolean
}

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

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function waitForExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve(true)
    const timer = setTimeout(() => { child.removeListener('exit', onExit); resolve(false) }, timeoutMs)
    const onExit = () => { clearTimeout(timer); resolve(true) }
    child.once('exit', onExit)
  })
}

// Runs one taskkill attempt against the whole tree. Never throws and never
// hangs: the timer bounds the wait even if taskkill itself wedges, and the
// killer is released afterwards so a stuck taskkill cannot stall the caller.
function runTaskkill(taskkillPath: string, pid: number, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    let killer: ChildProcess | undefined
    let done = false
    const settle = (ok: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve(ok)
    }
    const timer = setTimeout(() => {
      settle(false)
      if (killer) {
        try { killer.kill() } catch { /* ignore */ }
        try { killer.unref() } catch { /* ignore */ }
      }
    }, timeoutMs)
    killer = spawn(taskkillPath, ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
    killer.on('error', () => settle(false))
    killer.on('close', code => settle(code === 0))
  })
}

// Never throws. Resolves true only when the whole tree is confirmed gone.
// Denied or raced taskkill calls are retried within a bounded budget; if the OS
// or an EDR still refuses to terminate the tree, the leader is taken down
// directly (so its pipes close) but false is returned: descendants may survive
// and callers must report a controlled stopFailed instead of a clean stop.
async function terminateTree(child: ChildProcess): Promise<boolean> {
  if (!child.pid) return true
  const exited = () => child.exitCode !== null || child.signalCode !== null
  if (exited()) return true
  if (process.platform === 'win32') {
    const taskkill = join(process.env.SystemRoot ?? 'C:\Windows', 'System32', 'taskkill.exe')
    for (let attempt = 0; attempt < 3 && !exited(); attempt += 1) {
      if (await runTaskkill(taskkill, child.pid, 2000)) {
        if (await waitForExit(child, 1500)) return true
      }
      if (exited()) return true
      await sleep(100)
    }
    if (exited()) return true
    try { child.kill() } catch { /* ignore */ }
    await waitForExit(child, 2000)
    return false
  }
  try {
    process.kill(-child.pid, 'SIGKILL')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') {
      try { child.kill('SIGKILL') } catch { /* ignore */ }
    }
  }
  return waitForExit(child, 2000)
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
    let stopRequested = false
    let leaderExited = false
    let stopping: Promise<void> | undefined
    let settled = false
    let spawnError: Error | undefined
    let streamsClosed = false
    let leaderExitCode: number | null = null
    let closedExitCode: number | null = null
    let drainTimer: ReturnType<typeof setTimeout> | undefined
    const stillAlive = () => child.exitCode === null && child.signalCode === null

    const clearDrain = () => {
      if (drainTimer) { clearTimeout(drainTimer); drainTimer = undefined }
    }
    // A detached descendant that inherited our pipes can outlive the leader and
    // keep 'close' from ever firing. The grace timer makes the wait bounded: a
    // normal command's close arrives in milliseconds and cancels it; a command
    // that spawned such a descendant still settles shortly after the leader.
    const scheduleDrain = () => {
      clearDrain()
      drainTimer = setTimeout(() => {
        drainTimer = undefined
        if (!settled) finish()
      }, EXIT_DRAIN_MS)
    }
    const cleanup = () => {
      clearTimeout(timer)
      clearDrain()
      signal?.removeEventListener('abort', abort)
    }
    // Releases the pipe handles after the promise settles so a process that the
    // OS refuses to kill cannot keep this service's event loop alive or keep
    // accumulating buffered output.
    const abandon = () => {
      for (const stream of [child.stdout, child.stderr]) {
        if (!stream) continue
        stream.removeAllListeners('data')
        stream.on('error', () => { /* swallow errors raised by destroy() */ })
        stream.destroy()
      }
      if (stillAlive()) { try { child.unref() } catch { /* ignore */ } }
    }
    const finish = () => {
      if (settled) return
      settled = true
      cleanup()
      abandon()
      // A clean stop is only provable once our stdio pipes actually closed:
      // that is what releases the handles and what a pipe-holding detached
      // descendant would keep open. The parent exiting alone is not proof the
      // whole tree is gone, so a stop whose streams never closed is reported as
      // a controlled stopFailed rather than a clean stop or an unresolved wait.
      const stopFailed = stopRequested && !streamsClosed
      resolve({
        exitCode: closedExitCode ?? leaderExitCode ?? child.exitCode,
        output: buffer.subarray(0, size).toString('utf8'),
        truncated, cancelled, timedOut,
        ...(stopFailed ? { stopFailed: true } : {}),
      })
    }
    const stop = () => {
      if (stopping || settled) return
      stopRequested = true
      clearTimeout(timer)
      // Teardown is bounded and never rejects: a confirmed tree exit closes the
      // outcome normally, and any tree we cannot confirm stopped yields a
      // controlled stopFailed result instead of an unresolved promise.
      stopping = (async () => {
        if (settled) return
        if (leaderExited) {
          // The leader is already gone: the exit handler scheduled the drain
          // timer that bounds pipe-holding descendants, and a clean close
          // settles immediately. Nothing else for stopping to force.
          return
        }
        let ok = false
        try { ok = await terminateTree(child) } catch { ok = false }
        if (ok) return // exit/close handlers settle promptly once the tree dies
        // Termination could not be confirmed (denied/blocked): settle with a
        // controlled stopFailed after a short grace period instead of hanging.
        await sleep(500)
        if (!settled) finish()
      })()
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
    child.on('error', error => {
      spawnError = error
      if (!settled) { settled = true; cleanup(); abandon(); reject(error) }
    })
    child.on('exit', code => {
      if (settled) return
      leaderExited = true
      leaderExitCode = code
      scheduleDrain()
    })
    child.on('close', exitCode => {
      if (settled) return
      streamsClosed = true
      closedExitCode = exitCode
      void (async () => {
        await stopping
        if (!settled) {
          if (spawnError) reject(spawnError)
          else finish()
        }
      })().catch(reject)
    })
    if (signal?.aborted) abort()
  })
}
