import assert from 'node:assert/strict'
import test from 'node:test'

import { setStoredToken } from '../src/services/api.ts'
import {
  createGlobalRunNotifier,
  type GlobalRunEvent,
} from '../src/composables/useGlobalRunNotifier.ts'

class MemoryStorage implements Storage {
  private readonly store = new Map<string, string>()
  get length() {
    return this.store.size
  }
  clear() {
    this.store.clear()
  }
  getItem(key: string) {
    return this.store.has(key) ? this.store.get(key)! : null
  }
  key(index: number) {
    return Array.from(this.store.keys())[index] ?? null
  }
  removeItem(key: string) {
    this.store.delete(key)
  }
  setItem(key: string, value: string) {
    this.store.set(key, value)
  }
}

interface BrowserMockHandle {
  setFetch(handler: typeof fetch): void
  restore(): void
}

function installBrowserMocks(): BrowserMockHandle {
  const localStorage = new MemoryStorage()
  const sessionStorage = new MemoryStorage()
  const originalFetch = globalThis.fetch
  const originalWindow = (globalThis as { window?: unknown }).window
  const originalLocalStorage = (globalThis as { localStorage?: unknown }).localStorage
  const originalSessionStorage = (globalThis as { sessionStorage?: unknown }).sessionStorage
  const originalLocation = (globalThis as { location?: unknown }).location

  Object.defineProperty(globalThis, 'window', { configurable: true, value: globalThis })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: localStorage })
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: sessionStorage })
  Object.defineProperty(globalThis, 'location', {
    configurable: true,
    value: { pathname: '/tasks', search: '', hash: '', href: '/tasks' },
  })

  return {
    setFetch(handler: typeof fetch) {
      globalThis.fetch = handler
    },
    restore() {
      Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
      Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: originalLocalStorage })
      Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: originalSessionStorage })
      Object.defineProperty(globalThis, 'location', { configurable: true, value: originalLocation })
      globalThis.fetch = originalFetch
    },
  }
}

function encodeSseChunk(eventType: string, data: unknown): Uint8Array {
  const text = `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`
  return new TextEncoder().encode(text)
}

interface MockStream {
  push(chunk: Uint8Array): void
  close(): void
  response: Response
}

function createMockStream(): MockStream {
  let push!: (chunk: Uint8Array) => void
  let close!: () => void
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (chunk: Uint8Array) => controller.enqueue(chunk)
      close = () => controller.close()
    },
  })
  const response = new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
  return { push, close, response }
}

function waitForCondition(predicate: () => boolean, timeoutMs = 1000): Promise<void> {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (predicate()) {
        resolve()
        return
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error('waitForCondition timed out'))
        return
      }
      setTimeout(tick, 5)
    }
    tick()
  })
}

test('useGlobalRunNotifier dispatches run_completed events to listeners', async () => {
  const browser = installBrowserMocks()
  try {
    setStoredToken('test-token')

    const { push, close, response } = createMockStream()
    let fetchCalls = 0
    let observedAuth = ''
    browser.setFetch(async (_input, init) => {
      fetchCalls += 1
      observedAuth = new Headers(init?.headers).get('Authorization') ?? ''
      return response
    })

    const notifier = createGlobalRunNotifier()
    const received: GlobalRunEvent[] = []
    const dispose = notifier.onEvent((event) => {
      received.push(event)
    })

    notifier.start()
    await waitForCondition(() => fetchCalls === 1)
    assert.equal(observedAuth, 'Bearer test-token')

    push(encodeSseChunk('heartbeat', { type: 'heartbeat', ts: 0 }))
    push(encodeSseChunk('run_completed', {
      type: 'run_completed',
      run_id: 99,
      run_type: 'analysis',
      trigger_source: 'schedule',
    }))

    await waitForCondition(() => received.length >= 1)
    // heartbeats must be filtered out at the transport layer
    assert.equal(received.length, 1)
    assert.equal(received[0].type, 'run_completed')
    assert.equal(received[0].run_id, 99)
    assert.equal(received[0].run_type, 'analysis')

    dispose()
    close()
    notifier.stop()
  } finally {
    browser.restore()
  }
})

test('stop() prevents further dispatches even if more bytes arrive', async () => {
  const browser = installBrowserMocks()
  try {
    setStoredToken('stop-token')

    const { push, close, response } = createMockStream()
    browser.setFetch(async () => response)

    const notifier = createGlobalRunNotifier()
    const received: GlobalRunEvent[] = []
    notifier.onEvent((event) => received.push(event))
    notifier.start()
    // Wait for the connection to be ready before pushing the first event so
    // the reader actually processes it; otherwise the abort below races the
    // reader startup and the test passes for the wrong reason.
    await waitForCondition(() => globalThis.fetch !== undefined)

    push(encodeSseChunk('run_failed', {
      type: 'run_failed',
      run_id: 1,
    }))
    await waitForCondition(() => received.length >= 1)

    notifier.stop()
    // Push another chunk after stop(); even if it sneaks past the abort the
    // listener should NOT see it because the reader loop has unwound.
    push(encodeSseChunk('run_completed', {
      type: 'run_completed',
      run_id: 2,
    }))

    // Give the runtime a tick to deliver any straggler dispatches.
    await new Promise((resolve) => setTimeout(resolve, 50))
    assert.equal(received.length, 1, 'only the pre-stop event should be observed')

    close()
  } finally {
    browser.restore()
  }
})

test('start() without a stored token bails without calling fetch', async () => {
  const browser = installBrowserMocks()
  try {
    setStoredToken('')  // no token

    let fetchCalls = 0
    browser.setFetch(async () => {
      fetchCalls += 1
      return new Response(null, { status: 200 })
    })

    const notifier = createGlobalRunNotifier()
    notifier.start()
    await new Promise((resolve) => setTimeout(resolve, 30))

    assert.equal(fetchCalls, 0)
    notifier.stop()
  } finally {
    browser.restore()
  }
})
