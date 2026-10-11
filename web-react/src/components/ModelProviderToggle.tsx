import { useState } from 'react'
import { requestJSON, type ConnectionSettings } from '@/lib/api'
import { cn } from '@/lib/utils'

type Choice = 'brev' | 'cisco'
type Props = {
  settings: ConnectionSettings | null
  locked: boolean
  onSaved: (settings: ConnectionSettings) => void
  onConfigure: (provider: Choice) => void
  onBusy: (busy: boolean) => void
}

export default function ModelProviderToggle({ settings, locked, onSaved, onConfigure, onBusy }: Props) {
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  async function choose(provider: Choice) {
    if (!settings || locked || saving || settings.provider === provider) return
    const config = settings.providers[provider]
    if (!config.model || !config.base_url || !settings.provider_keys_configured[provider]) {
      setMessage(`Configure ${provider === 'brev' ? 'Brev' : 'Cisco'} below, then save its connection.`)
      onConfigure(provider)
      return
    }
    setSaving(true); onBusy(true); setMessage('Switching…')
    try {
      // Send only public configuration. The server selects this provider's saved key.
      const saved = await requestJSON<ConnectionSettings>('/settings', {
        provider, model: config.model, base_url: config.base_url,
        max_concurrent_llm_calls: settings.max_concurrent_llm_calls,
      }, 10000)
      onSaved(saved)
      setMessage(`${provider === 'brev' ? 'Brev' : 'Cisco'} selected for new reviews.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not switch providers.') }
    finally { setSaving(false); onBusy(false) }
  }

  return <div className="flex flex-wrap items-center gap-2">
    <span className="text-xs text-muted-foreground">Model</span>
    <div role="radiogroup" aria-label="Model provider" aria-busy={saving} className="flex rounded-lg border border-border bg-surface-raised p-1">
      {(['brev', 'cisco'] as const).map((provider) => <button key={provider} type="button" role="radio"
        aria-checked={settings?.provider === provider} disabled={!settings || locked || saving}
        title={locked ? 'Pause reviews and wait for the active review to finish.' : undefined}
        onClick={() => void choose(provider)}
        className={cn('rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
          settings?.provider === provider ? 'bg-accent-soft text-accent-strong' : 'text-muted-foreground hover:bg-accent-soft/60')}>
        {provider === 'brev' ? 'Brev' : 'Cisco'}
      </button>)}
    </div>
    <span role="status" className="text-[11px] text-muted-foreground">{message || (settings?.provider === 'paid' ? 'Paid provider active' : '')}</span>
  </div>
}
