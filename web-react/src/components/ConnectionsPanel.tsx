import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { requestJSON, type ConnectionSettings, type Provider } from '@/lib/api'

const field = 'h-9 w-full rounded-md border border-border bg-background px-2.5 text-xs'
const providerLabels: Record<Provider, string> = { brev: 'Team model (Brev)', cisco: 'Cisco lab', paid: 'Paid provider' }

type Props = { locked: boolean; repository?: string; githubError: string | null }

export default function ConnectionsPanel({ locked, repository, githubError }: Props) {
  const [saved, setSaved] = useState<ConnectionSettings | null>(null)
  const [tab, setTab] = useState<'github' | 'model'>('github')
  const [busy, setBusy] = useState(false)
  const [modelStatus, setModelStatus] = useState('')
  const [githubStatus, setGithubStatus] = useState('')
  const [useToken, setUseToken] = useState(false)
  const [githubToken, setGithubToken] = useState('')
  const [clearGithub, setClearGithub] = useState(false)
  const [provider, setProvider] = useState<Provider>('brev')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [concurrency, setConcurrency] = useState(3)
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)

  function render(data: ConnectionSettings, nextProvider: Provider = data.provider) {
    setSaved(data)
    setProvider(nextProvider)
    setModel(data.providers[nextProvider]?.model ?? '')
    setBaseUrl(data.providers[nextProvider]?.base_url ?? '')
    setConcurrency(data.max_concurrent_llm_calls)
    setUseToken(data.github_token_configured)
    setApiKey(''); setClearKey(false); setGithubToken(''); setClearGithub(false)
  }

  useEffect(() => {
    requestJSON<ConnectionSettings>('/settings').then((data) => { render(data); setModelStatus('Saved model ready to test.') })
      .catch((error: unknown) => setModelStatus(error instanceof Error ? error.message : 'Could not load settings.'))
  }, [])

  useEffect(() => { if (githubError) { setTab('github'); setGithubStatus(githubError) } }, [githubError])

  function pickProvider(next: Provider) {
    if (!saved) return
    setProvider(next); setApiKey(''); setClearKey(false)
    setModel(saved.providers[next]?.model ?? ''); setBaseUrl(saved.providers[next]?.base_url ?? '')
  }

  const savedConfig = saved?.providers[provider]
  const modelChanged = !saved || provider !== saved.provider || model.trim() !== savedConfig?.model || (provider !== 'paid' && baseUrl.trim() !== savedConfig?.base_url)
    || concurrency !== saved.max_concurrent_llm_calls || !!apiKey || clearKey
  const githubChanged = !saved || useToken !== saved.github_token_configured || !!githubToken || clearGithub

  async function guard(task: () => Promise<void>, setStatus: (text: string) => void) {
    setBusy(true)
    try { await task() } catch (error) { setStatus(error instanceof Error ? error.message : 'Request failed') }
    finally { setBusy(false) }
  }

  const saveModel = (event: FormEvent) => { event.preventDefault(); void guard(async () => {
    if (!modelChanged) { setModelStatus('Checking saved connection…'); setModelStatus((await requestJSON<{ message: string }>('/settings/test', { target: 'model' })).message); return }
    const data = await requestJSON<ConnectionSettings>('/settings', { provider, model: model.trim(), base_url: baseUrl.trim(), max_concurrent_llm_calls: concurrency, llm_api_key: apiKey || null, clear_llm_api_key: clearKey }, 10000)
    render(data)
    setModelStatus('Model saved. Checking the connection…')
    try { setModelStatus((await requestJSON<{ message: string }>('/settings/test', { target: 'model' })).message) }
    catch (error) { setModelStatus(`Saved, but connection check failed: ${error instanceof Error ? error.message : 'unknown error'}`) }
  }, setModelStatus) }

  const saveGithub = (event: FormEvent) => { event.preventDefault(); void guard(async () => {
    if (!githubChanged) {
      if (!repository) { setGithubStatus('Enter a repository in the watcher, then check access.'); return }
      setGithubStatus((await requestJSON<{ message: string }>('/settings/test', { target: 'github', repository })).message); return
    }
    if (useToken && !saved?.github_token_configured && !githubToken.trim()) { setGithubStatus('Enter a GitHub token, or choose public access.'); return }
    const data = await requestJSON<{ github_token_configured: boolean }>('/settings/github', { github_token: useToken ? githubToken || null : null, clear_github_token: !useToken || clearGithub }, 10000)
    setSaved((current) => current && { ...current, github_token_configured: data.github_token_configured })
    setUseToken(data.github_token_configured); setGithubToken(''); setClearGithub(false)
    setGithubStatus('Access saved. Load a repository to check it.')
  }, setGithubStatus) }

  return <div className="flex flex-col gap-3">
    <div role="tablist" aria-label="Connections" className="flex gap-1">
      {(['github', 'model'] as const).map((name) => <button key={name} role="tab" type="button" aria-selected={tab === name} onClick={() => setTab(name)} className={`rounded-md px-3 py-1 text-xs font-medium ${tab === name ? 'bg-accent-soft text-accent-strong' : 'text-muted-foreground'}`}>{name === 'github' ? 'GitHub' : 'Model'}</button>)}
    </div>
    {locked && <p className="text-[11px] text-muted-foreground">Pause reviews and wait for the active review before saving changes.</p>}
    {tab === 'github' ? <form className="grid max-w-md gap-2" onSubmit={saveGithub} autoComplete="off">
      <label className="grid gap-1 text-xs font-medium">Access method
        <select className={field} value={useToken ? 'token' : 'public'} disabled={busy || !saved} onChange={(event) => { setUseToken(event.currentTarget.value === 'token'); setGithubToken(''); setClearGithub(false) }}>
          <option value="public">Public repositories · no token</option><option value="token">GitHub token</option>
        </select>
      </label>
      {useToken && <>
        <label className="grid gap-1 text-xs font-medium">Token {saved?.github_token_configured && <span className="font-normal text-muted-foreground">· saved</span>}
          <input type="password" className={field} value={githubToken} autoComplete="new-password" placeholder={saved?.github_token_configured ? 'Leave blank to keep saved token' : 'Pull requests: Read permission'} disabled={busy || clearGithub} onChange={(event) => setGithubToken(event.currentTarget.value)}/>
        </label>
        {saved?.github_token_configured && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={clearGithub} onChange={(event) => setClearGithub(event.currentTarget.checked)}/>Remove saved token</label>}
      </>}
      <div><Button type="submit" className="h-9 text-xs" disabled={busy || !saved || (locked && githubChanged)}>{githubChanged ? 'Save & connect' : 'Test access'}</Button></div>
      {githubStatus && <p role="status" className="text-[11px] text-muted-foreground">{githubStatus}</p>}
    </form> : <form className="grid max-w-md gap-2" onSubmit={saveModel} autoComplete="off">
      <label className="grid gap-1 text-xs font-medium">Provider
        <select className={field} value={provider} disabled={busy || !saved} onChange={(event) => pickProvider(event.currentTarget.value as Provider)}>
          {(Object.keys(providerLabels) as Provider[]).map((name) => <option key={name} value={name}>{providerLabels[name]}</option>)}
        </select>
      </label>
      <label className="grid gap-1 text-xs font-medium">Access key {saved?.provider_keys_configured[provider] ? <span className="font-normal text-muted-foreground">· saved</span> : <span className="font-normal text-muted-foreground">· enter a key</span>}
        <input type="password" className={field} value={apiKey} autoComplete="new-password" disabled={busy || clearKey} onChange={(event) => setApiKey(event.currentTarget.value)}/>
      </label>
      <label className="grid gap-1 text-xs font-medium">Model name <input className={field} value={model} required maxLength={200} disabled={busy} onChange={(event) => setModel(event.currentTarget.value)}/></label>
      {provider !== 'paid' && <label className="grid gap-1 text-xs font-medium">Service URL <input type="url" className={field} value={baseUrl} required disabled={busy} onChange={(event) => setBaseUrl(event.currentTarget.value)}/></label>}
      <label className="grid gap-1 text-xs font-medium">Concurrent calls <input type="number" min={1} max={16} className={field} value={concurrency} required disabled={busy} onChange={(event) => setConcurrency(Number(event.currentTarget.value))}/></label>
      {saved?.provider_keys_configured[provider] && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.currentTarget.checked)}/>Remove saved key</label>}
      <div className="flex gap-2">
        <Button type="submit" className="h-9 text-xs" disabled={busy || !saved || (locked && modelChanged)}>{modelChanged ? 'Save & connect' : 'Test connection'}</Button>
        {provider === 'brev' && saved && <Button type="button" variant="outline" className="h-9 text-xs" disabled={busy} onClick={() => { setModel('llama3.1:8b'); setBaseUrl(saved.team_model_url); setConcurrency(1) }}>Use team defaults</Button>}
      </div>
      {modelStatus && <p role="status" className="text-[11px] text-muted-foreground">{modelStatus}</p>}
    </form>}
  </div>
}
