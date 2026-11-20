import { useEffect, useRef, useState } from 'react'
import type { DragEvent, FormEvent, ReactNode } from 'react'
import { ChevronRight, CircleDashed, Ellipsis, FilePlus2, FileText, Folder, Globe, Image as ImageIcon, Layers, Pencil, Plus, RefreshCw, Tag, Trash2, TriangleAlert, Upload } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router'
import { api } from '@/api/client'
import type { Citation, DocumentLocation, Fragment, KnowledgeBase, LibraryOverview, Material } from '@/api/types'
import { allPages, errorMessage, locatorLabel, safeExternalUrl } from '@/api/ui'
import { ApiError } from '@/api/stream'
import { PasteTextDialog } from './PasteTextDialog'
import { WebClipDialog } from './WebClipDialog'
import { ActivityHeatmap } from '@/components/arc/activity-heatmap/activity-heatmap'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Input } from '@/components/arc/input/input'
import { Stepper } from '@/components/arc/stepper/stepper'
import { Textarea } from '@/components/arc/textarea/textarea'
import { TreeView } from '@/components/arc/tree-view/tree-view'
import type { TreeNode } from '@/components/arc/tree-view/tree-view'
import { useWorkspace } from '../WorkspaceData'
import { LibraryClassification } from './LibraryClassification'
import { LibraryFragments, number } from './LibraryFragments'
import { LibraryStudio } from './LibraryStudio'
import { LibraryHitTest } from './LibraryHitTest'
import { SourceReaderDrawer, type ReaderActionContext, type SourceReaderTarget } from './SourceReaderDrawer'
import './Library.css'

const phases: Record<string, string> = {
  queued: '等待处理', parsing: '解析中', ocr: '识别文字中', indexing: '建立索引中',
  ready: '已完成', failed: '处理失败', cancelled: '已取消',
}

type Panel = 'create' | 'edit' | 'deleteKb' | 'topics' | 'tags' | 'files' | 'ocr' | 'ocrQueue' | 'failed' | 'activity'
type DocumentScope = { material_id?: string; section_id?: string; label?: string }

function heading(icon: ReactNode, title: string, note: string) {
  return <header className="library-section-heading">{icon}<h2>{title}</h2><p>{note}</p></header>
}

export default function Library({ preferredId }: { preferredId?: string }) {
  const { user, libraries, loading, refresh, notify, report, requireLogin } = useWorkspace()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const fragmentId = params.get('fragment')
  const noteId = fragmentId ? null : params.get('note')
  const [created, setCreated] = useState<KnowledgeBase>()
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const options = created && !libraries.some((item) => item.id === created.id) ? [created, ...libraries] : libraries
  const library = preferredId ? options.find((item) => item.id === preferredId) : options[0]
  const select = (id: string) => void navigate(`/knowledge?id=${encodeURIComponent(id)}`)
  const closeSearchResult = () => { if (library) setParams({ id: library.id }, { replace: true }) }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (createBusy) return
    const form = new FormData(event.currentTarget)
    const name = String(form.get('name')).trim()
    if (!name) return
    setCreateBusy(true)
    try {
      const item = await api.createKnowledgeBase({ name, description: String(form.get('description')).trim() || null })
      setCreated(item)
      setCreating(false)
      refresh()
      notify('知识库已创建。')
      select(item.id)
    } catch (error) { report(error) } finally { setCreateBusy(false) }
  }

  if (!user) return <div className="placeholder-page"><EmptyState title="登录后管理知识库" description="在个人空间创建知识库并导入资料。" action={<Button onClick={requireLogin}>登录</Button>} /></div>
  return <>
    {library ? <LibraryDetail key={library.id} library={library} libraries={options} select={select} create={() => setCreating(true)}
      fragmentId={fragmentId} noteId={noteId} closeSearchResult={closeSearchResult} onDeleted={() => {
      if (created?.id === library.id) setCreated(undefined)
      const next = options.find((item) => item.id !== library.id)
      void navigate(next ? `/knowledge?id=${encodeURIComponent(next.id)}` : '/knowledge', { replace: true })
    }} />
      : <div className="placeholder-page">
        {loading ? <p role="status">正在读取知识库…</p> : <EmptyState title={fragmentId || noteId ? '内容已不可用' : preferredId ? '知识库不可用' : '暂无知识库'}
          description={preferredId ? '请切换到可访问的知识库。' : '创建第一个知识库，开始整理个人资料。'}
          action={options.length ? <Button onClick={() => select(options[0]!.id)}>切换知识库</Button> : <Button onClick={() => setCreating(true)}>创建知识库</Button>} />}
      </div>}
    <Dialog open={creating} onOpenChange={setCreating}>
      <DialogContent title="创建知识库" closeLabel="关闭">
        <LibraryForm onSubmit={create} busy={createBusy} submit="创建知识库" />
      </DialogContent>
    </Dialog>
  </>
}

function LibraryForm({ library, onSubmit, busy, submit }: {
  library?: KnowledgeBase; onSubmit: (event: FormEvent<HTMLFormElement>) => void; busy: boolean; submit: string;
}) {
  return <form className="library-dialog-stack" onSubmit={onSubmit}>
    <Input label="名称" name="name" defaultValue={library?.name} maxLength={200} required />
    <Textarea label="描述" name="description" defaultValue={library?.description ?? ''} maxLength={2000} />
    <Button type="submit" loading={busy} disabled={busy}>{submit}</Button>
  </form>
}

function LibraryDetail({ library, libraries, select, create, onDeleted, fragmentId, noteId, closeSearchResult }: {
  library: KnowledgeBase; libraries: KnowledgeBase[]; select: (id: string) => void; create: () => void; onDeleted: () => void;
  fragmentId: string | null; noteId: string | null; closeSearchResult: () => void;
}) {
  const { revision, refresh, notify, report, libraryUploadQueue: uploadQueue } = useWorkspace()
  const navigate = useNavigate()
  const id = library.id
  const [overview, setOverview] = useState<LibraryOverview>()
  const [materials, setMaterials] = useState<Material[]>([])
  const [failure, setFailure] = useState('')
  const [reload, setReload] = useState(0)
  const [fragmentRevision, setFragmentRevision] = useState(0)
  const [busy, setBusy] = useState<string>()
  const [panel, setPanel] = useState<Panel>()
  const [pasteOpen, setPasteOpen] = useState(false)
  const [webClipOpen, setWebClipOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [confirmMaterial, setConfirmMaterial] = useState<Material | null>(null)
  const [scope, setScope] = useState<DocumentScope>({})
  const [day, setDay] = useState<string>()
  const [readerTarget, setReaderTarget] = useState<SourceReaderTarget | null>(null)
  const [fragmentError, setFragmentError] = useState('')
  useEffect(() => {
    if (!fragmentId) { setFragmentError(''); return }
    let live = true
    setReaderTarget(null)
    setFragmentError('')
    void api.fragment(id, fragmentId).then(
      (fragment) => {
        if (!live) return
        setReaderTarget({
          materialVersionId: fragment.version_id,
          materialId: fragment.material_id,
          knowledgeBaseId: id,
          knowledgeBaseName: library.name,
          chunkId: fragment.id,
          title: fragment.title,
          quote: fragment.text,
          locator: fragment.locator,
          locationLabel: locatorLabel(fragment.locator),
        })
      },
      (error: unknown) => {
        if (!live) return
        const failure = error instanceof ApiError && [404, 422].includes(error.status ?? 0) ? '内容已不可用' : errorMessage(error)
        setFragmentError(failure)
      },
    )
    return () => { live = false }
  }, [id, fragmentId])
  const fileInput = useRef<HTMLInputElement>(null)
  const fragmentPanel = useRef<HTMLElement>(null)
  const [expanded, setExpanded] = useState([id])
  const [locations, setLocations] = useState<Record<string, { key: string; rows: DocumentLocation[] | 'failed' }>>({})
  const inFlightLocations = useRef(new Set<string>())

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    let timer: number
    async function load() {
      try {
        const [summary, rows] = await Promise.all([
          api.libraryOverview(id, signal),
          allPages((cursor) => api.materials(id, cursor, signal)),
        ])
        if (signal.aborted) return
        setFragmentRevision((value) => value + 1)
        setOverview(summary)
        setMaterials(rows)
        setFailure('')
        if (summary.processing_count) timer = window.setTimeout(load, 2200)
      } catch (error) { if (!signal.aborted) setFailure(errorMessage(error)) }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [id, revision, reload])

  useEffect(() => {
    for (const material of materials) {
      if (!expanded.includes(material.id) || material.status !== 'ready') continue
      const key = `${material.id}:${material.version_id}`
      if (locations[material.id]?.key === key || inFlightLocations.current.has(key)) continue
      inFlightLocations.current.add(key)
      void api.documentLocations(id, material.id).then(
        (rows) => setLocations((current) => ({ ...current, [material.id]: { key, rows } })),
        () => setLocations((current) => ({ ...current, [material.id]: { key, rows: 'failed' } })),
      ).finally(() => inFlightLocations.current.delete(key))
    }
  }, [expanded, id, locations, materials])

  const reloadData = () => setReload((value) => value + 1)
  const addFiles = (files: FileList | File[]) => uploadQueue.enqueue(files, { id, name: library.name })
  const failedUploads = uploadQueue.items.filter((row) => row.status === 'failed')

  function onDragOver(event: DragEvent<HTMLElement>) {
    if (!Array.from(event.dataTransfer?.types ?? []).includes('Files')) return
    event.preventDefault()
    if (!dragging) setDragging(true)
  }

  function onDragLeave(event: DragEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    setDragging(false)
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    if (!event.dataTransfer?.files?.length) return
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
  }
  const selectDay = (value: string | null) => { setDay(value ?? undefined); setPanel(undefined) }
  const scrollToFragments = () => fragmentPanel.current?.scrollIntoView({ block: 'nearest' })

  async function sync() {
    setBusy('sync')
    try {
      setOverview(await api.syncLibrary(id))
      setLocations({})
      reloadData()
      notify('索引状态已刷新。')
    } catch (error) { report(error) } finally { setBusy(undefined) }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const name = String(form.get('name')).trim()
    if (!name || busy) return
    setBusy('edit')
    try {
      await api.updateKnowledgeBase(id, { name, description: String(form.get('description')).trim() || null })
      setPanel(undefined)
      refresh()
      notify('知识库已更新。')
    } catch (error) { report(error) } finally { setBusy(undefined) }
  }

  async function retry(material: Material) {
    if (busy) return
    setBusy(material.id)
    try { await api.retryMaterial(material.id); reloadData(); notify('已重新提交处理。') }
    catch (error) { report(error) } finally { setBusy(undefined) }
  }

  async function cancelJob(material: Material) {
    if (busy || !material.job_id) return
    setBusy(`cancel:${material.id}`)
    try {
      await api.cancelJob(material.job_id)
      reloadData()
      refresh()
      notify('已取消资料处理。')
    } catch (error) { report(error) } finally { setBusy(undefined) }
  }

  async function removeMaterial(material: Material) {
    if (busy) return
    setBusy(`delete:${material.id}`)
    try {
      await api.deleteMaterial(material.id)
      if (scope.material_id === material.id) setScope({})
      setLocations((current) => { const next = { ...current }; delete next[material.id]; return next })
      setConfirmMaterial(null)
      reloadData()
      refresh()
      notify('资料已删除。')
    } catch (error) { report(error) } finally { setBusy(undefined) }
  }

  async function removeLibrary() {
    if (busy) return
    setBusy('deleteKb')
    try {
      await api.deleteKnowledgeBase(id)
      setPanel(undefined)
      refresh()
      notify('知识库已删除。')
      onDeleted()
    } catch (error) { report(error) } finally { setBusy(undefined) }
  }

  function openSource(target: SourceReaderTarget) {
    setReaderTarget({ knowledgeBaseId: id, knowledgeBaseName: library.name, ...target })
  }

  function openCitation(citation: Citation) {
    if (citation.availability !== 'available') {
      notify('此来源已不可访问。')
      return
    }
    openSource({
      materialVersionId: citation.material_version_id,
      materialId: citation.material_id,
      knowledgeBaseId: citation.knowledge_base_id,
      chunkId: citation.chunk_id,
      title: citation.title,
      quote: citation.quote,
      locator: citation.locator,
      locationLabel: citation.location_label,
      sourceUrl: citation.source_url,
    })
  }

  function openFragment(fragment: Fragment) {
    openSource({
      materialVersionId: fragment.version_id,
      materialId: fragment.material_id,
      chunkId: fragment.id,
      title: fragment.title,
      quote: fragment.text,
      locator: fragment.locator,
      locationLabel: locatorLabel(fragment.locator),
    })
  }

  const roots: TreeNode[] = [{ id, label: library.name, icon: <Folder size={15} />, children: materials.map((material) => {
    const entries = locations[material.id]?.rows
    const children = entries === 'failed' ? [{ id: `retry:${material.id}`, label: '位置读取失败，点击重试' }]
      : entries ? entries.map((row) => ({ id: `${material.id}:${row.id}`, label: `${row.heading ?? locatorLabel(row.locator)}（${row.count}）` }))
      : [{ id: `loading:${material.id}`, label: '正在读取位置…' }]
    return { id: material.id, label: material.title, children: material.status === 'ready' ? children : undefined }
  }) }]

  function chooseNode(node: TreeNode) {
    if (node.id === id) return setScope({})
    const [materialId, sectionId] = node.id.split(':')
    if (materialId === 'retry' && sectionId) {
      setLocations((current) => { const next = { ...current }; delete next[sectionId]; return next })
      return
    }
    if (materialId === 'loading') return
    setScope({ material_id: materialId, section_id: sectionId, label: node.label })
  }

  const cards: { label: string; icon: ReactNode; count?: number; note: string; action: () => void }[] = [
    { label: '主题', icon: <Layers size={21} />, count: overview?.topics.length, note: '资料的主要分类', action: () => setPanel('topics') },
    { label: '标签', icon: <Tag size={21} />, count: overview?.tags.length, note: '用于检索与进一步筛选', action: () => setPanel('tags') },
    { label: '片段', icon: <FileText size={21} />, count: overview?.fragment_count, note: '已切分并建立索引的文本', action: scrollToFragments },
    { label: 'OCR 图片', icon: <ImageIcon size={21} />, count: overview?.ocr_count, note: '已识别并提取文字的图片和扫描页', action: () => setPanel('ocr') },
  ]
  const failed = materials.filter((row) => ['failed', 'cancelled'].includes(row.status))
  const panelMaterials = panel === 'failed' ? failed : panel === 'ocr' ? materials.filter((row) => overview?.ocr_material_ids?.includes(row.id))
    : panel === 'ocrQueue' ? materials.filter((row) => row.status === 'ocr') : materials
  const heatmap = (selectable = true) => overview ? <ActivityHeatmap className="library-heatmap" days={overview.activity} label="资料活动" period="最近 12 周" weekStartsOn={1} locale="zh-CN"
    unit={{ one: '次索引', other: '次索引' }} selectedDate={day} onSelectDate={selectable ? (value) => { selectDay(value); scrollToFragments() } : undefined} /> : <p className="muted">暂无活动记录</p>

  if (!overview) return failure ? <div className="placeholder-page">
    <EmptyState title="无法读取知识库" description={failure} action={<Button onClick={() => { setFailure(''); reloadData() }}>重试</Button>} />
  </div> : <LibraryLoading />

  return <section className="library-detail library-detail-ready" aria-label="知识库详情" data-dragging={dragging || undefined} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
    <div className="orbit-light library-orbit" aria-hidden="true" />
    <div className="library-main">
      <header className="library-header">
        <Layers size={32} />
        <div><h1>{library.name}</h1>
          {library.embedding_binding && <p>向量模型 · {library.embedding_binding.model} · {library.embedding_connection_id ? '个人连接' : '平台连接'} · 1024 维</p>}{library.description && <p>{library.description}</p>}</div>
        <div className="library-header-actions">
          <Button size="sm" variant="secondary" onClick={() => setPanel('edit')}><Pencil size={16} />编辑</Button>
          <Button size="sm" variant="secondary" loading={busy === 'sync'} disabled={Boolean(busy)} onClick={() => void sync()}><RefreshCw size={16} />同步</Button>
          <Button size="sm" variant="secondary" onClick={() => setWebClipOpen(true)}><Globe size={16} />导入网页</Button>
          <DropdownMenu label="知识库操作" icon={<Ellipsis size={17} />} iconOnly disabled={Boolean(busy)} items={[
            { label: '导入资料', icon: <Upload size={16} />, onSelect: () => fileInput.current?.click() },
            { label: '随手贴文本', icon: <FilePlus2 size={16} />, onSelect: () => setPasteOpen(true) },
            { label: '管理资料', icon: <FileText size={16} />, onSelect: () => { setConfirmMaterial(null); setPanel('files') } },
            { label: '新建知识库', icon: <Plus size={16} />, onSelect: create },
            ...libraries.filter((item) => item.id !== id).map((item, index) => ({ label: item.name, icon: <Folder size={16} />, separatorBefore: index === 0, onSelect: () => select(item.id) })),
            { label: '删除知识库', icon: <Trash2 size={16} />, destructive: true, separatorBefore: true, onSelect: () => setPanel('deleteKb') },
          ]} />
        </div>
        <input ref={fileInput} type="file" multiple hidden accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg" onChange={(event) => {
          const files = event.target.files ? Array.from(event.target.files) : []
          event.target.value = ''
          if (files.length) addFiles(files)
        }} />
      </header>
      {dragging && <p className="library-processing-banner" role="status">松开鼠标，将文件批量导入「{library.name}」。</p>}
      {overview && overview.processing_count > 0 && (
        <p className="library-processing-banner" role="status">
          <CircleDashed size={18} aria-hidden="true" />
          {overview.processing_count} 份资料正在处理，页面会自动刷新状态。
        </p>
      )}
      {uploadQueue.latestNote && <p role="status" className="muted library-upload-note">{uploadQueue.latestNote}</p>}
      {failedUploads.length > 0 && (
        <div className="upload-queue-failures library-upload-note">
          {failedUploads.map((item) => (
            <span key={item.id} className="upload-failure-item">
              <span>「{item.name}」上传至「{item.libraryName}」失败</span>
              <Button type="button" size="sm" variant="secondary" onClick={() => uploadQueue.retryItem(item.id)}>重试上传</Button>
            </span>
          ))}
        </div>
      )}
      {failure && <p className="library-empty" role="alert">{failure} <Button size="sm" onClick={reloadData}>重试</Button></p>}
      <section className="library-panel">
        {heading(<Layers size={22} />, '知识结构', '从文档、主题、标签到片段的结构化视图')}
        <div className="library-structure">
          <div className="library-documents">
            <h3>
              <Folder size={18} />
              <span>文档层级</span>
              {materials.length > 0 && <Button size="sm" variant="ghost" onClick={() => { setConfirmMaterial(null); setPanel('files') }}>管理资料</Button>}
            </h3>
            {materials.length ? <TreeView density="compact" nodes={roots} expandedIds={expanded} onExpandedChange={setExpanded} onSelect={chooseNode} aria-label="文档层级" />
              : <p className="muted library-empty">{overview ? '暂无资料。可从页头菜单导入。' : '正在读取资料…'}</p>}
          </div>
          <div className="library-metrics">
            {cards.map((card) => <Button key={card.label} variant="secondary" className="library-metric" onClick={card.action}>
              <span className="library-metric-content">
                <span>{card.icon}{card.label}<ChevronRight size={16} /></span>
                <strong className="numeric">{card.count === undefined ? '—' : number.format(card.count)}</strong>
                <small>{card.note}</small>
              </span>
            </Button>)}
          </div>
        </div>
      </section>
      <section className="library-panel">
        {heading(<Layers size={22} />, '索引流程', '知识库的索引构建与处理状态')}
        <LibraryProgress overview={overview} />
      </section>
      <LibraryStudio
        kbId={id}
        kbName={library.name}
        readyCount={overview?.ready_count ?? 0}
        readyMaterials={materials.filter((material) => material.status === 'ready')}
        revision={fragmentRevision}
        linkedNoteId={noteId}
        closeLinkedNote={closeSearchResult}
        onIngested={reloadData}
        onOpenSource={openCitation}
      />
      <LibraryHitTest
        kbId={id}
        readyCount={overview?.ready_count ?? 0}
        onOpenSource={(item) => openSource({ materialVersionId: item.version_id, materialId: item.material_id, chunkId: item.chunk_id, title: item.title, quote: item.text, locator: item.locator, locationLabel: item.location_label })}
        onFragmentUpdated={() => setFragmentRevision((value) => value + 1)}
      />
      <section className="library-panel library-fragment-panel" ref={fragmentPanel}>
        {heading(<FileText size={22} />, '片段浏览', '检索与管理已生成的内容片段')}
        {fragmentError && <p role="alert" className="error-text">{fragmentError}</p>}
        <LibraryFragments id={id} tags={overview?.tags ?? []} day={day} setDay={setDay} scope={scope}
          clearScope={() => setScope({})} revision={fragmentRevision} onOpenSource={openFragment} />
      </section>
    </div>
    <aside className="library-panel library-aside" aria-label="资料库活动与索引概况">
      <h2><button className="library-aside-heading" type="button" onClick={() => setPanel('activity')}>资料库活动<ChevronRight size={16} /></button></h2>
      <p className="library-aside-note">近 12 周的资料索引活动</p>
      {heatmap()}
      <div className="library-aside-summary">
        <h2><Layers size={21} />索引概况</h2>
        {[
          { label: '文件总数', icon: <Folder size={19} />, value: overview?.file_count, action: () => setPanel('files') },
          { label: '就绪片段', icon: <FileText size={19} />, value: overview?.fragment_count, action: scrollToFragments },
          { label: 'OCR 队列', icon: <CircleDashed size={19} />, value: overview?.ocr_queue, action: () => setPanel('ocrQueue') },
          { label: '处理失败', icon: <TriangleAlert size={19} />, value: overview?.failed_count, action: () => setPanel('failed') },
        ].map((item) => <button key={item.label} type="button" className="library-aside-row" onClick={item.action}>
          {item.icon}<span>{item.label}</span><span className="numeric">{item.value === undefined ? '—' : number.format(item.value)}</span><ChevronRight size={15} />
        </button>)}
        <div className="library-aside-row"><RefreshCw size={18} /><span>最后同步</span>
          <span className="numeric">{overview?.last_synced_at ? new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
          }).format(new Date(overview.last_synced_at)) : '—'}</span>
        </div>
      </div>
    </aside>
    <Dialog open={Boolean(panel)} onOpenChange={(open) => {
      if (!open && !busy) {
        if (fragmentId) closeSearchResult()
        setConfirmMaterial(null)
        setPanel(undefined)
      }
    }}>
      {panel === 'edit' && <DialogContent title="编辑知识库" closeLabel="关闭">
        <LibraryForm library={library} onSubmit={save} busy={busy === 'edit'} submit="保存" />
      </DialogContent>}
      {panel === 'deleteKb' && <DialogContent
        title="删除知识库"
        description="删除后该知识库内的资料与索引将被停用。"
        closeLabel="关闭删除知识库"
      >
        <div className="stack-form">
          <p>即将删除知识库「{library.name}」（共 {number.format(overview?.file_count ?? materials.length)} 份资料）。</p>
          <p className="muted">删除后：库内资料与向量索引立即停用，处理中的任务自动取消，关联的项目会话解除绑定，历史问答中的对应引用将显示为「来源已不可用」。</p>
          <div className="form-actions">
            <Button type="button" variant="danger" loading={busy === 'deleteKb'} onClick={() => void removeLibrary()}>确认删除知识库</Button>
            <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setPanel(undefined)}>取消</Button>
          </div>
        </div>
      </DialogContent>}
      {(panel === 'topics' || panel === 'tags') && <DialogContent title={panel === 'topics' ? '主题管理' : '标签管理'} closeLabel="关闭">
        <LibraryClassification id={id} materials={materials} labels={overview?.[panel] ?? []} onChange={reloadData} />
      </DialogContent>}
      {panel === 'activity' && overview && <DialogContent className="library-activity-dialog"
        title="索引活动" description="最近 12 周各日期的资料索引次数" closeLabel="关闭索引活动">
        <div className="library-activity-stats">
          <div><span>索引次数</span><strong>{number.format(overview.activity.reduce((total, day) => total + day.count, 0))}</strong></div>
          <div><span>活跃天数</span><strong>{number.format(overview.activity.filter((day) => day.count > 0).length)}</strong></div>
        </div>
        <div className="library-activity-chart">{heatmap(false)}</div>
      </DialogContent>}
      {(panel === 'files' || panel === 'failed' || panel === 'ocr' || panel === 'ocrQueue') && <DialogContent
        title={confirmMaterial ? '删除资料' : panel === 'files' ? '资料列表' : panel === 'failed' ? '处理失败的资料' : panel === 'ocrQueue' ? 'OCR 队列' : '已识别的资料'} closeLabel="关闭">
        {confirmMaterial ? (
          <div className="stack-form">
            <p>确认删除资料「{confirmMaterial.title}」吗？删除后该资料不再参与检索，历史引用将显示为不可用。</p>
            <div className="form-actions">
              <Button type="button" variant="danger" loading={busy === `delete:${confirmMaterial.id}`} disabled={Boolean(busy) && busy !== `delete:${confirmMaterial.id}`} onClick={() => void removeMaterial(confirmMaterial)}>确认删除</Button>
              <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setConfirmMaterial(null)}>返回列表</Button>
            </div>
          </div>
        ) : <>
          <ul className="resource-list library-file-list">
            {panelMaterials.map((item) => {
              const inProgress = !['ready', 'failed', 'cancelled'].includes(item.status)
              const sourceUrl = safeExternalUrl(item.source_url)
              return <li key={item.id}>
                <div><strong>{item.title}</strong><p className="muted">{phases[item.status] ?? item.status}</p>
                  {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer">打开原网页</a>}
                  {item.error_message && <p className="error-text">{item.error_message}</p>}</div>
                <div className="library-dialog-actions">
                  {item.status === 'ready' && item.version_id && <Button size="sm" variant="secondary" disabled={Boolean(busy)} onClick={() => openSource({ materialVersionId: item.version_id!, materialId: item.id, title: item.title })}>查看原文</Button>}
                  {inProgress && item.job_id && <Button size="sm" variant="secondary" loading={busy === `cancel:${item.id}`} disabled={Boolean(busy)} onClick={() => void cancelJob(item)}>取消处理</Button>}
                  {['failed', 'cancelled'].includes(item.status) && <Button size="sm" variant="secondary" loading={busy === item.id} disabled={Boolean(busy)} onClick={() => void retry(item)}>重试处理</Button>}
                  <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => setConfirmMaterial(item)}>删除</Button>
                </div>
              </li>
            })}
          </ul>
          {panelMaterials.length === 0 && <p className="muted">暂无符合条件的资料。</p>}
        </>}
      </DialogContent>}
    </Dialog>
    <PasteTextDialog
      open={pasteOpen}
      onOpenChange={setPasteOpen}
      libraryName={library.name}
      onSubmitFile={(file) => addFiles([file])}
    />
    <WebClipDialog open={webClipOpen} onOpenChange={setWebClipOpen} libraryId={id} libraryName={library.name} />
    <SourceReaderDrawer open={Boolean(readerTarget)} target={readerTarget} onOpenChange={(open) => {
      if (!open) {
        setReaderTarget(null)
        if (fragmentId) closeSearchResult()
      }
    }} onAskFollowUp={(context: ReaderActionContext) => {
      const prompt = `请根据以下资料摘录回答我的问题。\n\n来源：${context.title} · ${context.locationLabel}\n\n${context.excerpt}\n\n我的问题：`
      void navigate('/', { state: { preferredLibrary: context.knowledgeBaseId, initialDraft: prompt, initialMaterialIds: [context.materialId] } })
    }} />
  </section>
}

function LibraryLoading() {
  return <div className="library-detail library-loading" role="status">
    <span className="library-loading-label">正在读取知识库…</span>
    <div className="library-main" aria-hidden="true">
      <div className="library-skeleton library-skeleton-heading" />
      <div className="library-skeleton library-skeleton-structure" />
      <div className="library-skeleton library-skeleton-panel" />
      <div className="library-skeleton library-skeleton-panel" />
    </div>
    <div className="library-skeleton library-skeleton-aside" aria-hidden="true" />
  </div>
}

function LibraryProgress({ overview }: { overview?: LibraryOverview }) {
  const total = overview?.file_count ?? 0
  const ready = overview?.ready_count ?? 0
  const cut = overview?.cut_count ?? 0
  const done = total > 0 && ready === total
  const active = !total ? 0 : done ? 6 : overview?.ocr_queue ? 1
    : overview?.parsed_count !== total ? 2 : cut === 0 ? 3 : overview?.embedded_count !== cut ? 4 : 5
  const steps = [
    { id: 'upload', label: '上传完成', description: overview ? `${number.format(total)} 个文件` : '无记录' },
    { id: 'ocr', label: 'OCR 识别', description: overview?.ocr_queue ? `${overview.ocr_queue} 个任务处理中` : overview?.ocr_count ? `${number.format(overview.ocr_count)} 张图片` : done ? '不适用' : '无记录' },
    { id: 'parse', label: '内容解析', description: overview ? `${number.format(overview.parsed_count)} / ${number.format(total)} 个文件` : '无记录' },
    { id: 'cut', label: '内容切分', description: overview ? `${number.format(cut)} 个片段` : '无记录' },
    { id: 'embed', label: '向量化', description: overview ? `${number.format(overview.embedded_count)} / ${number.format(cut)}` : '无记录' },
    { id: 'ready', label: '索引就绪', description: done ? '已完成' : overview ? `${number.format(ready)} / ${number.format(total)} 个文件` : '无记录',
      error: overview?.failed_count ? `${number.format(overview.failed_count)} 个文件处理失败` : undefined },
  ]
  return <Stepper label="索引流程" className="library-stepper" steps={steps} current={active} completeLabel="索引已完成" />
}
