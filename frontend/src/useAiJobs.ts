import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { AiGenerationJobType, AiJob, SubmittedAiJob } from '@/api/types'

const jobTypes: AiGenerationJobType[] = ['notes', 'flashcards', 'quiz']
const labels: Record<AiGenerationJobType, string> = {
  notes: '知识工坊产物',
  flashcards: '记忆闪卡',
  quiz: '练习题',
}

function storageKey(userId: string) {
  return `knowloop.ai-jobs.${userId}`
}

function readTrackedIds(userId: string): string[] {
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(storageKey(userId)) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function isActive(job: AiJob) {
  return job.status === 'queued' || job.status === 'running'
}

export type AiJobsController = {
  jobs: AiJob[]
  track: (job: SubmittedAiJob) => void
  cancel: (jobId: string) => Promise<void>
}

export function useAiJobs({
  userId,
  notify,
  report,
  refresh,
}: {
  userId?: string
  notify: (message: string, options?: { action?: { label: string; onClick: () => void }; durationMs?: number }) => void
  report: (error: unknown) => void
  refresh: () => void
}): AiJobsController {
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<AiJob[]>([])
  const [hasTrackedJobs, setHasTrackedJobs] = useState(false)
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  const pendingIds = useRef<string[]>([])
  const activeUserId = useRef(userId)
  const mounted = useRef(false)
  const ownerGeneration = useRef(0)
  const visibilityGeneration = useRef(0)
  const pollingOwners = useRef(new Map<string, object>())
  const visibleRef = useRef(visible)
  activeUserId.current = userId

  function savePending(ids: string[], ownerId: string) {
    const unique = [...new Set(ids)]
    pendingIds.current = unique
    try {
      window.sessionStorage.setItem(storageKey(ownerId), JSON.stringify(unique))
    } catch {
      // Keep in-memory tracking when browser storage is unavailable.
    }
    if (mounted.current && activeUserId.current === ownerId) setHasTrackedJobs(unique.length > 0)
  }

  const poll = useEffectEvent(async () => {
    const ownerId = userId
    if (!ownerId || !mounted.current || !visibleRef.current || pollingOwners.current.has(ownerId)) return
    const generation = ownerGeneration.current
    const visibilityRun = visibilityGeneration.current
    const isCurrent = () => mounted.current
      && activeUserId.current === ownerId
      && ownerGeneration.current === generation
      && visibilityGeneration.current === visibilityRun
      && visibleRef.current
    const requestToken = {}
    pollingOwners.current.set(ownerId, requestToken)
    try {
      let rows = await api.aiJobs.list({ jobTypes, status: 'all', limit: 100 })
      if (!isCurrent()) return
      const known = new Set(rows.map((job) => job.id))
      const missingIds = pendingIds.current.filter((id) => !known.has(id))
      if (missingIds.length) {
        const restored = await Promise.all(missingIds.map(async (id) => {
          try {
            return await api.aiJobs.get(id)
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) return null
            throw error
          }
        }))
        if (!isCurrent()) return
        const missingSet = new Set(missingIds.filter((_, index) => restored[index] === null))
        if (missingSet.size) savePending(pendingIds.current.filter((id) => !missingSet.has(id)), ownerId)
        rows = [...rows, ...restored.filter((job): job is AiJob => job !== null)]
      }
      if (!isCurrent()) return

      const activeJobs = rows.filter(isActive)
      const tracked = new Set([...pendingIds.current, ...activeJobs.map((job) => job.id)])
      savePending([...tracked], ownerId)
      setJobs(activeJobs)

      const completed = rows.find((job) => tracked.has(job.id) && !isActive(job))
      if (!completed) return
      savePending(pendingIds.current.filter((id) => id !== completed.id), ownerId)
      if (completed.status === 'cancelled') return
      refresh()
      if (completed.status === 'failed') {
        notify(
          `${labels[completed.job_type]}「${completed.title}」失败（${completed.error_code ?? 'generation_failed'}）：${completed.error_message ?? '生成未完成。'}请检查资料和模型设置后重新提交。`,
          { durationMs: 8000 },
        )
        return
      }

      const resourceId = completed.result_resource_id
      let action: { label: string; onClick: () => void } | undefined
      if (resourceId && completed.result_resource_type === 'note' && completed.knowledge_base_id) {
        action = {
          label: '立即查看',
          onClick: () => void navigate(`/knowledge?${new URLSearchParams({ id: completed.knowledge_base_id!, note: resourceId })}`),
        }
      } else if (resourceId && completed.result_resource_type === 'flashcard_deck' && completed.knowledge_base_id) {
        action = {
          label: '立即复习',
          onClick: () => void navigate('/learning', {
            state: { tab: 'flashcards', deckId: resourceId, kbId: completed.knowledge_base_id },
          }),
        }
      } else if (resourceId && completed.result_resource_type === 'quiz' && completed.knowledge_base_id) {
        action = {
          label: '立即练习',
          onClick: () => {
            void api.learning.startAttempt(resourceId).then(
              (attempt) => navigate('/learning', {
                state: { tab: 'practice', attemptId: attempt.id, kbId: completed.knowledge_base_id },
              }),
              report,
            )
          },
        }
      }
      notify(`已完成${labels[completed.job_type]}「${completed.title}」。`, action ? { action } : undefined)
    } catch (error) {
      if (isCurrent() && error instanceof ApiError && error.status === 401) {
        report(error)
      }
    } finally {
      if (pollingOwners.current.get(ownerId) === requestToken) pollingOwners.current.delete(ownerId)
    }
  })

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    ownerGeneration.current += 1
    pollingOwners.current.clear()
    setJobs([])
    if (!userId) {
      pendingIds.current = []
      setHasTrackedJobs(false)
      return
    }
    const restoredIds = readTrackedIds(userId)
    pendingIds.current = restoredIds
    setHasTrackedJobs(restoredIds.length > 0)
    void poll()
  }, [userId])

  useEffect(() => {
    function handleVisibilityChange() {
      const nextVisible = document.visibilityState !== 'hidden'
      visibilityGeneration.current += 1
      pollingOwners.current.clear()
      visibleRef.current = nextVisible
      setVisible(nextVisible)
      if (nextVisible) void poll()
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    if (!userId || !visible || (!jobs.length && !hasTrackedJobs)) return
    const timer = window.setInterval(() => { void poll() }, 1800)
    return () => window.clearInterval(timer)
  }, [userId, visible, jobs.length, hasTrackedJobs])

  function track(submitted: SubmittedAiJob) {
    const ownerId = userId
    if (!ownerId || !mounted.current || activeUserId.current !== ownerId) return
    savePending([...pendingIds.current, submitted.job_id], ownerId)
    const now = new Date().toISOString()
    setJobs((current) => [{
      id: submitted.job_id,
      job_type: submitted.job_type,
      knowledge_base_id: submitted.knowledge_base_id,
      status: 'queued',
      phase: 'queued',
      progress: 0,
      title: submitted.title,
      result_resource_type: null,
      result_resource_id: null,
      error_code: null,
      error_message: null,
      created_at: now,
      updated_at: now,
    }, ...current.filter((job) => job.id !== submitted.job_id)])
  }

  async function cancel(jobId: string) {
    const ownerId = userId
    if (!ownerId || !mounted.current || activeUserId.current !== ownerId) return
    try {
      const updated = await api.aiJobs.cancel(jobId)
      if (!mounted.current || activeUserId.current !== ownerId) return
      if (isActive(updated)) {
        setJobs((current) => current.map((job) => job.id === jobId ? updated : job))
      } else if (updated.status === 'cancelled') {
        savePending(pendingIds.current.filter((id) => id !== jobId), ownerId)
        setJobs((current) => current.filter((job) => job.id !== jobId))
      } else {
        setJobs((current) => current.filter((job) => job.id !== jobId))
      }
    } catch (error) {
      if (mounted.current && activeUserId.current === ownerId) report(error)
    }
  }

  return { jobs, track, cancel }
}
