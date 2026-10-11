import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { IconAlertTriangle, IconBrandGithub, IconBolt, IconBulb, IconChecklist, IconChevronDown, IconFileDiff, IconMoon, IconPuzzle, IconRefresh, IconShieldCheck, IconSun, IconUpload, IconX } from '@tabler/icons-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import ConnectionsPanel from '@/components/ConnectionsPanel'
import GitHubPanel from '@/components/GitHubPanel'
import AgentOwl from '@/components/AgentOwl'
import { requestJSON, type PullRequestInfo, type WatchDetail, type WatchState } from '@/lib/api'

type Agent = 'security' | 'performance' | 'compatibility' | 'quality' | 'product' | 'system'
type Position = 'approve' | 'approve_with_changes' | 'block' | 'escalate'
type Message = { id:string; review_id:string; round:number; agent:Agent; kind:string; position:Position|null; confidence:number|null; claims:Array<{file:string;line:number|null;concern:string;severity:string}>; references:Array<{agent:Agent;message_id:string;stance:'agree'|'challenge'|'compromise'}>; text:string; ts:number }
type TradeoffMap = { review_id:string; outcome:'consensus'|'escalate'; final_positions:Record<string,Position>; resolved:string[]; unresolved:string[]; human_decisions:string[] }
const agents = [
  { id:'security', name:'Security', icon:IconShieldCheck, color:'#ef4444' },
  { id:'performance', name:'Performance', icon:IconBolt, color:'#f59e0b' },
  { id:'compatibility', name:'Compatibility', icon:IconPuzzle, color:'#3b82f6' },
  { id:'quality', name:'Quality', icon:IconChecklist, color:'#10b981' },
  { id:'product', name:'Product', icon:IconBulb, color:'#a855f7' },
] as const
type AgentTab = typeof agents[number]['id']
const labels:Record<Position,string> = {approve:'Approve',approve_with_changes:'With changes',block:'Block',escalate:'Escalate'}
const symbols:Record<Position,string> = {approve:'✓',approve_with_changes:'~',block:'×',escalate:'!'}
const stanceLabels = {agree:'agrees with',challenge:'challenges',compromise:'compromises with'}
const getAgent = (name:Agent) => agents.find((agent) => agent.id === name)
const wsUrl = (path:string) => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${path}`

function readMap(text:string):TradeoffMap|null {
  try {
    const value = JSON.parse(text) as Partial<TradeoffMap>
    if ((value.outcome !== 'consensus' && value.outcome !== 'escalate') || !Array.isArray(value.unresolved)) return null
    return {review_id:value.review_id ?? '',outcome:value.outcome,final_positions:value.final_positions ?? {},resolved:value.resolved ?? [],unresolved:value.unresolved,human_decisions:value.human_decisions ?? []}
  } catch { return null }
}

export default function CouncilApp() {
  const [diff,setDiff] = useState('')
  const [messages,setMessages] = useState<Message[]>([])
  const [map,setMap] = useState<TradeoffMap|null>(null)
  const [state,setState] = useState<'idle'|'connecting'|'running'|'complete'|'error'>('idle')
  const [status,setStatus] = useState('Ready')
  const [reviewId,setReviewId] = useState<string|null>(null)
  const [error,setError] = useState<string|null>(null)
  const [selectedAgent,setSelectedAgent] = useState<AgentTab|null>('security')
  const [showGithub,setShowGithub] = useState(false)
  const [watch,setWatch] = useState<WatchState|null>(null)
  const [selectedJob,setSelectedJob] = useState<string|null>(null)
  const [loadedPR,setLoadedPR] = useState<PullRequestInfo|null>(null)
  const [githubError,setGithubError] = useState<string|null>(null)
  const run = useRef(0)
  const ws = useRef<WebSocket|null>(null)
  const fileInput = useRef<HTMLInputElement|null>(null)
  const agentTabs = useRef<Array<HTMLButtonElement|null>>([])
  const agentList = useRef<HTMLDivElement|null>(null)
  const count = useRef(0)
  const gotVerdict = useRef(false)
  const timers = useRef<number[]>([])
  const busy = state === 'connecting' || state === 'running'

  useEffect(() => () => { timers.current.forEach(clearTimeout); ws.current?.close() },[])

  useEffect(() => {
    if(!selectedJob) return
    let stop = false
    let timer = 0
    const poll = async () => {
      try {
        const data = await requestJSON<WatchDetail>(`/watch/reviews/${selectedJob}`)
        if(stop) return
        const valid = data.valid_result !== false
        const shown = data.messages.filter((message) => message.kind !== 'verdict' || valid) as unknown as Message[]
        setMessages(shown); setLoadedPR(data.pull_request); setError(data.status==='failed'?(data.error??'The AI output did not pass validation. Retry this review.'):null)
        const diffMessage = shown.find((message) => message.kind==='diff'); if(diffMessage) setDiff(diffMessage.text)
        const verdict = shown.find((message) => message.kind==='verdict'); setMap(verdict?readMap(verdict.text):null)
        setReviewId(selectedJob)
        if(data.status==='running'||data.status==='queued'){ setState('running'); setStatus(`PR #${data.pull_request.number} · ${data.status}`); timer=window.setTimeout(poll,2000) }
        else if(data.status==='completed'){ setState('complete'); setStatus(`Review complete · #${data.pull_request.number}`) }
        else { setState('error'); setStatus(`PR #${data.pull_request.number} · ${data.status}`) }
      } catch(reason) { if(!stop){ setState('error'); setError(reason instanceof Error?reason.message:'Could not load review.') } }
    }
    void poll()
    return () => { stop = true; window.clearTimeout(timer) }
  },[selectedJob])

  function viewJob(id:string) {
    run.current++
    timers.current.forEach(clearTimeout); timers.current=[]; ws.current?.close(); ws.current=null
    setSelectedJob(id)
  }

  function reset(next:'live'|'replay') {
    setSelectedJob(null); setLoadedPR(null)
    const token = ++run.current
    timers.current.forEach(clearTimeout); timers.current=[]; ws.current?.close(); ws.current=null
    count.current=0; gotVerdict.current=false
    setMessages([]); setMap(null); setReviewId(null); setError(null); setState('connecting')
    setStatus(next === 'live' ? 'Starting' : 'Replaying')
    return token
  }

  function receive(message:Message,token:number) {
    if(token!==run.current) return
    count.current++
    setMessages((current)=>[...current,message])
    if(message.kind==='diff') setDiff(message.text)
    if(message.kind==='verdict') { gotVerdict.current=true; const result=readMap(message.text); setMap(result); if(!result) setError('Verdict arrived, but the tradeoff map could not be parsed.') }
    setState('running'); setStatus(message.kind==='verdict'?'Verdict reached':`${count.current} messages`)
  }

  async function localReplay(token:number) {
    setStatus('Local replay')
    try {
      const response=await fetch('/fixtures/transcripts/demo.json')
      if(!response.ok) throw new Error(`Fixture request failed (${response.status})`)
      const fixture=await response.json() as {messages:Message[];tradeoff_map?:TradeoffMap}
      if(token!==run.current) return
      const playback=[...fixture.messages]
      if(fixture.tradeoff_map) playback.push({id:`verdict-${fixture.tradeoff_map.review_id}`,review_id:fixture.tradeoff_map.review_id,round:playback.at(-1)?.round??3,agent:'system',kind:'verdict',position:null,confidence:null,claims:[],references:[],text:JSON.stringify(fixture.tradeoff_map),ts:Date.now()/1000})
      playback.forEach((message,index)=>timers.current.push(window.setTimeout(()=>{receive(message,token); if(index===playback.length-1&&token===run.current){setState('complete');setStatus('Replay complete')}},index*650)))
    } catch(reason) { if(token===run.current){setState('error');setStatus('Replay failed');setError(reason instanceof Error?reason.message:'Could not load replay fixture.')} }
  }

  function connect(path:string,token:number,fallback=false) {
    const connection=new WebSocket(wsUrl(path)); ws.current=connection
    connection.onmessage=(event)=>{if(token!==run.current)return;try{receive(JSON.parse(event.data) as Message,token)}catch{setError('The server sent an invalid message.')}}
    connection.onclose=()=>{
      if(token!==run.current)return
      ws.current=null
      if(fallback&&count.current===0) void localReplay(token)
      else if(gotVerdict.current){setState('complete');setStatus('Verdict reached')}
      else if(count.current){setState('error');setStatus('Stream ended');setError('Connection closed early.')}
      else if(!fallback){setState('error');setStatus('No messages');setError('No messages received.')}
    }
    connection.onerror=()=>connection.close()
  }

  async function submit() {
    if(!diff.trim()){setError('Paste a diff first.');return}
    const token=reset('live')
    try {
      const response=await fetch('/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({diff})})
      if(!response.ok)throw new Error(`Could not start review (${response.status})`)
      const result=await response.json() as {review_id?:string}
      if(!result.review_id)throw new Error('Server did not return a review ID.')
      if(token!==run.current)return
      setReviewId(result.review_id);setStatus('Connecting');connect(`/ws/${result.review_id}`,token)
    } catch(reason) {if(token===run.current){setState('error');setStatus('Failed to start');setError(reason instanceof Error?reason.message:'Could not reach the server.')}}
  }

  function replay(){const token=reset('replay');connect('/replay',token,true)}
  async function loadDemo(){try{const response=await fetch('/fixtures/diffs/demo.diff');if(!response.ok)throw new Error(`Demo request failed (${response.status})`);setDiff(await response.text());setError(null)}catch(reason){setError(reason instanceof Error?reason.message:'Could not load demo diff.')}}

  const [dark,setDark]=useState(()=>document.documentElement.classList.contains('dark'))
  function toggleTheme(){const next=!dark;document.documentElement.classList.toggle('dark',next);localStorage.setItem('council-theme',next?'dark':'light');setDark(next)}

  const latestByAgent=new Map<Agent,Message>()
  for(const message of messages){if(message.agent!=='system'&&message.kind!=='diff'&&message.kind!=='verdict')latestByAgent.set(message.agent,message)}
  const activeRound=Math.max(1,...messages.map((message)=>message.round))
  function moveAgentTab(event:KeyboardEvent<HTMLButtonElement>,index:number){
    let next=index
    if(event.key==='ArrowDown')next=(index+1)%agents.length
    else if(event.key==='ArrowUp')next=(index-1+agents.length)%agents.length
    else if(event.key==='Home')next=0
    else if(event.key==='End')next=agents.length-1
    else return
    event.preventDefault()
    agentTabs.current[next]?.focus()
  }
  const stateDot=state==='error'?'bg-destructive':busy?'bg-warn':'bg-primary'

  return <div className="perch-shell min-h-dvh bg-background text-foreground">
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-background/90 px-4 backdrop-blur sm:px-7">
      <a href="#workspace" className="font-mono text-lg font-semibold tracking-tight text-foreground">perch</a>
      <div className="flex items-center gap-1.5 sm:gap-3">
        <Badge variant="outline" className="hidden items-center gap-2 border-border text-muted-foreground sm:inline-flex"><span className={cn('size-1.5 rounded-full',stateDot)}/>{status}</Badge>
        {reviewId&&<code className="hidden rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground lg:inline-flex">{reviewId}</code>}
        <Button variant="ghost" size="icon" aria-label="GitHub and connections" title="GitHub and connections" aria-expanded={showGithub} onClick={()=>setShowGithub((open)=>!open)}><IconBrandGithub/></Button>
        <Button variant="ghost" size="icon" aria-label="Upload diff" title="Upload diff" onClick={()=>fileInput.current?.click()} disabled={busy}><IconUpload/></Button>
        <Button variant="ghost" size="icon" aria-label="Replay demo" title="Replay demo" onClick={replay} disabled={busy}><IconRefresh/></Button>
        <Button className="h-9 px-3 text-xs sm:px-4 sm:text-sm" aria-label="Run council" onClick={()=>void submit()} disabled={busy}>{busy?'Thinking':'Run council'}</Button>
        <Button variant="ghost" size="icon" aria-label={dark?'Switch to light mode':'Switch to dark mode'} title={dark?'Switch to light mode':'Switch to dark mode'} onClick={toggleTheme}>{dark?<IconSun/>:<IconMoon/>}</Button>
      </div>
    </header>
    <input ref={fileInput} type="file" accept=".diff,.patch,.txt,text/plain" className="sr-only" aria-label="Upload a diff file" onChange={(event)=>{const file=event.currentTarget.files?.[0];if(file)void file.text().then((content)=>{setDiff(content);setError(null)}).catch(()=>setError('Could not read that file.'));event.currentTarget.value=''}}/>
    <main id="workspace" className="perch-main mx-auto flex w-full max-w-[1540px] flex-1 flex-col px-3 py-3 sm:px-5 sm:py-4">
      {error&&<Alert className="mb-3 border-border bg-surface"><IconAlertTriangle size={18} className="mt-0.5 text-destructive"/><div className="flex-1"><AlertTitle>Error</AlertTitle><AlertDescription>{error}</AlertDescription></div><button aria-label="Dismiss" className="text-muted-foreground hover:text-foreground" onClick={()=>setError(null)}><IconX size={16}/></button></Alert>}
      {loadedPR&&<p className="mb-3 text-xs text-muted-foreground"><a className="text-accent-strong underline" href={loadedPR.url} target="_blank" rel="noopener noreferrer">{loadedPR.repository} #{loadedPR.number}: {loadedPR.title}</a> · {loadedPR.head_sha.slice(0,7)} · {loadedPR.author}</p>}
      <section hidden={!showGithub} aria-label="GitHub integration" className="mb-3 flex flex-col gap-4 rounded-[14px] border border-border bg-surface-raised p-4">
        <GitHubPanel busy={busy&&!selectedJob} selectedJob={selectedJob} onSelectJob={viewJob} onWatch={setWatch} onAuthError={setGithubError} onLoadPR={(content,pr)=>{setDiff(content);setLoadedPR(pr);setError(null)}}/>
        <details className="border-t border-border pt-3"><summary className="cursor-pointer text-xs font-medium text-accent-strong">Connections</summary><div className="pt-3"><ConnectionsPanel locked={busy||!!watch?.enabled||!!watch?.active} repository={watch?.repository??undefined} githubError={githubError}/></div></details>
      </section>
      <div className="perch-workspace grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(300px,.9fr)_minmax(0,1.6fr)]">
        <section className="diff-panel flex min-h-[430px] min-w-0 flex-col overflow-hidden rounded-[14px] border border-border bg-surface-raised" aria-labelledby="diff-heading">
          <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-4"><h1 id="diff-heading" className="text-sm font-semibold">Diff</h1><div className="flex items-center gap-3"><span className="font-mono text-[11px] text-muted-foreground">{diff?`${diff.split('\n').length} lines`:'No file'}</span><Button variant="ghost" className="h-7 px-2 text-xs text-accent-strong" onClick={()=>void loadDemo()} disabled={busy}><IconFileDiff/>Demo</Button></div></div>
          <ScrollArea className="diff-view min-h-[220px] flex-1 bg-surface"><div className="min-h-full">{diff?<pre className="m-0 min-w-max py-2 font-mono text-[11px] leading-[1.62] text-foreground/80">{diff.split('\n').map((line,index)=><DiffLine key={`${index}-${line}`} line={line}/>)}</pre>:<div className="flex h-full min-h-[220px] flex-col items-center justify-center gap-2 p-7 text-center text-muted-foreground"><IconFileDiff size={25}/><p className="text-xs">Upload or paste a diff</p></div>}</div></ScrollArea>
          <details className="diff-input shrink-0 border-t border-border bg-surface-raised px-4 py-2" open={!diff}><summary className="cursor-pointer select-none text-xs font-medium text-accent-strong">{diff?'Edit diff':'Paste diff'}</summary><Textarea aria-label="Paste a diff" placeholder={'Paste a unified diff here\n\ndiff --git a/src/file.ts b/src/file.ts'} value={diff} onChange={(event)=>setDiff(event.currentTarget.value)} rows={4} disabled={busy} className="mt-2 resize-y border-border bg-background font-mono text-[11px]"/></details>
        </section>
        <section className="agent-panel flex min-h-[610px] min-w-0 flex-col" aria-label="Council agents">
          <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-1"><h2 className="text-sm font-semibold">Council</h2><span className="font-mono text-[11px] text-muted-foreground">{messages.length?`${messages.length} messages`:'Five reviewers'}</span></div>
          <div ref={agentList} className="agent-list relative mt-3 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-14">
            {agents.map((agent,index)=>{
              const latest=latestByAgent.get(agent.id)
              const thinking=busy&&(!latest||latest.round<activeRound)
              const open=selectedAgent===agent.id
              const Icon=agent.icon
              return <div key={agent.id} className="overflow-hidden rounded-[12px] border border-border bg-surface-raised">
                <button ref={(element)=>{agentTabs.current[index]=element}} id={`agent-tab-${agent.id}`} type="button" aria-expanded={open} aria-controls={`agent-panel-${agent.id}`} onClick={()=>setSelectedAgent(open?null:agent.id)} onKeyDown={(event)=>moveAgentTab(event,index)} className={cn('agent-tab flex w-full items-center gap-2.5 px-3.5 py-4 text-left transition-colors hover:bg-accent-soft/60',open?'text-foreground':'text-muted-foreground')}>
                  <span aria-hidden="true" className={cn('size-2.5 shrink-0 rounded-full',thinking&&'animate-pulse')} style={{backgroundColor:agent.color}}/>
                  <Icon size={16} stroke={1.8} className="shrink-0 text-accent-strong"/>
                  <span className="shrink-0 text-sm font-semibold">{agent.name}</span>
                  {latest?.position&&<Badge variant={positionBadge[latest.position]}>{symbols[latest.position]} {labels[latest.position]}</Badge>}
                  <span className={cn('min-w-0 flex-1 truncate text-[11px]',thinking?'text-accent-strong':'text-muted-foreground')}>{open?'':thinking?'Thinking':latest?.text??'No contribution yet'}</span>
                  {open&&thinking&&<span className="text-xs font-medium text-accent-strong">Thinking</span>}
                  {typeof latest?.confidence==='number'&&<span className="font-mono text-[10px] text-muted-foreground">{Math.round(latest.confidence*100)}%</span>}
                  <IconChevronDown size={16} className={cn('shrink-0 transition-transform duration-300',open&&'rotate-180')}/>
                </button>
                <div id={`agent-panel-${agent.id}`} role="region" aria-labelledby={`agent-tab-${agent.id}`} inert={!open} className={cn('grid transition-[grid-template-rows] duration-300 ease-in-out motion-reduce:transition-none',open?'grid-rows-[1fr]':'grid-rows-[0fr]')}>
                  <div className="min-h-0 overflow-hidden"><div className="max-h-[320px] overflow-y-auto border-t border-border">{latest?<Contribution message={latest}/>:<p className="px-4 py-5 text-sm text-muted-foreground">{busy?'Thinking':'No contribution yet'}</p>}</div></div>
                </div>
              </div>
            })}
            <AgentOwl listRef={agentList} busy={busy} thinking={agents.map((agent)=>{const latest=latestByAgent.get(agent.id);return busy&&(!latest||latest.round<activeRound)})}/>
          </div>
          <TradeoffPanel map={map} busy={busy}/>
        </section>
      </div>
    </main>
  </div>
}

function DiffLine({line}:{line:string}){let kind='';if(line.startsWith('+++')||line.startsWith('---')||line.startsWith('diff ')||line.startsWith('index '))kind='text-muted-foreground';else if(line.startsWith('+'))kind='bg-emerald-500/10 text-accent-strong';else if(line.startsWith('-'))kind='bg-red-500/10 text-destructive';else if(line.startsWith('@@'))kind='bg-accent-soft text-accent-strong';return <span className={cn('block min-w-max whitespace-pre px-3.5',kind)}>{line||' '}</span>}
const positionBadge:Record<Position,'success'|'warning'|'destructive'>={approve:'success',approve_with_changes:'warning',block:'destructive',escalate:'warning'}
function Contribution({message}:{message:Message}){return <div className="flex flex-col gap-2.5 p-4"><div className="flex flex-wrap items-center gap-2">{message.references?.map((reference)=>{const target=getAgent(reference.agent);return target?<span key={`${reference.message_id}-${reference.stance}`} className="text-[11px] text-accent-strong">{stanceLabels[reference.stance]} {target.name}</span>:null})}</div><p className="max-w-[78ch] whitespace-pre-wrap text-[13px] leading-[1.65] text-foreground">{message.text}</p>{message.claims?.length>0&&<div className="grid gap-1.5 pt-1">{message.claims.map((claim,index)=><div key={`${claim.file}-${claim.line}-${index}`} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-2 border-t border-border pt-1.5"><div className="flex items-center gap-2"><Badge variant={claim.severity==='critical'||claim.severity==='high'?'destructive':claim.severity==='medium'?'warning':'secondary'} className="text-[9px]">{claim.severity}</Badge><code className="max-w-[42vw] truncate rounded bg-muted px-1 py-0.5 font-mono text-[10px]">{claim.file}{claim.line==null?'':`:${claim.line}`}</code></div><span className="col-span-2 mt-1 text-xs leading-snug text-muted-foreground sm:col-span-1 sm:mt-0">{claim.concern}</span></div>)}</div>}</div>}
function TradeoffPanel({map,busy}:{map:TradeoffMap|null;busy:boolean}){
  const [expanded,setExpanded]=useState(false)
  const summary=map?`${map.resolved.length} resolved · ${map.unresolved.length} unresolved · ${map.human_decisions.length} for a human`:busy?'Waiting on the council':'Pending'
  return <section className={cn('tradeoff-panel mt-3 shrink-0 rounded-[12px] border border-border bg-surface-raised',map?.outcome==='escalate'&&'tradeoff-escalate')} aria-labelledby="tradeoff-heading">
    <div className="flex items-center justify-between gap-3 px-3.5 py-2">
      <div className="flex min-w-0 items-center gap-2"><h3 id="tradeoff-heading" className="text-xs font-semibold">Tradeoff map</h3>{map&&<Badge variant={map.outcome==='escalate'?'warning':'success'}>{map.outcome}</Badge>}<span className="truncate text-[10px] text-muted-foreground">{summary}</span></div>
      <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs text-accent-strong" aria-expanded={expanded} aria-controls="tradeoff-body" onClick={()=>setExpanded((open)=>!open)}>{expanded?'Shrink':'Enlarge'}<IconChevronDown size={14} className={cn('transition-transform duration-300',expanded&&'rotate-180')}/></Button>
    </div>
    <div id="tradeoff-body" inert={!expanded} className={cn('grid transition-[grid-template-rows] duration-300 ease-in-out motion-reduce:transition-none',expanded?'grid-rows-[1fr]':'grid-rows-[0fr]')}>
      <div className="min-h-0 overflow-hidden"><div className="tradeoff-content max-h-[50vh] overflow-y-auto border-t border-border px-3.5 py-2">{map?<div className="grid grid-cols-1 gap-x-5 md:grid-cols-3"><MapList label="Resolved" items={map.resolved} empty="None"/><MapList label="Unresolved" items={map.unresolved} empty="None"/><MapList label="Human decisions" items={map.human_decisions} empty="None"/></div>:<p className="py-1 text-xs text-muted-foreground">Open questions and decisions for a person to make.</p>}</div></div>
    </div>
  </section>
}
function MapList({label,items,empty}:{label:string;items:string[];empty:string}){return <div className="min-w-0 py-1.5 md:px-2 md:first:pl-0"><div className="text-xs font-semibold text-accent-strong">{label}</div>{items.length?<div className="mt-1 flex flex-col gap-1">{items.map((item,index)=><p key={`${label}-${index}`} className="break-words text-xs leading-snug text-muted-foreground">{item}</p>)}</div>:<p className="mt-1 text-xs text-muted-foreground">{empty}</p>}</div>}
