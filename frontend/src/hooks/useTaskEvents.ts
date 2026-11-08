import { useCallback, useEffect, useState } from 'react'
import type { InvestigationTask } from '../api/reviewer'
import { ReviewerAccessError } from '../api/reviewer'
import { readTaskEvents } from '../api/taskEvents'

interface Progress {
  tasks: InvestigationTask[] | null
  phase: 'connecting' | 'live' | 'reconnecting' | 'stopped'
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
    const timer = setTimeout(finish, milliseconds)
    signal.addEventListener('abort', finish, { once: true })
    if (signal.aborted) finish()
  })
}

export function useTaskEvents(token: string, onUnauthorized: (message: string) => void) {
  const [generation, setGeneration] = useState(0)
  const [progress, setProgress] = useState<Progress>({ tasks: null, phase: 'connecting' })
  const reconnect = useCallback(() => {
    setProgress({ tasks: null, phase: 'connecting' })
    setGeneration((value) => value + 1)
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    setProgress({ tasks: null, phase: 'connecting' })
    void (async () => {
      let failures = 0
      while (!signal.aborted) {
        try {
          await readTaskEvents(token, signal, (tasks) => {
            if (!signal.aborted) setProgress({ tasks, phase: 'live' })
          })
          if (signal.aborted) return
          failures = 0 // Only a valid snapshot followed by deliberate server rotation resets the budget.
          await wait(500, signal)
        } catch (error) {
          if (signal.aborted) return
          if (error instanceof ReviewerAccessError) { onUnauthorized(error.message); return }
          failures += 1
          setProgress({ tasks: null, phase: failures >= 5 ? 'stopped' : 'reconnecting' })
          if (failures >= 5) return
          await wait(1000 * 2 ** (failures - 1), signal)
        }
      }
    })()
    return () => controller.abort() // Also prevents late chunks from restoring private data after lock/close.
  }, [token, onUnauthorized, generation])
  return { ...progress, reconnect }
}
