import { effectScope } from 'vue'

import { api, getStoredToken, handleAuthExpired } from '../services/api.ts'
import { parseSseChunk } from '../utils/sse.ts'

/**
 * Single, app-wide SSE consumer of `/api/aniu/events` that fires registered
 * listeners on `run_completed` / `run_failed` events.
 *
 * Unlike `useRunStream` (which only connects when the user manually starts
 * a run), this notifier is mounted once per session at the App root so the
 * client also sees scheduler-triggered runs finishing.
 *
 * Lifecycle: `start()` is idempotent and safe to call across route changes;
 * `stop()` aborts the in-flight stream and prevents auto-reconnect.
 */

export interface GlobalRunEvent {
  type: string
  run_id?: number
  ts?: number
  run_type?: string
  schedule_id?: number | null
  schedule_name?: string | null
  trigger_source?: string
  message?: string
  actions?: number
  [key: string]: unknown
}

const RECONNECT_BACKOFF_MS = [2000, 5000, 10000, 30000]
// Events that subscribers should *not* see — purely transport-level keepalives.
const TRANSPORT_EVENT_TYPES = new Set(['heartbeat'])

/** Internal factory exposed for tests. Production code should call
 * {@link useGlobalRunNotifier} which returns a process-wide singleton.
 */
export function createGlobalRunNotifier() {
  const listeners = new Set<(event: GlobalRunEvent) => void>()
  let controller: AbortController | null = null
  let running = false
  let stopRequested = false
  let reconnectTimer: number | null = null
  let attempt = 0

  function clearReconnectTimer() {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function dispatch(event: GlobalRunEvent) {
    for (const fn of listeners) {
      try {
        fn(event)
      } catch (err) {
        console.error('[useGlobalRunNotifier] listener failed', err)
      }
    }
  }

  async function connect(): Promise<void> {
    const token = getStoredToken()
    if (!token) {
      // No credentials yet — bail; callers should re-invoke `start()` after login.
      running = false
      return
    }

    controller = new AbortController()
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    headers.Authorization = `Bearer ${token}`

    let response: Response
    try {
      response = await fetch(api.globalEventsUrl(), {
        method: 'GET',
        headers,
        signal: controller.signal,
        cache: 'no-store',
      })
    } catch (err) {
      if ((err as DOMException)?.name === 'AbortError') return
      throw err
    }

    if (response.status === 401) {
      // Token expired — defer to the shared logout flow; don't reconnect.
      stopRequested = true
      handleAuthExpired()
      return
    }
    if (!response.ok || !response.body) {
      throw new Error(`global SSE connect failed (${response.status})`)
    }

    // We have an established stream — reset backoff so the next disconnect
    // starts from the shortest delay again.
    attempt = 0

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      if (stopRequested) break
      buffer += decoder.decode(value, { stream: true })

      let idx = buffer.indexOf('\n\n')
      while (idx >= 0) {
        const chunk = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const event = parseSseChunk<GlobalRunEvent>(chunk, (parseErr, payload) => {
          console.warn('[useGlobalRunNotifier] parse failed', parseErr, payload)
        })
        // Re-check after every parsed event so listeners stop firing as soon as
        // the consumer disconnects, even if we still have buffered bytes.
        if (stopRequested) return
        if (event && !TRANSPORT_EVENT_TYPES.has(event.type)) {
          dispatch(event)
        }
        idx = buffer.indexOf('\n\n')
      }
    }
  }

  function scheduleReconnect() {
    if (stopRequested) {
      running = false
      return
    }
    const delay = RECONNECT_BACKOFF_MS[Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)]
    attempt += 1
    clearReconnectTimer()
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      void runLoop()
    }, delay)
  }

  async function runLoop(): Promise<void> {
    if (!running || stopRequested) return
    try {
      await connect()
    } catch (err) {
      if ((err as DOMException)?.name !== 'AbortError') {
        console.warn('[useGlobalRunNotifier] stream error, will reconnect', err)
      }
    } finally {
      controller = null
    }
    if (stopRequested) {
      running = false
      return
    }
    // Either the server closed the stream, or fetch threw. Schedule a retry.
    scheduleReconnect()
  }

  function start(): void {
    if (running) return
    stopRequested = false
    running = true
    attempt = 0
    void runLoop()
  }

  function stop(): void {
    stopRequested = true
    running = false
    clearReconnectTimer()
    if (controller) {
      try {
        controller.abort()
      } catch {
        // ignore — already aborted
      }
      controller = null
    }
  }

  function onEvent(fn: (event: GlobalRunEvent) => void): () => void {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }

  return { start, stop, onEvent }
}

let singleton: ReturnType<typeof createGlobalRunNotifier> | null = null
let singletonScope: ReturnType<typeof effectScope> | null = null

export function useGlobalRunNotifier() {
  if (!singleton) {
    singletonScope = effectScope(true)
    singleton = singletonScope.run(() => createGlobalRunNotifier())!
  }
  return singleton
}
