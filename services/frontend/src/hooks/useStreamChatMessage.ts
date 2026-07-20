import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api/chat'
import type { ChatMessage, StreamChatHandlers } from '../api/chat'

export function useStreamChatMessage() {
  const [isPending, setIsPending] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    // Cancel any in-flight stream if the page unmounts mid-response.
    return () => controllerRef.current?.abort()
  }, [])

  const run = useCallback(
    (messages: ChatMessage[], handlers: StreamChatHandlers, resume?: api.ChatResume) => {
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller

      setIsPending(true)
      setError(null)

      api
        .streamChatMessage(messages, handlers, controller.signal, resume)
        .catch((err: unknown) => {
          if (controller.signal.aborted) return
          setError(
            err instanceof Error ? err : new Error('Something went wrong talking to ARIA.'),
          )
        })
        .finally(() => {
          if (controllerRef.current === controller) {
            setIsPending(false)
          }
        })
    },
    [],
  )

  const send = useCallback(
    (messages: ChatMessage[], handlers: StreamChatHandlers) => run(messages, handlers),
    [run],
  )

  // The resume call doesn't need real message history since the graph
  // state is already checkpointed under `threadId` (see `ChatResume`).
  const resumeAction = useCallback(
    (threadId: string, decision: 'confirm' | 'reject', handlers: StreamChatHandlers) =>
      run([], handlers, { threadId, decision }),
    [run],
  )

  return { send, resumeAction, isPending, isError: error !== null, error }
}
