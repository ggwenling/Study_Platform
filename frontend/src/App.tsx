import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ArrowUpRight, BookOpen, ChevronRight, History, Layers, MessageCircle, PanelLeft, Search, Settings } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent, DialogTrigger } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Home } from './components/Home'
import { Account } from './components/Account'
import type { AccountMode } from './components/Account'
import { WorkspaceData, useWorkspace } from './WorkspaceData'
import { api } from './api/client'
import { ApiError } from './api/stream'
import type { SearchItem, User, Conversation } from './api/types'
import { allPages, errorMessage } from './api/ui'

const DemoChat = lazy(() => import('./components/DemoChat'))
const LiveChat = lazy(() => import('./components/LiveChat'))
const Library = lazy(() => import('./components/Library'))

const navigation = [
  { label: '新对话', path: '/', icon: MessageCircle },
  { label: '历史', path: '/history', icon: History },
  { label: '知识库', path: '/knowledge', icon: Layers },
  { label: '设置', path: '/settings', icon: Settings },
]

function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <div className="placeholder-page">
      <EmptyState title={title} description={description} action={<Link className="return-link" to="/">返回首页</Link>} />
    </div>
  )
}

function ChatRoute({ notify }: { notify: (message: string) => void }) {
  const { id } = useParams()
  if (id !== 'demo' && !/^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(id ?? '')) {
    return <Placeholder title="会话不存在" description="请检查访问地址，或返回首页。" />
  }
  return <Suspense fallback={<div className="placeholder-page" role="status">正在打开会话…</div>}>
    {id === 'demo' ? <DemoChat notify={notify} /> : <LiveChat key={id} id={id!} />}
  </Suspense>
}

export function App() {
  const [user, setUser] = useState<User | null>(null)
  const location = useLocation()
  const navigate = useNavigate()
  const [sessionState, setSessionState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [sessionAttempt, setSessionAttempt] = useState(0)
  const [loginOpen, setLoginOpen] = useState(false)
  const [mode, setMode] = useState<AccountMode>('login')
  const [resetToken, setResetToken] = useState('')
  const [authError, setAuthError] = useState('')
  const [notice, setNotice] = useState('')
  const timer = useRef<number>(0)
  const reduced = useReducedMotion()
  const trigger = useRef<HTMLElement | null>(null)
  const destination = useRef('')
  const path = location.pathname + location.search
  const privatePage = !['/', '/chat/demo'].includes(location.pathname.replace(/\/+$/, '') || '/')

  function requireLogin(target?: string) {
    if (loginOpen) return
    if (typeof target === 'string') destination.current = target
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setMode('login')
    setAuthError('')
    setLoginOpen(true)
  }

  function notify(message: string) {
    window.clearTimeout(timer.current)
    setNotice(message)
    timer.current = window.setTimeout(() => setNotice(''), 3600)
  }

  useEffect(() => () => window.clearTimeout(timer.current), [])
  useEffect(() => {
    let active = true
    void api.refresh().then(
      (account) => { if (active) { setUser(account); setSessionState('ready') } },
      (error) => {
        if (!active) return
        setSessionState(error instanceof ApiError && error.status === 401 ? 'ready' : 'error')
      },
    )
    return () => { active = false }
  }, [sessionAttempt])

  useEffect(() => {
    if (sessionState === 'loading') return
    const params = new URLSearchParams(location.search)
    const auth = params.get('auth')
    const reset = new URLSearchParams(location.hash.slice(1)).get('reset')
    if (reset || auth === 'google_link' || auth === 'google_error') {
      params.delete('auth')
      const clean = location.pathname + (params.size ? '?' + params : '')
      destination.current = clean
      setResetToken(reset ?? '')
      setMode(reset ? 'reset' : auth === 'google_link' ? 'google_link' : 'login')
      setAuthError(auth === 'google_error' ? 'Google 登录未完成，请重试。' : '')
      setLoginOpen(true)
      if (reset) { setUser(null); api.clearSession(); setSessionState('ready') }
      void navigate('/', { replace: true })
    } else if (!user && privatePage && sessionState === 'ready') {
      destination.current = path
      requireLogin()
      void navigate('/', { replace: true })
    }
  }, [location, user, sessionState])

  function signedIn(account: User) {
    setUser(account)
    setSessionState('ready')
    setLoginOpen(false)
    if (destination.current) void navigate(destination.current, { replace: true })
    destination.current = ''
    setResetToken('')
  }

  return <>
    <WorkspaceData key={user?.id ?? 'guest'} user={user} notify={notify} requireLogin={requireLogin} onExpired={() => { api.clearSession(); setUser(null) }}>
      <Shell onLogout={() => setUser(null)} onPrivateLink={requireLogin} privatePage={privatePage && !user} sessionState={sessionState}
        retrySession={() => { setSessionState('loading'); setSessionAttempt((value) => value + 1) }} />
    </WorkspaceData>
    <Account open={loginOpen} onOpenChange={(open) => {
      setLoginOpen(open)
      if (!open) { destination.current = ''; setResetToken(''); setAuthError('') }
    }} onLogin={signedIn} mode={mode} setMode={(nextMode) => { setMode(nextMode); setAuthError('') }}
      next={destination.current || path} resetToken={resetToken} initialError={authError}
      returnFocus={() => { if (trigger.current?.isConnected) trigger.current.focus(); else document.querySelector<HTMLElement>('.nav-toggle')?.focus() }} />
    <div className="notice-region" role="status" aria-live="polite">
      <AnimatePresence mode="wait">
        {notice && (
          <motion.div key={notice} className="notice" initial={{ opacity: 0, y: reduced ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: reduced ? 0 : .2 }}>
            {notice}<Button variant="ghost" size="sm" onClick={() => setNotice('')}>关闭 <ChevronRight size={14} /></Button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  </>
}

function Shell({ onLogout, onPrivateLink, privatePage, sessionState, retrySession }: {
  onLogout: () => void
  onPrivateLink: (path: string) => void
  privatePage: boolean
  sessionState: 'loading' | 'ready' | 'error'
  retrySession: () => void
}) {
  const { user, recent, loading, notify, report, requireLogin } = useWorkspace()
  const [navOpen, setNavOpen] = useState(() => !window.matchMedia('(max-width: 480px)').matches)
  const [searchOpen, setSearchOpen] = useState(false)
  const navigate = useNavigate()
  const { pathname, search } = useLocation()
  const isChat = pathname.startsWith('/chat/')
  const accountItems = [
    { label: '设置', icon: <Settings size={16} />, onSelect: () => user ? navigate('/settings') : onPrivateLink('/settings') },
    user
      ? { label: '退出登录', onSelect: async () => { try { await api.logout(); onLogout(); void navigate('/') } catch (error) { report(error) } } }
      : { label: '登录 / 注册', onSelect: requireLogin },
  ]

  useEffect(() => {
    function openSearch(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        if (document.querySelector('[role="dialog"]')) return
        if (user) setSearchOpen(true)
        else requireLogin()
      }
    }
    window.addEventListener('keydown', openSearch)
    return () => window.removeEventListener('keydown', openSearch)
  }, [user, requireLogin])

  return (
    <Dialog open={searchOpen} onOpenChange={(open) => { if (open && !user) requireLogin(); else setSearchOpen(open) }}>
    <div className="app-shell" data-nav={navOpen} data-chat={isChat} onClickCapture={(event) => {
      if (user || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !(event.target instanceof Element)) return
      const link = event.target.closest<HTMLAnchorElement>('a[href^="/"]')
      if (!link || ['/', '/chat/demo'].includes(link.pathname)) return
      event.preventDefault()
      if (sessionState === 'ready') onPrivateLink(link.pathname + link.search)
      else void navigate(link.pathname + link.search)
    }}>
      <aside className="sidebar" id="sidebar" aria-label="主导航" inert={!navOpen} aria-hidden={!navOpen}>
        <div className="sidebar-inner">
          <Link to="/" className="brand" aria-label="知循首页">
            <BookOpen size={29} strokeWidth={1.8} /><strong>知循</strong><span>KnowLoop</span>
          </Link>
          <nav className="navigation" aria-label="工作区">
            {navigation.map(({ label, path, icon: Icon }) => (
              <NavLink key={path} to={path} end><Icon size={21} strokeWidth={1.7} />{label}</NavLink>
            ))}
            {isChat && (
              <DialogTrigger asChild>
                <Button variant="ghost" className="sidebar-search"><Search size={21} strokeWidth={1.7} />搜索会话</Button>
              </DialogTrigger>
            )}
          </nav>
          <section className="recent">
            <h2>最近对话</h2>
            {!recent.length && <p>{loading ? '正在读取…' : user ? '暂无最近对话' : '登录后查看最近对话'}</p>}
            {recent.slice(0, 6).map((conversation) => <NavLink key={conversation.id} className="recent-link" to={`/chat/${conversation.id}`}>{conversation.title || '未命名对话'}</NavLink>)}
            <NavLink to="/chat/demo" className="demo-link">查看示例会话 <ArrowUpRight size={14} /></NavLink>
          </section>
          {isChat && <div className="workspace-label"><DropdownMenu label={user?.display_name || '个人空间'} icon={<span className="account-avatar">{user?.display_name.slice(0, 1) || 'K'}</span>} items={accountItems} /></div>}
        </div>
      </aside>
      <div className="main-shell">
          <header className="topbar" aria-label="工具栏">
            <Button variant="ghost" className="icon-button nav-toggle" size="sm" aria-label={navOpen ? '收起导航' : '展开导航'} aria-expanded={navOpen} aria-controls="sidebar" onClick={() => setNavOpen(!navOpen)}>
              <PanelLeft size={19} />
            </Button>
            {!isChat && <div className="topbar-actions">
              <DialogTrigger asChild>
                <Button variant="secondary" className="search-button"><Search size={17} /><span>搜索知识库、资料或对话…</span><kbd>Ctrl K</kbd></Button>
              </DialogTrigger>
              <div className="account-menu">
                {user
                  ? <DropdownMenu label={user.display_name} icon={<span className="account-avatar">{user.display_name.slice(0, 1)}</span>} items={accountItems} />
                  : <Button variant="secondary" onClick={requireLogin}>登录</Button>}
              </div>
            </div>}
          </header>
        <main className="page">
          {privatePage ? sessionState === 'error'
            ? <div className="placeholder-page"><EmptyState title="无法恢复登录状态" description="请检查连接后重试。" action={<Button onClick={retrySession}>重试</Button>} /></div>
            : sessionState === 'loading' ? <div className="placeholder-page" role="status">正在检查登录状态…</div> : <Home />
          : <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/chat/:id" element={<ChatRoute notify={notify} />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/knowledge" element={<Suspense fallback={<div className="placeholder-page" role="status">正在打开知识库…</div>}><Library key={search} preferredId={new URLSearchParams(search).get('id') ?? undefined} /></Suspense>} />
            <Route path="/settings" element={<Placeholder title="设置暂未开放" description="个人偏好将在这里管理。" />} />
            <Route path="*" element={<Placeholder title="页面不存在" description="请检查访问地址，或返回首页。" />} />
          </Routes>}
        </main>
      </div>
    </div>
    <DialogContent title="搜索" description="搜索知识库、资料或对话。">
      {searchOpen && <SearchPanel onSelect={() => setSearchOpen(false)} />}
    </DialogContent>
    </Dialog>
  )
}

function HistoryPage() {
  const { user, revision, report, requireLogin } = useWorkspace()
  const [items, setItems] = useState<Conversation[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!user) return
    let active = true
    void allPages(api.conversations).then(
      (rows) => { if (active) { setItems(rows); setError('') } },
      (failure) => { if (active) { setError(errorMessage(failure)); report(failure) } },
    )
    return () => { active = false }
  }, [user, revision])
  if (!user) return <div className="placeholder-page"><EmptyState title="登录后查看历史" description="打开已保存的会话。" action={<Button onClick={requireLogin}>登录</Button>} /></div>
  if (error) return <Placeholder title="暂时无法读取历史" description={error} />
  if (!items) return <div className="placeholder-page" role="status">正在读取历史…</div>
  if (!items.length) return <Placeholder title="暂无会话历史" description="从首页开始第一轮问答。" />
  return <section className="resource-page"><h1>会话历史</h1><ul className="resource-list">
    {items.map((row) => <li key={row.id}><Link to={`/chat/${row.id}`}>{row.title || '未命名对话'}</Link></li>)}
  </ul></section>
}

function SearchPanel({ onSelect }: { onSelect: () => void }) {
  const { user, requireLogin, report } = useWorkspace()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[] | null>(null)
  const [busy, setBusy] = useState(false)

  if (!user) return <EmptyState title="登录后搜索" description="搜索你的知识库、资料或对话。" action={<Button onClick={() => { onSelect(); requireLogin() }}>登录</Button>} />

  return <div className="search-panel">
    <form className="stack-form" onSubmit={async (event) => {
      event.preventDefault()
      if (!query.trim() || busy) return
      setBusy(true)
      setResults(null)
      try { setResults(await allPages((cursor) => api.search(query.trim(), cursor))) } catch (error) { report(error) } finally { setBusy(false) }
    }}>
      <label>关键词<input value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} /></label>
      <Button type="submit" loading={busy}>搜索</Button>
    </form>
    {results?.length ? <ul className="resource-list">
      {results.map((item) => <li key={`${item.type}-${item.id}`}><Link to={item.type === 'conversation' ? `/chat/${item.id}` : `/knowledge?id=${item.knowledge_base_id ?? item.id}`} onClick={onSelect}>{item.title}</Link></li>)}
    </ul> : results && <p className="muted">暂无匹配结果。</p>}
  </div>
}
