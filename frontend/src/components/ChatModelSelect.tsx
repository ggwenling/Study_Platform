import type { Ref } from 'react'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'

const accountModel = 'account-default'

export function ChatModelSelect({ modelId, conversationId, saving, onChange, triggerRef }: {
  modelId?: string | null
  conversationId?: string
  saving?: boolean
  onChange: (id: string | null) => void
  triggerRef: Ref<HTMLButtonElement>
}) {
  const { capabilities, chat, user } = useWorkspace()
  const models = capabilities?.chat_models
  if (!user || !models || modelId === undefined) return null
  const accountDefault = models.find((model) => model.is_default)
  const defaultModel = accountDefault?.model ?? capabilities?.platform_chat_model
  const selected = models.find((model) => model.id === modelId)

  return <div className="chat-model-control" aria-busy={saving || undefined}>
    <Select ref={triggerRef} className="composer-select" compact label="聊天模型" value={modelId ?? accountModel}
      disabled={saving || (!conversationId && Boolean(chat.pendingFor()))}
      description={conversationId ? '生成后续回复所需的会话上下文会发送给所选模型。' : undefined}
      formatValue={(label) => modelId ? selected?.model ?? label : defaultModel ?? '账户默认'}
      onValueChange={(value) => {
        chat.clearSelectionFailure?.(conversationId)
        onChange(value === accountModel ? null : value)
      }}
      options={[
        { value: accountModel, label: `使用账户默认${defaultModel ? ` · ${defaultModel}` : ''}` },
        ...models.map((model) => ({ value: model.id, label: `${model.name} · ${model.model}${model.status !== 'ready' ? '（待修复）' : ''}`, disabled: model.status !== 'ready' })),
        ...(modelId && !selected ? [{ value: modelId, label: '原连接不可用', disabled: true }] : []),
      ]} />
  </div>
}
