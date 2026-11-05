import type { ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

/**
 * A model's reply, which it writes as Markdown, drawn as the page it means.
 *
 * Why every element is styled here and not by Tailwind's typography plugin: the reply sits
 * in a narrow side panel in a theme that may be dark, glass or anything a person made, and
 * the plugin's sizes and colours are tuned for articles. The same sizes as the rest of the
 * panel keep a reply looking like part of it.
 *
 * Raw HTML in a reply is never rendered (react-markdown leaves it as text, and rewrites a
 * `javascript:` link to nothing), so no sanitiser is needed. A reply can only produce the
 * elements listed here.
 */

const heading = (size: string) =>
  function Heading({ children }: { children?: ReactNode }) {
    return (
      <p
        className={`mt-3 mb-1 font-semibold text-gray-900 first:mt-0 dark:text-gray-100 ${size}`}
      >
        {children}
      </p>
    )
  }

const components: Components = {
  // Headings become bold lines, not page headings: the panel already has its own, and a
  // reply's "# Summary" must not outrank the title above the conversation. The panel is
  // not a document, so these are not real h1-h6 either.
  h1: heading('text-base'),
  h2: heading('text-sm'),
  h3: heading('text-sm'),
  h4: heading('text-sm'),
  h5: heading('text-sm'),
  h6: heading('text-sm'),
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc space-y-0.5 pl-5 first:mt-0 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-0.5 pl-5 first:mt-0 last:mb-0">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="[&>ol]:my-1 [&>p]:my-0 [&>ul]:my-1">{children}</li>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="opacity-70">{children}</del>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-gray-300 pl-3 text-gray-600 dark:border-gray-600 dark:text-gray-400">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-gray-200 dark:border-gray-700" />,
  // A fenced block is `pre > code`; the code rule below tints inline code, so the block
  // resets it. Long lines scroll inside the block instead of widening the panel.
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded bg-gray-100 p-2 text-xs dark:bg-gray-900 [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-gray-700">
      {children}
    </code>
  ),
  a: ({ href, children }) => {
    const className = 'text-blue-600 underline hover:no-underline dark:text-blue-400'
    // A path inside this app moves within it; anything else leaves in a new tab, with no
    // access back to this page.
    if (href?.startsWith('/') && !href.startsWith('//'))
      return (
        <Link to={href} className={className}>
          {children}
        </Link>
      )
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
        {children}
      </a>
    )
  },
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-gray-100 dark:bg-gray-700">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border border-gray-300 px-2 py-1 text-left font-semibold dark:border-gray-600">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-gray-300 px-2 py-1 dark:border-gray-600">{children}</td>
  ),
}

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="break-words text-gray-900 dark:text-gray-100">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
