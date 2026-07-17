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

  const send = useCallback((messages: ChatMessage[], handlers: StreamChatHandlers) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    setIsPending(true)
    setError(null)

    api
      .streamChatMessage(messages, handlers, controller.signal)
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err : new Error('Something went wrong talking to ARIA.'))
      })
      .finally(() => {
        if (controllerRef.current === controller) {
          setIsPending(false)
        }
      })
  }, [])

  return { send, isPending, isError: error !== null, error }
}
