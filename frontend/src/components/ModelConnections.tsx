import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowDownWideNarrow, CircleDot, Database, Ellipsis, Eye, MessageCircle, Plus, Server, ShieldCheck, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import { errorMessage } from '@/api/ui'
import type { ModelConnection, ModelConnections } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { Input } from '@/components/arc/input/input'
import { PasswordField } from '@/components/arc/password-field/password-field'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'
import './ModelConnections.css'

const statusLabel = { draft: '待测试', testing: '测试中', ready: '已测试', failed: '测试失败' }
const modelGroups = {
  llm: { label: 'LLM 文本模型', icon: MessageCircle,
    description: '用于对话、摘要和学习。默认变更对下一次新提问生效。',
    scope: '正在生成的回答和原轮次重试仍沿用原连接。',
    updated: '默认文本模型已更新，将用于下一次新提问。' },
  embedding: { label: '向量模型 · 1024 维', icon: Database,
    description: '用于知识库检索。默认变更仅影响之后新建的知识库。',
    scope: '已有知识库保持原绑定；更换默认不会迁移已有资料。',
    updated: '默认向量模型已更新，仅影响之后新建的知识库。' },
  vision: { label: '视觉理解模型（VLM）', icon: Eye,
    description: '按需识别图片或 PDF 中的图表、手写文字和公式。',
    scope: '启用连接后，仍须在上传、重新解析或拍照判题时主动选用。',
    updated: '默认视觉模型已更新。' },
  rerank: { label: '重排模型（Rerank）', icon: ArrowDownWideNarrow,
    description: '对知识库粗排候选片段进行精排，提升问答证据相关性。',
    scope: '仅在启用了 Rerank 的知识库检索中使用；服务异常时问答会回退到 RRF。',
    updated: '默认 Rerank 连接已更新。' },
} satisfies Record<ModelConnection['kind'], { label: string; icon: typeof Eye; description: string; scope: string; updated: string }>
const connectionTypes = ['llm', 'embedding', 'vision', 'rerank'] as const
const fixes: Record<string, string> = {
  model_auth_failed: '认证失败。请检查 API Key 和模型访问权限。',
  model_quota_unavailable: '余额不足或请求受限。请检查供应商额度，稍后重试。',
  model_timeout: '连接超时。请检查服务状态与服务器网络。',
  model_service_error: '服务返回错误。请确认 Base URL 指向兼容的 API 入口。',
  model_response_invalid: '协议不兼容或返回内容无效。请确认模型支持所选连接类型。',
  embedding_invalid: '向量返回不符合要求。请确认支持 1024 维及批量请求。',
  embedding_dimension_invalid: '向量维度不匹配。请使用支持 1024 维输出的模型。',
  vision_model_unsupported: '图像输入未被接受。请检查模型名称，确认该模型支持多模态输入。',
  vision_response_invalid: '视觉模型没有返回可用文字。请检查多模态协议与模型配置。',
  rerank_not_configured: '尚未配置可用的 Rerank 连接。',
  rerank_response_invalid: 'Rerank 返回格式无效。请确认接口支持标准 /rerank 协议。',
  rerank_service_error: 'Rerank 服务请求失败。请检查接口地址和模型配置。',
  model_address_blocked: '网络地址未获允许。内网或 HTTP 地址需要管理员配置放行。',
  model_dns_failed: '地址无法解析。请检查 Base URL。',
  model_decryption_failed: '凭据无法解密。请联系管理员或重新填写 API Key。',
}

export function ModelConnectionsPanel() {
  const { notify, report, refresh } = useWorkspace()
  const [data, setData] = useState<ModelConnections>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [editing, setEditing] = useState<ModelConnection | 'new'>()
  const [deleting, setDeleting] = useState<ModelConnection>()
  const [kind, setKind] = useState<ModelConnection['kind']>('llm')
  const [busy, setBusy] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const addRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const [editSession, setEditSession] = useState(0)
  const credentialsReady = Boolean(data?.encryption_available)
  function closeEditor() {
    const key = formRef.current?.elements.namedItem('api_key') as HTMLInputElement | null
    if (key) key.value = ''
    setEditing(undefined)
  }
  function rememberTrigger(row?: ModelConnection) {
    returnFocus.current = row ? document.getElementById(`model-connection-${row.id}`)?.querySelector('button') ?? null : addRef.current
  }
  function restoreFocus(event: Event) {
    event.preventDefault()
    const target = returnFocus.current?.isConnected ? returnFocus.current : addRef.current
    target?.focus()
  }
  useEffect(() => {
    let active = true
    let timer: number
    async function load() {
      try {
        const result = await api.modelConnections()
        if (!active) return
        setData(result)
        setError('')
        if (result.items.some((row) => row.status === 'testing')) timer = window.setTimeout(load, 1800)
      } catch (failure) { if (active) setError(errorMessage(failure)) }
    }
    void load()
    return () => { active = false; window.clearTimeout(timer) }
  }, [reload])
  const load = () => { setReload((value) => value + 1); refresh() }

  async function choose(kind: ModelConnection['kind'], id: string | null) {
    setBusy(id ?? kind)
    try { await api.defaultModelConnection(kind, id); notify(modelGroups[kind].updated); load() }
    catch (failure) { report(failure) } finally { setBusy('') }
  }
  async function test(row: ModelConnection) {
    setBusy(row.id)
    setData((value) => value && { ...value, items: value.items.map((item) => item.id === row.id ? { ...item, status: 'testing' } : item) })
    try {
      const result = await api.testModelConnection(row.id)
      notify(result.status === 'ready' ? row.is_default ? '默认连接测试成功。' : '连接测试成功，可设为默认。' : fixes[result.error_code ?? ''] ?? '测试失败，请检查配置。')
    } catch (failure) { report(failure) } finally { setBusy(''); load() }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!credentialsReady || busy) return
    const form = new FormData(event.currentTarget)
    const apiKey = String(form.get('api_key') ?? '').trim()
    const body = { kind, name: String(form.get('name')).trim(), base_url: String(form.get('base_url')).trim(),
      model: String(form.get('model')).trim(), ...(apiKey ? { api_key: apiKey } : {}) }
    setBusy('save')
    try {
      if (editing && editing !== 'new') await api.updateModelConnection(editing.id, body)
      else await api.createModelConnection(body)
      closeEditor()
      notify('连接已保存，请测试后启用。')
      load()
    } catch (failure) { report(failure) } finally { setBusy('') }
  }
  function open(row?: ModelConnection) {
    rememberTrigger(row)
    setEditSession((value) => value + 1)
    setKind(row?.kind ?? 'llm')
    setEditing(row ?? 'new')
  }

  function connection(row: ModelConnection) {
    const testLabel = row.status === 'failed' ? '重新测试' : '测试连接'
    return <article key={row.id} id={`model-connection-${row.id}`} className="model-connection" data-default={row.is_default || undefined} aria-label={`连接：${row.name}`}>
      {row.is_default && <p className="model-current"><CircleDot size={14} aria-hidden="true" />当前默认 · 个人连接</p>}
      <div className="model-connection-heading">
        <strong>{row.name}</strong>
        <span className={`model-status status-${row.status}`} role="status">{row.status === 'failed' && <TriangleAlert size={13} aria-hidden="true" />}{statusLabel[row.status]}</span>
        <DropdownMenu label={`管理连接 ${row.name}`} icon={<Ellipsis size={16} />} iconOnly items={[
          { label: '删除连接', disabled: Boolean(busy), destructive: true, onSelect: () => { rememberTrigger(row); setDeleting(row) } },
        ]} />
      </div>
      <p className="model-name">{row.model}</p>
      <p className="model-address">{row.base_url}</p>
      <div className="model-facts">
        <span className="model-key"><ShieldCheck size={13} aria-hidden="true" />{row.key_configured ? `Key 已加密 · 尾号 ${row.key_suffix}` : 'Key 未配置'}</span>
        {row.is_default && row.tested_at && <span>最近测试 {new Date(row.tested_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>}
      </div>
      {row.libraries.length > 0 && <p className="model-bound">绑定 {row.libraries.length} 个知识库 · {row.libraries.map((library) => library.name).join('、')}</p>}
      {row.status === 'failed' && <p className="model-warning">{fixes[row.error_code ?? ''] ?? '连接不可用，请检查配置并重新测试。'}</p>}
      {row.is_default && row.status === 'draft' && <p className="model-warning">配置已更改，请重新测试后再使用此默认连接。</p>}
      <div className="model-actions">
        <Button size="sm" variant="secondary" aria-label={`${testLabel}：${row.name}`} loading={busy === row.id} disabled={Boolean(busy) || row.status === 'testing' || !credentialsReady} onClick={() => void test(row)}>{testLabel}</Button>
        <Button size="sm" variant="ghost" aria-label={`编辑连接：${row.name}`} disabled={Boolean(busy) || !credentialsReady} onClick={() => open(row)}>编辑</Button>
        {row.is_default
          ? <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => void choose(row.kind, null)}>{row.kind === 'vision' || row.kind === 'rerank' ? '清除默认连接' : '恢复平台默认'}</Button>
          : <Button size="sm" variant="ghost" aria-label={`设为默认：${row.name}`} disabled={Boolean(busy) || row.status !== 'ready' || !credentialsReady} onClick={() => void choose(row.kind, row.id)}>设为默认</Button>}
      </div>
    </article>
  }

  return <section className="model-settings" aria-labelledby="model-settings-title">
    <header><div><h2 id="model-settings-title"><Server size={20} />模型连接</h2>
      <p>查看当前默认模型，管理个人连接。连接测试会产生少量调用。</p></div>
      <Button ref={addRef} variant="secondary" onClick={() => open()} disabled={Boolean(busy) || !credentialsReady}><Plus size={16} />添加连接</Button>
    </header>
    {error && <p role="alert">{error} <Button size="sm" onClick={load}>重试</Button></p>}
    {!data && !error && <p role="status">正在读取模型连接…</p>}
    {data && !credentialsReady && <div role="alert" className="model-encryption-warning"><TriangleAlert size={18} aria-hidden="true" /><p>管理员尚未配置有效的模型凭据加密密钥，暂时无法新增、编辑、测试或启用个人连接。你仍可查看连接、恢复平台默认或删除未使用的连接。</p></div>}
    {data && <div className="model-connection-groups">
      {connectionTypes.map((type) => {
        const rows = data.items.filter((row) => row.kind === type)
        const selected = rows.find((row) => row.is_default)
        const others = rows.filter((row) => !row.is_default)
        const platform = type === 'llm' ? data.platform_llm : type === 'embedding' ? data.platform_embedding : null
        const group = modelGroups[type]
        const Icon = group.icon
        return <section key={type} className="model-group">
          <header><h3><Icon size={19} aria-hidden="true" />{group.label}</h3>
            <p>{group.description}</p>
          </header>
          {selected ? connection(selected) : <div className="model-platform" data-default="true">
            <p className="model-current"><CircleDot size={14} aria-hidden="true" />{type === 'vision' ? '视觉理解未启用' : type === 'rerank' ? '尚未设置默认连接' : '当前默认 · 平台连接'}</p>
            {type !== 'vision' && type !== 'rerank' && <strong className="model-name">{platform ?? '尚未配置'}</strong>}
          </div>}
          <div className="model-scope">
            <p>{group.scope}</p>
            {selected && type !== 'vision' && type !== 'rerank' && <p>平台默认：<span className="model-name">{platform ?? '尚未配置'}</span></p>}
          </div>
          <div className="model-other-connections">
            <h4>其他连接 <span>{others.length}</span></h4>
            {others.map(connection)}
            {!others.length && <p className="model-empty">{selected ? '暂无其他个人连接。' : '尚未添加个人连接。添加后先测试，再设为默认。'}</p>}
          </div>
        </section>
      })}
    </div>}
    <Dialog open={Boolean(editing)} onOpenChange={(open) => { if (!open && busy !== 'save') closeEditor() }}>
      <DialogContent title={editing === 'new' ? '添加模型连接' : '编辑模型连接'} closeLabel="关闭" onCloseAutoFocus={restoreFocus}>
        {editing && <form ref={formRef} className="model-form" onSubmit={save} key={editSession}>
          {editing !== 'new' && editing.tested_at && <p className="muted">最近测试 {new Date(editing.tested_at).toLocaleString('zh-CN')}</p>}
          <Select label="连接类型" value={kind} disabled={editing !== 'new'} onValueChange={(value) => setKind(value as ModelConnection['kind'])}
            options={connectionTypes.map((value) => ({ value, label: modelGroups[value].label }))} />
          <Input label="连接名称" name="name" maxLength={80} required defaultValue={editing === 'new' ? '' : editing.name} placeholder="例如：个人模型服务" />
          <Input label="Base URL" name="base_url" required maxLength={500} defaultValue={editing === 'new' ? '' : editing.base_url} placeholder="https://api.example.com/v1"
            readOnly={editing !== 'new' && editing.libraries.length > 0} />
          <Input label="模型名称" name="model" required maxLength={120} defaultValue={editing === 'new' ? '' : editing.model} placeholder="供应商提供的模型 ID"
            readOnly={editing !== 'new' && editing.libraries.length > 0} />
          <PasswordField label={editing === 'new' ? 'API Key' : '替换 API Key（留空保留）'} name="api_key" required={editing === 'new'} autoComplete="new-password" maxLength={4096} messages={{ showPassword: '显示 API Key', hidePassword: '隐藏 API Key' }} />
          {editing !== 'new' && editing.libraries.length > 0 && <p className="muted">此连接已绑定知识库，可更换 Key。更换地址或模型请添加新连接。</p>}
          <Button type="submit" loading={busy === 'save'} disabled={Boolean(busy) || !credentialsReady}>保存连接</Button>
        </form>}
      </DialogContent>
    </Dialog>
    <Dialog open={Boolean(deleting)} onOpenChange={(open) => { if (!open) setDeleting(undefined) }}>
      <DialogContent title="删除模型连接" closeLabel="关闭" onCloseAutoFocus={restoreFocus}>
        {deleting && <>
          <p>删除「{deleting.name}」？{deleting.libraries.length ? '它正被以下知识库使用，当前无法删除。' : deleting.is_default ? deleting.kind === 'vision' ? '请先清除或切换默认视觉连接。' : '请先切换默认连接或恢复平台默认。' : ''}</p>
          {deleting.libraries.map((library) => <p key={library.id}>{library.name}</p>)}
          <Button variant="danger" disabled={Boolean(busy) || deleting.is_default || deleting.libraries.length > 0} onClick={async () => {
            setBusy('delete')
            try { await api.deleteModelConnection(deleting.id); setDeleting(undefined); load(); notify('连接已删除。') }
            catch (failure) { report(failure) } finally { setBusy('') }
          }}>确认删除</Button>
        </>}
      </DialogContent>
    </Dialog>
  </section>
}
