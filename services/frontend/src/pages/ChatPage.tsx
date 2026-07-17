import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AiServiceError } from '../api/chat'
import type { ChatCitation, ChatMessage } from '../api/chat'
import { ChatBubble } from '../components/ChatBubble'
import { useStreamChatMessage } from '../hooks/useStreamChatMessage'

// UI-only, local to this page — never resent as-is, since `ai-service`
// rejects unrecognized fields on the resent `messages` array.
type DisplayMessage = ChatMessage & { citations?: ChatCitation[] }

function toWireMessages(messages: DisplayMessage[]): ChatMessage[] {
  return messages.map(({ role, content }) => ({ role, content }))
}

export function ChatPage() {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [input, setInput] = useState('')
  // Live reasoning preview — never added to `messages`, never resent, and
  // discarded the moment the real answer starts arriving.
  const [thinkingPreview, setThinkingPreview] = useState('')
  // Set when a stream finishes with no error but never produced any answer
  // content (e.g. the model only emitted a reasoning block) — surfaced like
  // any other failure so the response doesn't just silently vanish.
  const [emptyResponse, setEmptyResponse] = useState(false)
  const sendMessage = useStreamChatMessage()
  const bottomRef = useRef<HTMLDivElement>(null)
  // Index of the streaming assistant placeholder in `messages`, or null if
  // the current request hasn't produced any answer content yet. Doubles as
  // the "has a placeholder been created" flag — no separate boolean needed.
  const assistantIndexRef = useRef<number | null>(null)
  const wasPendingRef = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sendMessage.isPending, thinkingPreview])

  useEffect(() => {
    if (!sendMessage.isError) return
    setThinkingPreview('')
    const index = assistantIndexRef.current
    if (index !== null) {
      setMessages((prev) => prev.filter((_, i) => i !== index))
      assistantIndexRef.current = null
    }
  }, [sendMessage.isError])

  useEffect(() => {
    if (
      wasPendingRef.current &&
      !sendMessage.isPending &&
      !sendMessage.isError &&
      assistantIndexRef.current === null
    ) {
      setEmptyResponse(true)
    }
    wasPendingRef.current = sendMessage.isPending
  }, [sendMessage.isPending, sendMessage.isError])

  function send(nextMessages: ChatMessage[]) {
    // Computed synchronously (not read from `messages` state, and not
    // derived inside a `setMessages` updater) so it's already correct the
    // instant the *first* `onToken` call checks it — a single `read()` on
    // the underlying stream can deliver more than one token with no
    // `await` in between, and a state updater isn't guaranteed to have run
    // yet when that second, same-tick call checks `assistantIndexRef`.
    const assistantIndex = nextMessages.length
    assistantIndexRef.current = null
    setThinkingPreview('')
    setEmptyResponse(false)

    let pendingCitations: ChatCitation[] = []

    sendMessage.send(nextMessages, {
      onCitations: (citations) => {
        pendingCitations = citations
      },
      onThinking: (delta) => {
        setThinkingPreview((prev) => prev + delta)
      },
      onToken: (delta) => {
        if (assistantIndexRef.current === null) {
          assistantIndexRef.current = assistantIndex
          setThinkingPreview('')
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: delta, citations: pendingCitations },
          ])
          return
        }
        setMessages((prev) =>
          prev.map((m, i) => (i === assistantIndex ? { ...m, content: m.content + delta } : m)),
        )
      },
    })
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || sendMessage.isPending) return

    const nextMessages: DisplayMessage[] = [...messages, { role: 'user', content: trimmed }]
    setMessages(nextMessages)
    setInput('')
    send(toWireMessages(nextMessages))
  }

  function retry() {
    if (messages.length === 0 || sendMessage.isPending) return
    send(toWireMessages(messages))
  }

  const errorMessage =
    sendMessage.isError && sendMessage.error instanceof AiServiceError
      ? sendMessage.error.message
      : sendMessage.isError
        ? 'Something went wrong talking to ARIA.'
        : emptyResponse
          ? "ARIA didn't return a response."
          : null

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      <h1 className="text-2xl font-semibold">Chat</h1>
      <p className="mt-1 text-subtle">
        Ask ARIA a general question. This conversation isn't saved — it clears when you leave the page.
      </p>

      <div className="mt-4 flex-1 space-y-3 overflow-y-auto rounded-lg border border-divider p-4">
        {messages.length === 0 && <p className="text-sm text-subtle">No messages yet — say hello.</p>}
        {messages.map((message, i) => (
          <ChatBubble key={i} message={message} citations={message.citations} />
        ))}
        {sendMessage.isPending && assistantIndexRef.current === null && (
          <div className="flex justify-start">
            <div className="max-w-[75%] rounded-lg bg-surface-hover px-3 py-2 text-sm italic text-subtle">
              {thinkingPreview || 'Thinking…'}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {errorMessage && (
        <div className="mt-2 flex items-center justify-between text-sm text-red-500">
          <span>{errorMessage}</span>
          <button
            type="button"
            onClick={retry}
            disabled={sendMessage.isPending}
            className="rounded-md border border-line px-2 py-1 text-subtle disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask ARIA something…"
          disabled={sendMessage.isPending}
          className="flex-1 rounded-md border border-line bg-transparent px-3 py-2"
        />
        <button
          type="submit"
          disabled={sendMessage.isPending || !input.trim()}
          className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}
