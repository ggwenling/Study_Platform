import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, Ellipsis, Plus, Server, ShieldCheck } from 'lucide-react'
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

const statusLabel = { draft: '待测试', testing: '测试中', ready: '可用', failed: '测试失败' }
const fixes: Record<string, string> = {
  model_auth_failed: '认证失败。请检查 API Key 和模型访问权限。',
  model_quota_unavailable: '余额不足或请求受限。请检查供应商额度，稍后重试。',
  model_timeout: '连接超时。请检查服务状态与服务器网络。',
  model_service_error: '服务返回错误。请确认 Base URL 指向兼容的 API 入口。',
  model_response_invalid: '协议不兼容或返回内容无效。LLM 需要支持流式文本。',
  embedding_invalid: '向量返回不符合要求。请确认支持 1024 维及批量请求。',
  embedding_dimension_invalid: '向量维度不匹配。请使用支持 1024 维输出的模型。',
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
  const [kind, setKind] = useState<'llm' | 'embedding'>('llm')
  const [busy, setBusy] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const addRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const [editSession, setEditSession] = useState(0)
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

  async function choose(kind: 'llm' | 'embedding', id: string | null) {
    setBusy(id ?? kind)
    try { await api.defaultModelConnection(kind, id); notify('默认模型已更新。'); load() }
    catch (failure) { report(failure) } finally { setBusy('') }
  }
  async function test(row: ModelConnection) {
    setBusy(row.id)
    setData((value) => value && { ...value, items: value.items.map((item) => item.id === row.id ? { ...item, status: 'testing' } : item) })
    try {
      const result = await api.testModelConnection(row.id)
      notify(result.status === 'ready' ? '连接测试成功，可设为默认。' : fixes[result.error_code ?? ''] ?? '测试失败，请检查配置。')
    } catch (failure) { report(failure) } finally { setBusy(''); load() }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
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
  return <section className="model-settings" aria-labelledby="model-settings-title">
    <header><div><h2 id="model-settings-title"><Server size={20} />模型连接</h2>
      <p>为账户配置 LLM 与向量模型。请求从后端服务器发出，连接测试会产生少量调用。</p></div>
      <Button ref={addRef} variant="secondary" onClick={() => open()} disabled={Boolean(busy) || !data?.encryption_available}><Plus size={16} />添加连接</Button>
    </header>
    {error && <p role="alert">{error} <Button size="sm" onClick={load}>重试</Button></p>}
    {!data && !error && <p role="status">正在读取模型连接…</p>}
    {data && !data.encryption_available && <p role="alert" className="model-warning">管理员尚未配置有效的模型凭据加密密钥。配置完成后可保存个人连接。</p>}
    {data && <div className="model-connection-groups">
      {(['llm', 'embedding'] as const).map((type) => {
        const rows = data.items.filter((row) => row.kind === type)
        const selected = rows.find((row) => row.is_default)
        const platform = type === 'llm' ? data.platform_llm : data.platform_embedding
        return <section key={type} className="model-group">
          <header><h3>{type === 'llm' ? 'LLM 文本模型' : '向量模型 · 1024 维'}</h3>
            <p>{type === 'llm' ? '用于对话、摘要、检索改写、学习与资料分类。' : '默认选择仅用于之后新建的知识库。已有知识库继续使用其绑定连接。'}</p>
          </header>
          <div className="model-platform"><span>平台默认 · {platform ?? '尚未配置'}</span>
            {!selected ? <span className="model-current"><Check size={14} />当前默认</span>
              : <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => void choose(type, null)}>恢复平台默认</Button>}
          </div>
          {!rows.length && <p className="muted">尚未添加个人连接。</p>}
          {rows.map((row) => <article key={row.id} id={`model-connection-${row.id}`} className="model-connection">
            <div className="model-connection-heading"><strong>{row.name}</strong>
              {row.is_default && <span className="model-current"><Check size={13} />默认</span>}
              <span className={`model-status status-${row.status}`} role="status">{statusLabel[row.status]}</span>
              <DropdownMenu label={`管理连接 ${row.name}`} icon={<Ellipsis size={16} />} iconOnly items={[
                { label: '编辑连接', disabled: Boolean(busy), onSelect: () => open(row) },
                { label: '删除连接', disabled: Boolean(busy), destructive: true, onSelect: () => { rememberTrigger(row); setDeleting(row) } },
              ]} />
            </div>
            <p className="model-name">{row.model}</p><p className="model-address">{row.base_url}</p>
            <p className="model-key"><ShieldCheck size={14} />Key 已加密 · 尾号 {row.key_suffix}</p>
            {row.libraries.length > 0 && <p className="model-bound">绑定 {row.libraries.length} 个知识库 · {row.libraries.map((library) => library.name).join('、')}</p>}
            {row.tested_at && <p className="muted">最近测试 {new Date(row.tested_at).toLocaleString('zh-CN')}</p>}
            {row.error_code && <p className="model-warning">{fixes[row.error_code] ?? '连接不可用，请检查配置并重试。'}</p>}
            <div className="model-actions">
              <Button size="sm" variant="secondary" loading={busy === row.id} disabled={Boolean(busy) || row.status === 'testing'} onClick={() => void test(row)}>测试连接</Button>
              {!row.is_default && <Button size="sm" variant="ghost" disabled={Boolean(busy) || row.status !== 'ready'} onClick={() => void choose(type, row.id)}>设为默认</Button>}
            </div>
          </article>)}
        </section>
      })}
    </div>}
    <Dialog open={Boolean(editing)} onOpenChange={(open) => { if (!open && busy !== 'save') closeEditor() }}>
      <DialogContent title={editing === 'new' ? '添加模型连接' : '编辑模型连接'} closeLabel="关闭" onCloseAutoFocus={restoreFocus}>
        {editing && <form ref={formRef} className="model-form" onSubmit={save} key={editSession}>
          <Select label="连接类型" value={kind} disabled={editing !== 'new'} onValueChange={(value) => setKind(value as 'llm' | 'embedding')}
            options={[{ value: 'llm', label: 'LLM 文本模型' }, { value: 'embedding', label: '向量模型 · 1024 维' }]} />
          <Input label="连接名称" name="name" maxLength={80} required defaultValue={editing === 'new' ? '' : editing.name} placeholder="例如：个人模型服务" />
          <Input label="Base URL" name="base_url" required maxLength={500} defaultValue={editing === 'new' ? '' : editing.base_url} placeholder="https://api.example.com/v1"
            readOnly={editing !== 'new' && editing.libraries.length > 0} />
          <Input label="模型名称" name="model" required maxLength={120} defaultValue={editing === 'new' ? '' : editing.model} placeholder="供应商提供的模型 ID"
            readOnly={editing !== 'new' && editing.libraries.length > 0} />
          <PasswordField label={editing === 'new' ? 'API Key' : '替换 API Key（留空保留）'} name="api_key" required={editing === 'new'} autoComplete="new-password" maxLength={4096} messages={{ showPassword: '显示 API Key', hidePassword: '隐藏 API Key' }} />
          {editing !== 'new' && editing.libraries.length > 0 && <p className="muted">此连接已绑定知识库，可更换 Key。更换地址或模型请添加新连接。</p>}
          <Button type="submit" loading={busy === 'save'} disabled={Boolean(busy)}>保存连接</Button>
        </form>}
      </DialogContent>
    </Dialog>
    <Dialog open={Boolean(deleting)} onOpenChange={(open) => { if (!open) setDeleting(undefined) }}>
      <DialogContent title="删除模型连接" closeLabel="关闭" onCloseAutoFocus={restoreFocus}>
        {deleting && <>
          <p>删除「{deleting.name}」？{deleting.libraries.length ? '它正被以下知识库使用，当前无法删除。' : deleting.is_default ? '请先切换默认连接或恢复平台默认。' : ''}</p>
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
