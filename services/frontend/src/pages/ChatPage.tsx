import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AiServiceError } from '../api/chat'
import type { ChatMessage } from '../api/chat'
import { ChatBubble } from '../components/ChatBubble'
import { useSendChatMessage } from '../hooks/useSendChatMessage'

export function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const sendMessage = useSendChatMessage()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sendMessage.isPending])

  function send(nextMessages: ChatMessage[]) {
    sendMessage.mutate(nextMessages, {
      onSuccess: (reply) => setMessages((prev) => [...prev, reply]),
    })
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || sendMessage.isPending) return

    const nextMessages: ChatMessage[] = [...messages, { role: 'user', content: trimmed }]
    setMessages(nextMessages)
    setInput('')
    send(nextMessages)
  }

  function retry() {
    if (messages.length === 0) return
    send(messages)
  }

  const errorMessage =
    sendMessage.isError && sendMessage.error instanceof AiServiceError
      ? sendMessage.error.message
      : sendMessage.isError
        ? 'Something went wrong talking to ARIA.'
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
          <ChatBubble key={i} message={message} />
        ))}
        {sendMessage.isPending && (
          <div className="flex justify-start">
            <div className="max-w-[75%] rounded-lg bg-surface-hover px-3 py-2 text-sm text-subtle">
              Thinking…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {errorMessage && (
        <div className="mt-2 flex items-center justify-between text-sm text-red-500">
          <span>{errorMessage}</span>
          <button type="button" onClick={retry} className="rounded-md border border-line px-2 py-1 text-subtle">
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
