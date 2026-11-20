import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Archive, ArchiveRestore, BookOpen, ChevronRight, Download, Ellipsis, GraduationCap, History, Layers, MessageCircle, PanelLeft, Pencil, Search, Settings, Trash2 } from 'lucide-react'
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from 'motion/react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent, DialogTrigger } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { UserMenu } from '@/components/arc/user-menu/user-menu'
import { motionTokens } from '@/components/arc/lib/motion-tokens'
import { ConversationDialogs, type ConversationDialogTarget } from './components/ConversationDialogs'
import { ProjectNavigation } from './components/ProjectNavigation'
import { SearchPanel } from './components/SearchPanel'
import { exportConversationMarkdown } from './conversationActions'
import type { AccountMode } from './components/Account'
import { WorkspaceData, useWorkspace, type NoticeOptions } from './WorkspaceData'
import { useTheme } from './Theme'
import { api } from './api/client'
import { ApiError } from './api/stream'
import type { User, Conversation } from './api/types'
import { allPages, errorMessage } from './api/ui'
import { clearAllChatDrafts } from './useChatDrafts'

const Home = lazy(() => import('./components/Home').then((m) => ({ default: m.Home })))
const Account = lazy(() => import('./components/Account').then((m) => ({ default: m.Account })))
const DemoChat = lazy(() => import('./components/DemoChat'))
const LiveChat = lazy(() => import('./components/LiveChat'))
const Library = lazy(() => import('./components/Library'))
const Learning = lazy(() => import('./components/Learning').then((m) => ({ default: m.Learning })))
const VisualOrbSmoke = lazy(() => import('./components/VisualOrbSmoke').then((m) => ({ default: m.VisualOrbSmoke })))
const SettingsPage = lazy(() => import('./components/Settings').then((m) => ({ default: m.Settings })))

const navigation = [
  { label: '新对话', path: '/', icon: MessageCircle },
  { label: '历史', path: '/history', icon: History },
  { label: '知识库', path: '/knowledge', icon: Layers },
  { label: '学习', path: '/learning', icon: GraduationCap },
  { label: '设置', path: '/settings', icon: Settings },
]

function LearningRoute({ notify }: { notify: (message: string) => void }) {
  const { user, libraries, overview, refresh, requireLogin } = useWorkspace()
  if (!user) {
    return <div className="placeholder-page"><EmptyState title="登录后使用学习" description="练习、拍照作答与错题本。" action={<Button onClick={requireLogin}>登录</Button>} /></div>
  }
  return <Learning knowledgeBases={libraries} notify={notify} overview={overview} refreshOverview={refresh} />
}

function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <div className="placeholder-page">
      <EmptyState title={title} description={description} action={<Link className="return-link" to="/">返回首页</Link>} />
    </div>
  )
}

function ChatRoute({ notify, searchButton }: { notify: (message: string) => void; searchButton: ReactNode }) {
  const { id } = useParams()
  if (id !== 'demo' && !/^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(id ?? '')) {
    return <Placeholder title="会话不存在" description="请检查访问地址，或返回首页。" />
  }
  return <Suspense fallback={<div className="placeholder-page" role="status">正在打开会话…</div>}>
    {id === 'demo' ? <DemoChat notify={notify} searchButton={searchButton} /> : <LiveChat key={id} id={id!} searchButton={searchButton} />}
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
  const [notice, setNotice] = useState<{ message: string; action?: NoticeOptions['action'] } | null>(null)
  const timer = useRef<number>(0)
  const reduced = useReducedMotion()
  const trigger = useRef<HTMLElement | null>(null)
  const destination = useRef('')
  const path = location.pathname + location.search
  const privatePage = !['/', '/chat/demo'].includes(location.pathname.replace(/\/+$/, '') || '/')

  function requireLogin(target?: string) {
    if (loginOpen) return
    void import('./components/Account')
    if (typeof target === 'string') destination.current = target
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setMode('login')
    setAuthError('')
    setLoginOpen(true)
  }

  function closeNotice() {
    window.clearTimeout(timer.current)
    setNotice(null)
  }

  function notify(message: string, options?: NoticeOptions) {
    window.clearTimeout(timer.current)
    setNotice({ message, action: options?.action })
    timer.current = window.setTimeout(closeNotice, options?.durationMs ?? (options?.action ? 5500 : 3600))
  }

  useEffect(() => () => window.clearTimeout(timer.current), [])
  useEffect(() => {
    let active = true
    void api.refresh().then(
      (account) => { if (active) { clearAllChatDrafts(account.id); setUser(account); setSessionState('ready') } },
      (error) => {
        if (!active) return
        const expired = error instanceof ApiError && error.status === 401
        if (expired) { clearAllChatDrafts(); setUser(null) }
        setSessionState(expired ? 'ready' : 'error')
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
      if (reset) { clearAllChatDrafts(); setUser(null); api.clearSession(); setSessionState('ready') }
      void navigate('/', { replace: true })
    } else if (!user && privatePage && sessionState === 'ready') {
      destination.current = path
      requireLogin()
      void navigate('/', { replace: true })
    }
  }, [location, user, sessionState])

  function signedIn(account: User) {
    clearAllChatDrafts(user && user.id !== account.id ? undefined : account.id)
    setUser(account)
    setSessionState('ready')
    setLoginOpen(false)
    if (destination.current) void navigate(destination.current, { replace: true })
    destination.current = ''
    setResetToken('')
  }

  return <>
    <WorkspaceData key={user?.id ?? 'guest'} user={user} notify={notify} requireLogin={requireLogin} onExpired={() => { clearAllChatDrafts(); api.clearSession(); closeNotice(); setUser(null) }}>
      <Shell onLogout={() => { clearAllChatDrafts(); closeNotice(); setUser(null) }} onPrivateLink={requireLogin} privatePage={privatePage && !user} sessionState={sessionState}
        retrySession={() => { setSessionState('loading'); setSessionAttempt((value) => value + 1) }} />
    </WorkspaceData>
    {loginOpen && (
      <Suspense fallback={null}>
        <Account open={loginOpen} onOpenChange={(open) => {
          setLoginOpen(open)
          if (!open) { destination.current = ''; setResetToken(''); setAuthError('') }
        }} onLogin={signedIn} mode={mode} setMode={(nextMode) => { setMode(nextMode); setAuthError('') }}
          next={destination.current || path} resetToken={resetToken} initialError={authError}
          returnFocus={() => { if (trigger.current?.isConnected) trigger.current.focus(); else document.querySelector<HTMLElement>('.nav-toggle')?.focus() }} />
      </Suspense>
    )}
    <div className="notice-region" role="status" aria-live="polite">
      <AnimatePresence mode="wait">
        {notice && (
          <motion.div key={notice.message} className="notice" initial={{ opacity: 0, y: reduced ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: reduced ? 0 : .2 }}>
            <span className="notice-message">{notice.message}</span>
            <div className="notice-actions">
              {notice.action && <Button variant="secondary" size="sm" onClick={() => { closeNotice(); notice.action?.onClick() }}>{notice.action.label}</Button>}
              <Button variant="ghost" size="sm" onClick={closeNotice}>关闭 <ChevronRight size={14} /></Button>
            </div>
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
  const { user, libraries, notify, report, requireLogin } = useWorkspace()
  const { theme, setTheme } = useTheme()
  const reduced = useReducedMotion()
  const [navOpen, setNavOpen] = useState(() => !window.matchMedia('(max-width: 480px)').matches)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchOrigin, setSearchOrigin] = useState({ top: 12, right: 12, x: 18, y: 18 })
  const searchTrigger = useRef<HTMLButtonElement>(null)
  const searchFocusReturn = useRef<HTMLElement | null>(null)
  const navigate = useNavigate()
  const { pathname, search } = useLocation()
  const workspacePage = navigation.find(({ path }) => path === pathname)
  const isChat = pathname.startsWith('/chat/')
  const isLibrary = pathname === '/knowledge'
  const libraryId = new URLSearchParams(search).get('id')
  const activeLibrary = libraryId ? libraries.find((library) => library.id === libraryId) : libraries[0]
  const accountMenu = user ? (
    <UserMenu
      user={{ name: user.display_name, email: user.email, avatarSrc: user.avatar_url ?? undefined }}
      showName
      showTheme
      theme={theme}
      onThemeChange={setTheme}
      align="start"
      items={[{ label: '设置', icon: <Settings size={16} />, onSelect: () => void navigate('/settings') }]}
      onSignOut={async () => {
        try {
          await api.logout()
          onLogout()
          void navigate('/')
        } catch (error) {
          report(error)
        }
      }}
    />
  ) : <Button variant="secondary" onClick={requireLogin}>登录</Button>

  function showSearch(focusReturn: HTMLElement | null) {
    if (!user) return requireLogin()
    const rect = searchTrigger.current?.getBoundingClientRect()
    if (rect) {
      const width = document.documentElement.clientWidth
      const right = Math.max(8, width - rect.right)
      const edge = Math.min(right, Math.max(8, width - 360))
      const top = Math.max(8, Math.min(rect.top, 72))
      setSearchOrigin({ top, right, x: width - edge - (rect.left + rect.width / 2), y: Math.max(0, rect.top + rect.height / 2 - top) })
    }
    searchFocusReturn.current = focusReturn && focusReturn !== document.body ? focusReturn : searchTrigger.current
    setSearchOpen(true)
  }

  useEffect(() => {
    function openSearch(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        if (document.querySelector('[role="dialog"]')) return
        showSearch(document.activeElement instanceof HTMLElement ? document.activeElement : null)
      }
    }
    window.addEventListener('keydown', openSearch)
    return () => window.removeEventListener('keydown', openSearch)
  }, [user, requireLogin])

  const searchButton = <DialogTrigger asChild>
    <Button ref={searchTrigger} variant="ghost" className="icon-button search-button" size="sm" aria-label="全局搜索"
      aria-keyshortcuts="Control+K Meta+K" title="搜索资料、笔记或对话（Ctrl+K）"><Search size={19} /></Button>
  </DialogTrigger>

  return (
    <Dialog open={searchOpen} onOpenChange={(open) => { if (open) showSearch(searchTrigger.current); else setSearchOpen(false) }}>
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
          <LayoutGroup id="workspace-navigation"><nav className="navigation" aria-label="工作区">
            {navigation.map(({ label, path, icon: Icon }) => (
              <NavLink key={path} to={path} end>{({ isActive }) => <>
                {isActive && (reduced
                  ? <span className="navigation-selection" aria-hidden="true" />
                  : <motion.span className="navigation-selection" aria-hidden="true"
                    layoutId="selection" transition={motionTokens.spring.morph} />)}
                <Icon size={21} strokeWidth={1.7} />{label}
              </>}</NavLink>
            ))}
          </nav></LayoutGroup>
          <ProjectNavigation />
          <div className="workspace-label">{accountMenu}</div>
        </div>
      </aside>
      <div className="main-shell">
          <header className="topbar" aria-label="工具栏">
            <Button variant="ghost" className="icon-button nav-toggle" size="sm" aria-label={navOpen ? '收起导航' : '展开导航'} aria-expanded={navOpen} aria-controls="sidebar" onClick={() => setNavOpen(!navOpen)}>
              <PanelLeft size={19} />
            </Button>
            {isLibrary && <div className="library-breadcrumb" aria-label="当前位置">
              <Link to="/knowledge">知识库</Link>
              {activeLibrary && <><ChevronRight size={14} /><strong>{activeLibrary.name}</strong></>}
            </div>}
            {!isChat && searchButton}
          </header>
        <main className="page">
          <Suspense fallback={<div className="placeholder-page" role="status">正在打开{pathname === '/' ? '首页' : workspacePage?.label ?? '页面'}…</div>}>
            <AnimatePresence initial={false}>
              <motion.div key={workspacePage ? pathname : 'other'} className="workspace-page"
                initial={workspacePage && !privatePage && !reduced ? { opacity: .6, top: 6 } : false}
                animate={{ opacity: 1, top: 0 }} transition={{ duration: reduced ? 0 : .22, ease: motionTokens.ease.enter }}>
                {privatePage ? sessionState === 'error'
                  ? <div className="placeholder-page"><EmptyState title="无法恢复登录状态" description="请检查连接后重试。" action={<Button onClick={retrySession}>重试</Button>} /></div>
                  : sessionState === 'loading' ? <div className="placeholder-page" role="status">正在检查登录状态…</div>
                    : <Home />
                : <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/chat/:id" element={<ChatRoute notify={notify} searchButton={searchButton} />} />
                  <Route path="/history" element={<HistoryPage />} />
                  <Route path="/knowledge" element={<Library preferredId={libraryId ?? undefined} />} />
                  <Route path="/learning" element={<LearningRoute notify={notify} />} />
                  <Route path="/settings" element={<SettingsPage user={user} requireLogin={requireLogin} />} />
                  <Route path="/__visual/orb" element={<Suspense fallback={<div className="placeholder-page" role="status">正在加载…</div>}><VisualOrbSmoke /></Suspense>} />
                  <Route path="*" element={<Placeholder title="页面不存在" description="请检查访问地址，或返回首页。" />} />
                </Routes>}
              </motion.div>
            </AnimatePresence>
          </Suspense>
        </main>
      </div>
    </div>
    <DialogContent title="搜索工作区" description="查找资料正文、研究笔记、知识库或对话标题。" closeLabel="关闭搜索"
      className="workspace-search" overlayClassName="workspace-search-overlay" fromIcon
      style={{ '--search-top': `${searchOrigin.top}px`, '--search-right': `${searchOrigin.right}px`, transformOrigin: `calc(100% - ${searchOrigin.x}px) ${searchOrigin.y}px` } as CSSProperties}
      onOpenAutoFocus={(event) => {
        const input = document.querySelector<HTMLInputElement>('.workspace-search input')
        if (input) { event.preventDefault(); input.focus({ preventScroll: true }) }
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault()
        if (searchFocusReturn.current?.isConnected) searchFocusReturn.current.focus({ preventScroll: true })
      }}>
      <SearchPanel onSelect={() => { searchFocusReturn.current = null; setSearchOpen(false) }} />
    </DialogContent>
    </Dialog>
  )
}

const historyWhen = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function HistoryPage() {
  const { user, revision, refresh, notify, report, requireLogin, chat } = useWorkspace()
  const [archived, setArchived] = useState(false)
  const [items, setItems] = useState<Conversation[] | null>(null)
  const [error, setError] = useState('')
  const [dialogTarget, setDialogTarget] = useState<ConversationDialogTarget | null>(null)
  useEffect(() => {
    if (!user) return
    let active = true
    void allPages((cursor) => api.conversations(cursor, undefined, archived)).then(
      (rows) => { if (active) { setItems(rows); setError('') } },
      (failure) => { if (active) { setError(errorMessage(failure)); report(failure) } },
    )
    return () => { active = false }
  }, [user, revision, archived])
  if (!user) return <div className="placeholder-page"><EmptyState title="登录后查看历史" description="打开已保存的会话。" action={<Button onClick={requireLogin}>登录</Button>} /></div>
  function changeHistory(next: boolean) {
    if (archived === next) return
    setItems(null)
    setError('')
    setArchived(next)
  }
  return <section className="resource-page history-page"><h1>会话历史</h1>
    <div className="history-tabs" role="group" aria-label="会话状态">
      <Button size="sm" variant={archived ? 'ghost' : 'secondary'} aria-pressed={!archived} onClick={() => changeHistory(false)}>未归档</Button>
      <Button size="sm" variant={archived ? 'secondary' : 'ghost'} aria-pressed={archived} onClick={() => changeHistory(true)}>已归档</Button>
    </div>
    {error ? <p role="alert">{error} <Button size="sm" onClick={refresh}>重试</Button></p>
      : !items ? <p role="status">正在读取历史…</p>
      : !items.length ? <p className="muted">{archived ? '暂无已归档的会话。' : '暂无会话历史。'}</p>
      : <ul className="resource-list history-list">
    {items.map((row) => <li key={row.id}>
      <Link to={`/chat/${row.id}`}>{row.title || '未命名对话'}</Link>
      <time className="history-meta" dateTime={row.updated_at}>{historyWhen.format(new Date(row.updated_at))}</time>
      <DropdownMenu
        label={`会话操作：${row.title || '未命名对话'}`}
        icon={<Ellipsis size={16} />}
        iconOnly
        modal={false}
        items={[
          archived
            ? { label: '恢复会话', icon: <ArchiveRestore size={15} />, onSelect: async () => {
                try {
                  await api.archiveConversation(row.id, false)
                  setItems((current) => current?.filter((item) => item.id !== row.id) ?? null)
                  refresh()
                  notify('会话已恢复。')
                } catch (failure) { report(failure) }
              } }
            : { label: '归档会话', icon: <Archive size={15} />, onSelect: () => setDialogTarget({ mode: 'archive', conversation: row }) },
          { label: '重命名会话', icon: <Pencil size={15} />, onSelect: () => setDialogTarget({ mode: 'rename', conversation: row }) },
          { label: '导出 Markdown', icon: <Download size={15} />, onSelect: async () => {
            try {
              await exportConversationMarkdown(row.id, { conversation: row })
              notify('已导出 Markdown 文件。')
            } catch (failure) { report(failure) }
          } },
          { label: '删除会话', icon: <Trash2 size={15} />, destructive: true, separatorBefore: true, onSelect: () => setDialogTarget({ mode: 'delete', conversation: row }) },
        ]}
      />
    </li>)}
  </ul>}
  <ConversationDialogs
    target={dialogTarget}
    onClose={() => setDialogTarget(null)}
    onRenamed={(updated) => {
      setItems((current) => current ? current.map((item) => item.id === updated.id ? updated : item) : current)
      refresh()
    }}
    onArchived={(updated) => {
      setItems((current) => current?.filter((item) => item.id !== updated.id) ?? null)
      refresh()
    }}
    onBeforeDelete={(deletedId) => chat?.evictConversation?.(deletedId)}
    onDeleted={(deletedId) => {
      chat?.evictConversation?.(deletedId)
      setItems((current) => current ? current.filter((item) => item.id !== deletedId) : current)
      refresh()
    }}
    report={report}
    notify={notify}
  />
  </section>
}
