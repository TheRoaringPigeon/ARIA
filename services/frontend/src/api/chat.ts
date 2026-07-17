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

export class AiServiceError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function sendChatMessage(
  messages: ChatMessage[],
): Promise<{ message: ChatMessage; citations: ChatCitation[] }> {
  const res = await fetch(`${AI_SERVICE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
    credentials: 'include',
  })

  if (!res.ok) {
    throw new AiServiceError(res.status, await parseErrorDetail(res))
  }

  const body = await res.json()
  return { message: body.message as ChatMessage, citations: (body.citations ?? []) as ChatCitation[] }
}
