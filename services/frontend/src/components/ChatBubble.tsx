// No rehype-raw plugin — react-markdown only interprets markdown syntax and
// never renders raw HTML tags from the (untrusted, model-generated) content.
import ReactMarkdown, { type Components } from 'react-markdown'
import type { ChatCitation, ChatMessage } from '../api/chat'
import { downloadUrl } from '../api/documents'

const markdownComponents: Components = {
  p: ({ children }) => <p className="mt-2 first:mt-0">{children}</p>,
  ul: ({ children }) => <ul className="mt-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="mt-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  code: ({ children }) => <code className="rounded bg-active px-1 py-0.5 text-xs">{children}</code>,
}

export function ChatBubble({
  message,
  citations,
  agentLabel,
}: {
  message: ChatMessage
  citations?: ChatCitation[]
  agentLabel?: string
}) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
          isUser ? 'bg-primary text-white' : 'bg-surface-hover'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            {agentLabel && <p className="mb-1 text-xs font-medium text-subtle">{agentLabel}</p>}
            <ReactMarkdown components={markdownComponents}>{message.content}</ReactMarkdown>
            {citations && citations.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5 border-t border-divider pt-2">
                {citations.map((citation, i) => (
                  <a
                    key={`${citation.document_id}-${citation.page_number}-${i}`}
                    href={downloadUrl(citation.document_id)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-full bg-active px-2 py-0.5 text-xs text-subtle hover:underline"
                  >
                    {citation.filename}, p.{citation.page_number}
                  </a>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
