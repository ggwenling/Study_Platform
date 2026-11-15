import { useEffect, useState } from 'react'
import { Copy, ExternalLink, Folder, MessageCircle, Plus } from 'lucide-react'
import { NavLink, useLocation, useNavigate } from 'react-router'
import { api } from '@/api/client'
import type { Conversation, KnowledgeBase, Page } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { ContextMenu } from '@/components/arc/context-menu/context-menu'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { useWorkspace } from '../WorkspaceData'
import { useDisclosurePreference } from '../useDisclosurePreference'

export function ProjectNavigation() {
  const { user, libraries, recent, loading, refresh, requireLogin, report } = useWorkspace()
  const [recentOpen, setRecentOpen] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:recent`, true)
  const [createOpen, setCreateOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <div className="sidebar-sections">
    <section className="recent">
      <Accordion openIndex={recentOpen ? 0 : -1} onOpenChange={(index) => setRecentOpen(index === 0)} items={[{ title: '最近对话', content: <>
        {!recent.length && <p>{loading ? '正在读取…' : user ? '暂无最近对话' : '登录后查看最近对话'}</p>}
        {recent.slice(0, 6).map((row) => <NavLink key={row.id} className="recent-link" to={`/chat/${row.id}`}>{row.title || '未命名对话'}</NavLink>)}
        <NavLink to="/chat/demo" className="demo-link">查看示例会话 ↗</NavLink>
      </> }]} />
    </section>
    <section className="projects" aria-label="项目">
      <div className="projects-heading"><h2>项目</h2><Button variant="ghost" size="sm" aria-label="新建项目" onClick={() => { if (!user) return requireLogin(); setError(''); setCreateOpen(true) }}><Plus size={15} /></Button></div>
      {libraries.map((kb) => <ProjectFolder key={kb.id} kb={kb} />)}
      {user && !loading && !libraries.length && <p className="muted">创建知识库，整理项目会话。</p>}
    </section>
    <Dialog open={createOpen} onOpenChange={(open) => { if (!busy) setCreateOpen(open) }}>
      <DialogContent title="新建项目" description="创建一个知识库，作为项目的资料与会话空间。" closeLabel="关闭新建项目">
        <form className="stack-form" onSubmit={async (event) => {
          event.preventDefault(); if (busy) return
          const name = String(new FormData(event.currentTarget).get('name')).trim()
          setBusy(true); setError('')
          try { await api.createKnowledgeBase({ name }); refresh(); setCreateOpen(false) } catch (failure) { setError(errorMessage(failure)); report(failure) } finally { setBusy(false) }
        }}><Input label="项目名称" name="name" required maxLength={200} />{error && <p className="error-text" role="alert">{error}</p>}<Button type="submit" loading={busy}>创建项目</Button></form>
      </DialogContent>
    </Dialog>
  </div>
}

function ProjectFolder({ kb }: { kb: KnowledgeBase }) {
  const { user, revision, refresh, report, notify } = useWorkspace()
  const [open, setOpen] = useDisclosurePreference(`knowloop:${user!.id}:project:${kb.id}`, false)
  const [page, setPage] = useState<Page<Conversation> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  useEffect(() => {
    if (!open) return
    let current = true
    setPage(null); setError('')
    void api.conversations(undefined, kb.id).then((value) => { if (current) setPage(value) }, (failure) => { if (current) { setError(errorMessage(failure)); report(failure) } })
    return () => { current = false }
  }, [open, kb.id, revision, attempt])
  return <div className="project-folder"><Accordion openIndex={open ? 0 : -1} onOpenChange={(index) => setOpen(index === 0)} items={[{
    title: <span className="project-name"><Folder size={17} /><span>{kb.name}</span></span>, content: <div className="project-conversations">
      <Button variant="ghost" size="sm" loading={busy} onClick={async () => {
        setBusy(true)
        try { const row = await api.createConversation({ project_knowledge_base_id: kb.id }); refresh(); void navigate(`/chat/${row.id}`) } catch (failure) { report(failure) } finally { setBusy(false) }
      }}><Plus size={14} />新对话</Button>
      {error ? <><p className="error-text" role="alert">{error}</p><Button variant="ghost" size="sm" onClick={() => setAttempt((value) => value + 1)}>重试</Button></> : !page ? <p role="status">正在读取…</p> : <>
        {page.items.map((row) => <ContextMenu
          key={row.id}
          label={`会话：${row.title || '未命名对话'}`}
          items={[
            { id: 'open', label: '打开会话', icon: <MessageCircle size={15} />, onSelect: () => void navigate(`/chat/${row.id}`) },
            { id: 'new-tab', label: '在新标签页打开', icon: <ExternalLink size={15} />, onSelect: () => { window.open(`/chat/${row.id}`, '_blank', 'noopener,noreferrer') } },
            { id: 'copy', label: '复制链接', icon: <Copy size={15} />, onSelect: async () => {
              try {
                await navigator.clipboard.writeText(new URL(`/chat/${row.id}`, window.location.origin).href)
                notify('已复制会话链接。')
              } catch { notify('无法访问剪贴板。') }
            } },
          ]}
        ><span className="recent-link" aria-current={pathname === `/chat/${row.id}` ? 'page' : undefined}>{row.title || '未命名对话'}</span></ContextMenu>)}
        {!page.items.length && <p className="muted">暂无会话</p>}
        {page.next_cursor && <Button variant="ghost" size="sm" loading={busy} onClick={async () => {
          setBusy(true)
          try { const next = await api.conversations(page.next_cursor!, kb.id); setPage({ ...next, items: [...page.items, ...next.items] }) } catch (failure) { report(failure) } finally { setBusy(false) }
        }}>加载更多</Button>}
      </>}
    </div>,
  }]} /></div>
}
