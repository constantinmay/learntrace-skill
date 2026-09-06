import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const markdownPlugins = typeof remarkGfm === 'function' ? [remarkGfm] : []

export function MarkdownView({ children, onCitation }: { children: string; onCitation?: (citationId: string) => void }) {
  return <ReactMarkdown
    remarkPlugins={markdownPlugins}
    components={{
      a: ({ href, children: label }) => {
        const external = Boolean(href && /^(https?:)?\/\//i.test(href))
        return <a href={href} {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}>{label}</a>
      },
      code: ({ children: value, className }) => {
        const text = String(value).replace(/\n$/, '')
        const citation = !className && /^evt-[A-Za-z0-9_.:-]+$/.test(text)
        return citation && onCitation
          ? <button type="button" className="citation-link" onClick={() => onCitation(text)}>{text}</button>
          : <code className={className}>{value}</code>
      },
    }}
  >{children}</ReactMarkdown>
}
