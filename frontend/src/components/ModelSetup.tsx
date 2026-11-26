import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import type { DiscoveredModel, ModelConnection, ModelConnectionKind, ModelProvider } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { PasswordField } from '@/components/arc/password-field/password-field'
import { Select } from '@/components/arc/select/select'

const roles = { llm: '文本', embedding: '向量', vision: '视觉', rerank: '重排' } as const
const kinds = Object.keys(roles) as ModelConnectionKind[]
const custom: ModelProvider = {
  id: 'custom', name: '自定义兼容地址', base_url: '', models: { llm: null, embedding: null, vision: null, rerank: null },
  discovery: true, rerank_path: null, key_label: 'API Key', hint: null,
}
type Tested = { row?: ModelConnection; endpoint: string; model: string; keyRevision: number; message?: string }

function roleEndpoint(provider: ModelProvider, base: string, kind: ModelConnectionKind) {
  if (kind !== 'rerank' || !provider.rerank_path || !base) return base
  try { return new URL(provider.rerank_path, base).href } catch { return base }
}

function origin(value: string) {
  try { return new URL(value).origin } catch { return null }
}

export function ModelSetup({ onClose, onChange, onCloseAutoFocus, explainFailure }: {
  onClose: () => void
  onChange: () => void
  onCloseAutoFocus: (event: Event) => void
  explainFailure: (code: string | null) => string
}) {
  const prefix = useId()
  const form = useRef<HTMLFormElement>(null)
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [catalogError, setCatalogError] = useState('')
  const [provider, setProvider] = useState(custom)
  const [base, setBase] = useState('')
  const [keyRevision, setKeyRevision] = useState(0)
  const [drafts, setDrafts] = useState<Partial<Record<ModelConnectionKind, string>>>({ llm: '' })
  const [tested, setTested] = useState<Partial<Record<ModelConnectionKind, Tested>>>({})
  const [models, setModels] = useState<DiscoveredModel[]>([])
  const [discoveryNote, setDiscoveryNote] = useState('')
  const [busy, setBusy] = useState('')
  const [activated, setActivated] = useState<Partial<Record<ModelConnectionKind, string>>>({})
  const [actionError, setActionError] = useState('')
  const abortDiscovery = useRef<AbortController | null>(null)

  useEffect(() => {
    let active = true
    void api.modelProviders().then(
      (rows) => { if (active) setProviders(rows) },
      () => { if (active) setCatalogError('无法读取供应商目录。仍可填写自定义地址。') },
    )
    return () => { active = false; abortDiscovery.current?.abort() }
  }, [])

  function clearDiscovery() { setModels([]); setDiscoveryNote('') }

  function clearKey() {
    const input = form.current?.elements.namedItem('api_key') as HTMLInputElement | null
    if (input) input.value = ''
    setKeyRevision((value) => value + 1)
  }

  function chooseProvider(id: string) {
    const next = providers.find((row) => row.id === id) ?? custom
    clearKey()
    setProvider(next)
    setBase(next.base_url)
    const first = kinds.find((kind) => kind in next.models) ?? 'llm'
    setDrafts({ [first]: next.models[first] ?? '' })
    setTested({})
    setActivated({})
    setActionError('')
    clearDiscovery()
  }

  function credential(fields: HTMLFormElement) {
    return String(new FormData(fields).get('api_key') ?? '').trim()
  }

  async function findModels() {
    const fields = form.current
    if (!fields || busy) return
    const urlField = fields.elements.namedItem('base_url') as HTMLInputElement
    const keyField = fields.elements.namedItem('api_key') as HTMLInputElement
    if (!urlField.reportValidity() || !keyField.reportValidity()) return
    const controller = new AbortController()
    abortDiscovery.current = controller
    setBusy('discovery')
    clearDiscovery()
    try {
      const result = await api.discoverModels(base.trim(), credential(fields), controller.signal)
      if (controller.signal.aborted) return
      setModels(result)
      setDiscoveryNote(result.length ? `找到 ${result.length} 个候选模型。仍需按用途测试。` : '服务没有返回候选模型，请填写模型 ID。')
    } catch (failure) {
      if (!controller.signal.aborted) setDiscoveryNote(errorMessage(failure))
    } finally {
      if (!controller.signal.aborted) setBusy('')
    }
  }

  function matches(current: Tested, kind: ModelConnectionKind) {
    return current.model === drafts[kind]?.trim() && current.endpoint === roleEndpoint(provider, base.trim(), kind)
      && current.keyRevision === keyRevision
  }

  async function testSelected(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const key = credential(event.currentTarget)
    const selected = kinds.filter((kind) => drafts[kind] !== undefined)
    if (!key || !selected.length || selected.some((kind) => !drafts[kind]?.trim())) {
      setActionError('请填写 API Key，并为至少一种用途填写模型 ID。')
      return
    }
    setBusy('test')
    setActionError('')
    for (const kind of selected) {
      const previous = tested[kind]
      if (previous?.row?.status === 'ready' && matches(previous, kind)) continue
      const model = drafts[kind]!.trim()
      const endpoint = roleEndpoint(provider, base.trim(), kind)
      const body = { name: `${provider.name} · ${roles[kind]}`, kind, base_url: endpoint, model, api_key: key }
      let row = previous?.row
      let saved = previous
      try {
        // Changing a tested configuration creates a new draft, so a working default survives a failed test.
        if (!row || row.status === 'ready') row = await api.createModelConnection(body)
        else if (!previous || !matches(previous, kind)) row = await api.updateModelConnection(row.id, body)
        saved = { row, endpoint, model, keyRevision }
        row = await api.testModelConnection(row.id)
        setTested((current) => ({ ...current, [kind]: { ...saved!, row } }))
      } catch (failure) {
        setTested((current) => ({ ...current, [kind]: { ...(saved ?? { endpoint, model, keyRevision }), message: errorMessage(failure) } }))
      }
    }
    onChange()
    setBusy('')
  }

  async function activate(kind: ModelConnectionKind, row: ModelConnection) {
    setBusy(kind)
    setActionError('')
    try {
      await api.defaultModelConnection(kind, row.id)
      setActivated((current) => ({ ...current, [kind]: `${row.id}:${row.revision}` }))
      onChange()
    } catch (failure) { setActionError(errorMessage(failure)) }
    finally { setBusy('') }
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent title="配置个人模型" description="选择供应商，填写 Key，再为本次配置选择用途。测试使用简短示例，可能产生少量费用。"
      closeLabel="关闭" onCloseAutoFocus={onCloseAutoFocus}>
      <form ref={form} className="model-form" method="post" aria-label="个人模型配置" onSubmit={(event) => void testSelected(event)}>
        <fieldset className="model-setup-fields" disabled={Boolean(busy)}>
          <legend>供应商与凭据</legend>
          <Select label="供应商" value={provider.id} disabled={Boolean(busy)} onValueChange={chooseProvider}
            options={[...providers, custom].map(({ id, name }) => ({ value: id, label: name }))} />
          {catalogError && <p role="status">{catalogError}</p>}
          {provider.hint && <p className="muted">{provider.hint}</p>}
          <Input label="API Base URL" name="base_url" required maxLength={500} spellCheck={false} value={base}
            placeholder="https://api.example.com/v1" onChange={(event) => {
              const next = event.target.value
              if (origin(next) !== origin(base)) clearKey()
              setBase(next)
              clearDiscovery()
            }} />
          <PasswordField label={provider.key_label} name="api_key" required maxLength={4096} autoComplete="new-password"
            onChange={() => { setKeyRevision((value) => value + 1); clearDiscovery() }}
            messages={{ showPassword: '显示 API Key', hidePassword: '隐藏 API Key' }} />
          {provider.discovery ? <Button type="button" variant="secondary" onClick={() => void findModels()} loading={busy === 'discovery'}>发现模型</Button>
            : <p className="muted">此预设使用推荐模型或手动填写模型 ID。</p>}
          <p role="status" hidden={!discoveryNote}>{discoveryNote}</p>
        </fieldset>
        <fieldset className="model-setup-fields" disabled={Boolean(busy)}>
          <legend>用途与模型 ID</legend>
          <p className="muted">选中的用途共用上方 Key。向量模型须返回 1024 维。</p>
          {kinds.filter((kind) => kind in provider.models).map((kind) => {
            const selected = drafts[kind] !== undefined
            const result = tested[kind]
            const row = result?.row
            const validResult = result && matches(result, kind)
            const ready = validResult && row?.status === 'ready' && !result.message
            return <div className="model-setup-role" key={kind}>
              <label><input type="checkbox" name={`use_${kind}`} checked={selected} onChange={(event) => {
                const checked = event.target.checked
                setDrafts((current) => ({ ...current, [kind]: checked ? provider.models[kind] ?? '' : undefined }))
              }} /> {roles[kind]}</label>
              {selected && <>
                <Input label={`${roles[kind]}模型 ID`} name={kind} list={models.length ? `${prefix}-models` : undefined}
                  required maxLength={120} spellCheck={false} value={drafts[kind]}
                  onChange={(event) => setDrafts({ ...drafts, [kind]: event.target.value })} />
                {kind === 'rerank' && provider.rerank_path && <p className="muted model-address">{roleEndpoint(provider, base.trim(), kind)}</p>}
                {result && <p role="status">{result.message ?? (validResult
                  ? ready ? '测试通过' : explainFailure(row?.error_code ?? null)
                  : '配置已修改，请重新测试。')}</p>}
                {ready && row && <Button type="button" variant="secondary" size="sm" loading={busy === kind}
                  disabled={activated[kind] === `${row.id}:${row.revision}`} onClick={() => void activate(kind, row)}>
                  {activated[kind] === `${row.id}:${row.revision}` ? `已设为默认${roles[kind]}模型` : `设为默认${roles[kind]}模型`}</Button>}
              </>}
            </div>
          })}
          {models.length > 0 && <datalist id={`${prefix}-models`}>{models.map((row) => <option key={row.id} value={row.id} label={row.name ?? undefined} />)}</datalist>}
        </fieldset>
        {actionError && <p className="error-text" role="alert">{actionError}</p>}
        <Button type="submit" loading={busy === 'test'} disabled={Boolean(busy)}>保存并测试所选模型</Button>
      </form>
    </DialogContent>
  </Dialog>
}
