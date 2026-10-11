import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { requestJSON, ApiError, type PullRequestInfo, type PullRequestLoad, type RepositoryPreview, type WatchJob, type WatchState } from '@/lib/api'

const field = 'h-9 w-full rounded-md border border-border bg-background px-2.5 text-xs'
const pending = ['queued', 'running']
const filters = { all: 'All', pending: 'Pending', completed: 'Completed', attention: 'Needs attention' } as const
type Filter = keyof typeof filters

function matches(job: WatchJob, filter: Filter) {
  if (filter === 'all') return true
  if (filter === 'pending') return pending.includes(job.status)
  if (filter === 'attention') return job.status === 'failed' || job.status === 'skipped'
  return job.status === filter
}

type Props = {
  busy: boolean
  selectedJob: string | null
  onSelectJob: (id: string) => void
  onWatch: (state: WatchState | null) => void
  onLoadPR: (diff: string, pr: PullRequestInfo) => void
  onAuthError: (message: string) => void
}

export default function GitHubPanel({ busy, selectedJob, onSelectJob, onWatch, onLoadPR, onAuthError }: Props) {
  const [watch, setWatch] = useState<WatchState | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [repository, setRepository] = useState('')
  const [edited, setEdited] = useState(false)
  const [mode, setMode] = useState<'all' | 'selected'>('selected')
  const [preview, setPreview] = useState<RepositoryPreview | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [prUrl, setPrUrl] = useState('')
  const initialized = useRef(false)

  const apply = useCallback((state: WatchState) => {
    setWatch(state)
    onWatch(state)
    if (!initialized.current) {
      initialized.current = true
      if (state.repository) { setRepository(state.repository); setMode(state.mode || 'selected'); setPicked(new Set(state.selected_prs)) }
    }
  }, [onWatch])

  useEffect(() => {
    let stop = false
    let timer = 0
    const tick = async () => {
      try { if (!stop) apply(await requestJSON<WatchState>('/watch')) }
      catch (error) {
        if (error instanceof ApiError && [403, 404, 405].includes(error.status)) { setUnavailable(true); setMessage(error.message); return }
        setMessage(`${error instanceof Error ? error.message : 'Queue unavailable'} Retrying shortly…`)
      }
      if (!stop) timer = window.setTimeout(tick, 3000)
    }
    void tick()
    return () => { stop = true; window.clearTimeout(timer); onWatch(null) }
  }, [apply, onWatch])

  async function run<T>(task: () => Promise<T>) {
    setLoading(true)
    try { return await task() }
    catch (error) {
      setMessage(error instanceof Error ? error.message : 'Request failed')
      if (error instanceof ApiError && [401, 403].includes(error.status)) onAuthError(error.message)
    } finally { setLoading(false) }
  }

  const loadPRs = () => run(async () => {
    setMessage('Loading open PRs using saved GitHub access…')
    const data = await requestJSON<RepositoryPreview>('/github/repository', { repository: repository.trim() })
    const previous = preview?.repository === data.repository ? picked : watch?.repository === data.repository ? new Set(watch.selected_prs) : new Set<number>()
    setPreview(data)
    setPicked(new Set(data.pull_requests.filter((pr) => !pr.draft && previous.has(pr.number)).map((pr) => pr.number)))
    setRepository(data.repository)
    setEdited(false)
    setMessage(`${data.pull_requests.length} open PRs${data.incomplete ? ' · limited to the newest 1,000' : ''}${data.pull_requests.length ? '' : ' · none to review'}`)
  })

  const start = () => run(async () => {
    const repo = repository.trim()
    const selected = preview ? [...picked] : !edited && watch?.repository === repo ? watch.selected_prs : []
    if (mode === 'selected' && !selected.length) { setMessage('Load open PRs and select at least one non-draft PR first.'); return }
    setMessage('Checking model and repository access…')
    apply(await requestJSON<WatchState>('/watch', { repository: repo, mode, selected_prs: mode === 'selected' ? selected : [] }))
    setEdited(false)
    setMessage('Review scope saved. Pause holds the queue; Ignore excludes a PR.')
  })

  const pause = () => run(async () => apply(await requestJSON<WatchState>('/watch/pause', {})))

  const jobAction = (job: WatchJob, action: 'retry' | 'ignore' | 'include') => run(async () => {
    apply(await requestJSON<WatchState>(`/watch/reviews/${job.id}/${action}`, {}))
    if (preview?.repository === job.pull_request.repository && action !== 'retry') {
      setPicked((current) => { const next = new Set(current); if (action === 'include') next.add(job.pull_request.number); else next.delete(job.pull_request.number); return next })
    }
  })

  const loadSinglePR = () => run(async () => {
    setMessage('Loading PR diff…')
    const data = await requestJSON<PullRequestLoad>('/github/pull-request', { url: prUrl.trim() })
    if (data.diff) { onLoadPR(data.diff, data.pull_request); setMessage(`Loaded ${data.pull_request.repository} #${data.pull_request.number}. Press Run council.`) }
  })

  const startLabel = watch?.repository && !watch.enabled && !edited && !preview ? 'Resume reviews' : mode === 'all' ? 'Start all-PR reviews' : 'Start selected reviews'
  const ready = mode === 'all' || (preview ? picked.size > 0 : !edited && watch?.repository === repository.trim() && !!watch?.selected_prs.length)
  const jobs = (watch?.jobs ?? []).filter((job) => matches(job, filter))
  const counts = watch?.counts ?? {}
  const summary = watch?.error || (watch?.enabled
    ? `Watching ${watch.repository} · ${counts.queued ?? 0} queued · ${counts.running ?? 0} reviewing · ${counts.completed ?? 0} completed · ${counts.failed ?? 0} failed`
    : watch?.repository ? `Paused · ${watch.repository}` : 'Connect a repository to begin automatic reviews.')

  return <div className="grid gap-4 lg:grid-cols-2">
    <div className="flex min-w-0 flex-col gap-3">
      <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); void loadSinglePR() }}>
        <label className="text-xs font-medium" htmlFor="pr-url">Review one PR</label>
        <div className="flex gap-2">
          <input id="pr-url" className={field} placeholder="https://github.com/owner/repo/pull/123" value={prUrl} onChange={(event) => setPrUrl(event.currentTarget.value)} disabled={busy || loading}/>
          <Button type="submit" variant="outline" className="h-9 text-xs" disabled={busy || loading || !prUrl.trim()}>Load diff</Button>
        </div>
      </form>
      <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); void start() }}>
        <label className="text-xs font-medium" htmlFor="repository">Repository watcher</label>
        <div className="flex gap-2">
          <input id="repository" className={field} placeholder="owner/repository or GitHub URL" value={repository} disabled={loading}
            onChange={(event) => { setRepository(event.currentTarget.value); setEdited(true); setPreview(null); setPicked(new Set()); setMessage('Repository changed. Load open PRs to choose from it.') }}/>
          <Button type="button" variant="outline" className="h-9 text-xs" onClick={() => void loadPRs()} disabled={loading || !repository.trim()}>Load open PRs</Button>
        </div>
        <select aria-label="Review scope" className={field} value={mode} disabled={loading} onChange={(event) => setMode(event.currentTarget.value as 'all' | 'selected')}>
          <option value="selected">Selected PRs only</option>
          <option value="all">All open PRs</option>
        </select>
        <p className="text-[11px] text-muted-foreground">{mode === 'all' ? 'All open, non-draft PRs are queued, including future PRs and changed revisions.' : 'Only selected PRs and their changed revisions are reviewed.'}</p>
        {mode === 'selected' && preview && <ul className="max-h-40 overflow-y-auto rounded-md border border-border">
          {preview.pull_requests.map((pr) => <li key={pr.number} className="border-b border-border px-2.5 py-1.5 last:border-b-0">
            <label className="flex items-start gap-2 text-xs">
              <input type="checkbox" className="mt-0.5" checked={picked.has(pr.number)} disabled={pr.draft}
                onChange={(event) => { const on = event.currentTarget.checked; setPicked((current) => { const next = new Set(current); if (on) next.add(pr.number); else next.delete(pr.number); return next }) }}/>
              <span>#{pr.number} {pr.title} · {pr.author}{pr.draft ? ' · draft (skipped)' : ''}</span>
            </label>
          </li>)}
        </ul>}
        <div className="flex gap-2">
          <Button type="submit" className="h-9 text-xs" disabled={loading || busy || unavailable || !ready}>{startLabel}</Button>
          <Button type="button" variant="outline" className="h-9 text-xs" onClick={() => void pause()} disabled={loading || !watch?.enabled}>Pause</Button>
        </div>
      </form>
      {message && <p role="status" className="text-[11px] text-muted-foreground">{message}</p>}
    </div>
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">Review queue</h3>
        <select aria-label="Filter queue" className="h-7 rounded-md border border-border bg-background px-2 text-[11px]" value={filter} onChange={(event) => setFilter(event.currentTarget.value as Filter)}>
          {Object.entries(filters).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <p className="text-[11px] text-muted-foreground">{unavailable ? 'The review queue is unavailable on this server.' : summary}</p>
      <ul className="flex max-h-72 flex-col overflow-y-auto rounded-md border border-border empty:hidden">
        {jobs.map((job) => {
          const ignored = watch?.ignored_prs.includes(job.pull_request.number) || job.status === 'ignored'
          return <li key={job.id} className={`flex flex-wrap items-center justify-between gap-2 border-b border-border px-2.5 py-2 last:border-b-0 ${selectedJob === job.id ? 'bg-accent-soft/60' : ''}`}>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{job.pull_request.repository} #{job.pull_request.number} {job.pull_request.title}</div>
              <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground"><code>{job.pull_request.head_sha.slice(0, 7)}</code><Badge variant={job.status === 'failed' ? 'destructive' : job.status === 'completed' ? 'success' : 'secondary'} className="text-[9px]">{job.status}</Badge>{job.outcome && <span>{job.outcome}</span>}</div>
              {job.error && <div className="text-[10px] text-destructive">{job.error}</div>}
            </div>
            <div className="flex gap-1">
              <Button variant="outline" className="h-7 px-2 text-[11px]" disabled={busy || !['running', 'completed', 'failed'].includes(job.status)} onClick={() => onSelectJob(job.id)}>{job.status === 'running' ? 'Watch debate' : 'View review'}</Button>
              {(job.status === 'failed' || job.status === 'skipped') && <Button variant="outline" className="h-7 px-2 text-[11px]" disabled={loading} onClick={() => void jobAction(job, 'retry')}>Retry</Button>}
              <Button variant="ghost" className="h-7 px-2 text-[11px]" disabled={loading} onClick={() => void jobAction(job, ignored ? 'include' : 'ignore')}>{ignored ? 'Include PR' : 'Ignore PR'}</Button>
            </div>
          </li>
        })}
      </ul>
    </div>
  </div>
}
