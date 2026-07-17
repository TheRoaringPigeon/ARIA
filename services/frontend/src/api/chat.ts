import { parseErrorDetail } from './client'

const AI_SERVICE_URL = import.meta.env.VITE_AI_SERVICE_URL ?? 'http://localhost:8001'

export type ChatRole = 'user' | 'assistant'

export interface ChatMessage {
  role: ChatRole
  content: string
}

export class AiServiceError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function sendChatMessage(messages: ChatMessage[]): Promise<ChatMessage> {
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
  return body.message as ChatMessage
}
