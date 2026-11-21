import type { AiGenerationJobType } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import type { AiJobsController } from '../useAiJobs'

const labels: Record<AiGenerationJobType, string> = {
  notes: '知识工坊产物',
  flashcards: '记忆闪卡',
  quiz: '练习题',
}

export function AiJobProgress({ jobs, cancel }: Pick<AiJobsController, 'jobs' | 'cancel'>) {
  if (!jobs.length) return null
  return <section className="ai-job-progress" aria-label="进行中的生成任务">
    {jobs.map((job) => <article className="ai-job-progress-item" key={job.id}>
      <div className="ai-job-progress-copy">
        <strong>{job.title}</strong>
        <small>{labels[job.job_type]} · {job.status === 'queued' ? '排队中' : '生成中'} · {job.progress}%</small>
      </div>
      <progress max={100} value={job.progress} aria-label={`${job.title} 进度`} />
      <Button type="button" size="sm" variant="ghost" onClick={() => void cancel(job.id)}>取消</Button>
    </article>)}
  </section>
}
