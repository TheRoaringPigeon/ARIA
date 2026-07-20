import { parseErrorDetail } from './client'

const AI_SERVICE_URL = import.meta.env.VITE_AI_SERVICE_URL ?? 'http://localhost:8001'

export type ChatRole = 'user' | 'assistant'

// The wire type resent as `messages` on every turn (`ai-service`'s
// `ChatRequest.messages` rejects extra fields) — never add citations or
// other response-only data to this shape.
export interface ChatMessage {
  role: ChatRole
  content: string
}

export interface ChatCitation {
  document_id: string
  filename: string
  page_number: number
  section_header: string | null
}

export interface ChatAgent {
  name: string
  label: string
}

// M8 write path — a proposed create_log/create_schedule action awaiting
// confirm/cancel. `threadId` is reused across exactly the propose request
// and the follow-up resume request (see `ChatResume`), never kept beyond
// that pair.
export interface ProposedAction {
  threadId: string
  tool: string
  label: string
  summary: string
}

// Wire shape for `ChatRequest.resume` (snake_case, matching every other
// field on the wire) — sent instead of real message history, since the
// graph's own state is already checkpointed under `threadId`.
export interface ChatResume {
  threadId: string
  decision: 'confirm' | 'reject'
}

export class AiServiceError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface StreamChatHandlers {
  onAgent?: (agent: ChatAgent) => void
  onActionProposed?: (action: ProposedAction) => void
  onCitations: (citations: ChatCitation[]) => void
  onThinking: (delta: string) => void
  onToken: (delta: string) => void
}

function dispatchFrame(frame: string, handlers: StreamChatHandlers): void {
  let event: string | null = null
  let data: string | null = null
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice('event: '.length)
    else if (line.startsWith('data: ')) data = line.slice('data: '.length)
  }
  if (!event || data === null) return

  const parsed = JSON.parse(data)
  switch (event) {
    case 'agent':
      handlers.onAgent?.(parsed as ChatAgent)
      return
    case 'action_proposed':
      handlers.onActionProposed?.({
        threadId: parsed.thread_id as string,
        tool: parsed.tool as string,
        label: parsed.label as string,
        summary: parsed.summary as string,
      })
      return
    case 'citations':
      handlers.onCitations(parsed.citations as ChatCitation[])
      return
    case 'thinking':
      handlers.onThinking(parsed.content as string)
      return
    case 'token':
      handlers.onToken(parsed.content as string)
      return
    case 'error':
      // ai-service has already committed a 200 status by the time this
      // can happen — a downstream model failure can only be reported as
      // an in-stream frame, not an HTTP status code.
      throw new AiServiceError(200, parsed.detail as string)
  }
}

// `EventSource` can't be used here — it only supports GET with no request
// body, and `/chat` needs a POST body plus the session cookie
// (`credentials: 'include'`). So SSE framing is parsed by hand over
// `fetch`'s `ReadableStream` instead.
export async function streamChatMessage(
  messages: ChatMessage[],
  handlers: StreamChatHandlers,
  signal: AbortSignal,
  resume?: ChatResume,
): Promise<void> {
  const body = resume
    ? { messages, resume: { thread_id: resume.threadId, decision: resume.decision } }
    : { messages }
  const res = await fetch(`${AI_SERVICE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
    signal,
  })

  if (!res.ok) {
    throw new AiServiceError(res.status, await parseErrorDetail(res))
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let frameEnd: number
      while ((frameEnd = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, frameEnd)
        buffer = buffer.slice(frameEnd + 2)
        if (frame) dispatchFrame(frame, handlers)
      }
    }
  } finally {
    reader.releaseLock()
  }
}
