import api from './client'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  reply: string
}

export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const { data } = await api.post('/assistant/chat', { messages })
  return data
}
