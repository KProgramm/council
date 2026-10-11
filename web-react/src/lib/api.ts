export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

export async function requestJSON<T>(path: string, payload?: unknown, timeoutMs = 65000): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, {
      method: payload === undefined ? 'GET' : 'POST',
      cache: 'no-store',
      signal: controller.signal,
      headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    })
    let data: { detail?: unknown }
    try { data = await response.json() } catch { throw new Error('Council returned an unreadable response. Check that the server is running.') }
    if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : 'Check the entered values and try again.', response.status)
    return data as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('The request timed out. Check the connection, then try again.')
    if (error instanceof TypeError) throw new Error('Could not reach Council. Check that the local server is running.')
    throw error
  } finally { clearTimeout(timeout) }
}

export type Provider = 'brev' | 'cisco' | 'paid'
export type ProviderConfig = { model: string; base_url: string }
export type ConnectionSettings = {
  provider: Provider
  providers: Record<Provider, ProviderConfig>
  max_concurrent_llm_calls: number
  github_token_configured: boolean
  llm_api_key_configured: boolean
  provider_keys_configured: Record<Provider, boolean>
  team_model_url: string
}

export type PullRequestSummary = { number: number; title: string; author: string; draft: boolean }
export type RepositoryPreview = { repository: string; pull_requests: PullRequestSummary[]; incomplete?: boolean }
export type PullRequestInfo = { repository: string; number: number; title: string; url: string; head_sha: string; author: string }
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'skipped' | 'ignored'
export type WatchJob = { id: string; status: JobStatus; outcome?: string | null; error?: string | null; pull_request: PullRequestInfo }
export type WatchState = {
  enabled: boolean
  active: boolean
  repository: string | null
  mode: 'all' | 'selected'
  selected_prs: number[]
  ignored_prs: number[]
  counts: Partial<Record<JobStatus, number>>
  jobs: WatchJob[]
  error?: string | null
  last_checked?: number | null
}
export type WatchDetail = {
  status: JobStatus
  error?: string | null
  valid_result?: boolean
  pull_request: PullRequestInfo
  messages: Array<Record<string, unknown> & { kind: string }>
}
export type PullRequestLoad = { pull_request: PullRequestInfo; changed: boolean; diff: string | null }
