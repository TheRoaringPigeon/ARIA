import { useMutation } from '@tanstack/react-query'
import * as api from '../api/chat'
import type { ChatMessage } from '../api/chat'

export function useSendChatMessage() {
  return useMutation({
    mutationFn: (messages: ChatMessage[]) => api.sendChatMessage(messages),
  })
}
