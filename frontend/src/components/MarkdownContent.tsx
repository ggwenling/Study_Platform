import { useEffect, useState } from 'react'
import Markdown, { type Options } from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function MarkdownContent({ children }: { children: string }) {
  const [rehypePlugins, setRehypePlugins] = useState<NonNullable<Options['rehypePlugins']>>([])

  useEffect(() => {
    let active = true
    void Promise.all([
      import('rehype-highlight'),
      import('highlight.js/styles/github-dark.css'),
    ]).then(([mod]) => {
      if (!active) return
      setRehypePlugins([mod.default])
    })
    return () => { active = false }
  }, [])

  return (
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins}>
      {children}
    </Markdown>
  )
}