import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AiServiceError } from '../api/chat'
import type { ChatAgent, ChatCitation, ChatMessage, ProposedAction, StreamChatHandlers } from '../api/chat'
import { ChatBubble } from '../components/ChatBubble'
import { useStreamChatMessage } from '../hooks/useStreamChatMessage'

// UI-only, local to this page — never resent as-is, since `ai-service`
// rejects unrecognized fields on the resent `messages` array.
type DisplayMessage = ChatMessage & { citations?: ChatCitation[]; agentLabel?: string }

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
  // Which specialist is handling this turn, if the `agent` frame has
  // arrived — real state (not just a ref) specifically so the "X is
  // looking into this…" placeholder text re-renders the instant the frame
  // arrives, rather than waiting for the next unrelated state update.
  const [pendingAgent, setPendingAgent] = useState<ChatAgent | null>(null)
  // A proposed create_log/create_schedule action awaiting confirm/cancel —
  // set from the `action_proposed` SSE frame, never both this and a
  // streaming assistant placeholder at the same time (see `onToken` below).
  const [pendingAction, setPendingAction] = useState<ProposedAction | null>(null)
  const sendMessage = useStreamChatMessage()
  const bottomRef = useRef<HTMLDivElement>(null)
  // Index of the streaming assistant placeholder in `messages`, or null if
  // the current request hasn't produced any answer content yet. Doubles as
  // the "has a placeholder been created" flag — no separate boolean needed.
  const assistantIndexRef = useRef<number | null>(null)
  const wasPendingRef = useRef(false)
  // The most recent confirm/cancel attempt — set right before firing the
  // resume request, cleared the moment a fresh (non-resume) send starts.
  // Lets `retry()` re-attempt the same confirm/cancel after a failed
  // resume instead of silently falling back to an ordinary new turn, which
  // left the backend's paused graph thread orphaned until its checkpoint
  // expired with no way for the user to actually retry what they clicked
  // (see `retry()` below; caught in code review).
  const lastResumeRef = useRef<{ threadId: string; decision: 'confirm' | 'reject' } | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sendMessage.isPending, thinkingPreview, pendingAgent, pendingAction])

  useEffect(() => {
    if (!sendMessage.isError) return
    setThinkingPreview('')
    setPendingAgent(null)
    setPendingAction(null)
    const index = assistantIndexRef.current
    if (index !== null) {
      setMessages((prev) => prev.filter((_, i) => i !== index))
      assistantIndexRef.current = null
    }
  }, [sendMessage.isError])

  useEffect(() => {
    // A turn that ends by proposing an action legitimately produces no
    // token content at all (see the M8 plan: no Ollama call happens that
    // turn) — `pendingAction` being set is not the same failure this
    // effect otherwise catches (e.g. the model only emitting a reasoning
    // block).
    if (wasPendingRef.current && !sendMessage.isPending && !sendMessage.isError) {
      // The in-flight resume (if any) reached the server and completed
      // without a client-visible error — nothing left to retry, so don't
      // let a later, unrelated Retry click (e.g. the empty-response case
      // below) replay an already-executed confirm/cancel.
      lastResumeRef.current = null
      if (assistantIndexRef.current === null && pendingAction === null) {
        setEmptyResponse(true)
      }
    }
    wasPendingRef.current = sendMessage.isPending
  }, [sendMessage.isPending, sendMessage.isError, pendingAction])

  // Shared by `send()` and `confirmPendingAction()` — both start a stream
  // and route its frames into `messages`/`thinkingPreview`/`pendingAgent`/
  // `pendingAction` identically, differing only in what they send and
  // which index the streamed answer lands at.
  function buildHandlers(assistantIndex: number): StreamChatHandlers {
    let pendingCitations: ChatCitation[] = []
    let pendingAgentLabel: string | undefined

    return {
      onAgent: (agent) => {
        pendingAgentLabel = agent.label
        setPendingAgent(agent)
      },
      onActionProposed: (action) => {
        setPendingAction(action)
      },
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
            {
              role: 'assistant',
              content: delta,
              citations: pendingCitations,
              agentLabel: pendingAgentLabel,
            },
          ])
          return
        }
        setMessages((prev) =>
          prev.map((m, i) => (i === assistantIndex ? { ...m, content: m.content + delta } : m)),
        )
      },
    }
  }

  function send(nextMessages: ChatMessage[]) {
    // Computed synchronously (not read from `messages` state, and not
    // derived inside a `setMessages` updater) so it's already correct the
    // instant the *first* `onToken` call checks it — a single `read()` on
    // the underlying stream can deliver more than one token with no
    // `await` in between, and a state updater isn't guaranteed to have run
    // yet when that second, same-tick call checks `assistantIndexRef`.
    const assistantIndex = nextMessages.length
    assistantIndexRef.current = null
    lastResumeRef.current = null
    setPendingAgent(null)
    setThinkingPreview('')
    setEmptyResponse(false)

    sendMessage.send(nextMessages, buildHandlers(assistantIndex))
  }

  function confirmPendingAction(decision: 'confirm' | 'reject') {
    // The `isPending` check (not just `pendingAction`) matters here: the
    // Confirm/Cancel buttons' `disabled` prop doesn't take effect until
    // the next render, so a double-click/double-tap fired from the same
    // pre-render closure would otherwise both pass a bare `!pendingAction`
    // guard and both fire a resume request for the same thread (caught in
    // code review).
    if (!pendingAction || sendMessage.isPending) return
    const assistantIndex = messages.length
    assistantIndexRef.current = null
    setPendingAgent(null)
    setThinkingPreview('')
    setEmptyResponse(false)
    const { threadId } = pendingAction
    setPendingAction(null)
    lastResumeRef.current = { threadId, decision }

    sendMessage.resumeAction(threadId, decision, buildHandlers(assistantIndex))
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || sendMessage.isPending || pendingAction) return

    const nextMessages: DisplayMessage[] = [...messages, { role: 'user', content: trimmed }]
    setMessages(nextMessages)
    setInput('')
    send(toWireMessages(nextMessages))
  }

  function retry() {
    if (sendMessage.isPending || pendingAction) return
    // A failed Confirm/Cancel is retried as the same resume, not as a
    // fresh message — re-sending full history with no `resume` field
    // would leave the backend's graph thread paused at its interrupt
    // forever (until `agent_checkpoint_ttl_minutes` expires it) with no
    // way to actually re-confirm what the user clicked (caught in code
    // review).
    if (lastResumeRef.current) {
      const { threadId, decision } = lastResumeRef.current
      const assistantIndex = messages.length
      assistantIndexRef.current = null
      setPendingAgent(null)
      setThinkingPreview('')
      setEmptyResponse(false)
      sendMessage.resumeAction(threadId, decision, buildHandlers(assistantIndex))
      return
    }
    if (messages.length === 0) return
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
          <ChatBubble
            key={i}
            message={message}
            citations={message.citations}
            agentLabel={message.agentLabel}
          />
        ))}
        {sendMessage.isPending && assistantIndexRef.current === null && (
          <div className="flex justify-start">
            <div className="max-w-[75%] rounded-lg bg-surface-hover px-3 py-2 text-sm italic text-subtle">
              {thinkingPreview ||
                (pendingAgent ? `${pendingAgent.label} is looking into this…` : 'Thinking…')}
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

      {pendingAction && (
        <div className="mt-3 rounded-lg border border-line bg-surface-hover p-3">
          <p className="text-sm font-medium">{pendingAction.label}</p>
          <p className="mt-1 text-sm text-subtle">{pendingAction.summary}</p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => confirmPendingAction('confirm')}
              disabled={sendMessage.isPending}
              className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium disabled:opacity-50"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => confirmPendingAction('reject')}
              disabled={sendMessage.isPending}
              className="rounded-md border border-line px-3 py-1.5 text-sm disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask ARIA something…"
          disabled={sendMessage.isPending || pendingAction !== null}
          className="flex-1 rounded-md border border-line bg-transparent px-3 py-2"
        />
        <button
          type="submit"
          disabled={sendMessage.isPending || pendingAction !== null || !input.trim()}
          className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}
