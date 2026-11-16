import { memo } from 'react'
import { BookOpen } from 'lucide-react'
import type { ChatPhase } from '@/api/types'
import { ThinkingOrbLazy } from './ThinkingOrbLazy'
import './AssistantActivity.css'

export const activityLabels: Record<ChatPhase, string> = {
  working: '正在处理…',
  searching: '正在检索资料…',
  solving: '正在深度推理…',
  composing: '正在生成回答…',
}

// Keep the avatar footprint identical for live and completed messages.
export const AssistantActivity = memo(function AssistantActivity({ phase }: { phase: ChatPhase | null }) {
  return (
    <span className="assistant-activity" aria-hidden="true">
      {phase
        ? <ThinkingOrbLazy state={phase} size={64} theme="auto" aria-hidden="true" />
        : <BookOpen size={20} />}
    </span>
  )
})
