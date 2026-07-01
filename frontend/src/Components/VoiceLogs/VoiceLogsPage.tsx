import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  listVoiceLogs,
  getVoiceLogAudio,
  deleteVoiceLog,
  clearVoiceLogs,
} from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'

type Kind = 'tts' | 'translate' | 'overview' | 'audio_overview'

interface VoiceLogEntry {
  id: string
  kind: Kind
  ts: string
  status: number
  duration_ms?: number | null
  error?: string | null

  // TTS-specific
  language?: string
  voice?: string
  speaking_rate?: number | null
  pitch?: number | null
  input_chars?: number
  stripped_chars?: number
  raw_text?: string
  stripped_text?: string
  audio_bytes?: number
  audio_kb?: number
  audio_path?: string | null

  // translate-specific
  target_language?: string
  style?: string
  model?: string
  output_chars?: number
  output_text?: string

  // overview-specific
  dataset_id?: string
  duration_seconds?: number | null
  user_name?: string
  extra?: { tts_model?: string; audio_format?: string }
}

function hasPlayableAudio(entry: VoiceLogEntry): boolean {
  return (entry.kind === 'tts' || entry.kind === 'audio_overview') && !!entry.audio_path
}

function entryKindMeta(kind: Kind) {
  return KIND_META[kind] || KIND_META.tts
}

interface Summary {
  total: number
  tts: number
  translate: number
  overview: number
  errors: number
  audio_kb: number
  input_chars: number
  output_chars: number
  max_entries: number
  audio_retention_days: number
  enabled: boolean
  keep_audio: boolean
}

const KIND_META: Record<Kind, { label: string; color: string; icon: string }> = {
  tts:            { label: 'TTS',            color: 'text-info bg-sky-500/10 border-sky-500/30',           icon: 'fa-solid fa-volume-high' },
  translate:      { label: 'Translate',      color: 'text-purple-300 bg-purple-500/10 border-purple-500/30', icon: 'fa-solid fa-language' },
  overview:       { label: 'Overview',       color: 'text-success bg-emerald-500/10 border-emerald-500/30', icon: 'fa-solid fa-microphone-lines' },
  audio_overview: { label: 'Audio Overview', color: 'text-success bg-emerald-500/10 border-emerald-500/30', icon: 'fa-solid fa-headphones' },
}

function fmtTs(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleString(undefined, {
      month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return ts
  }
}

function fmtDuration(ms: number | null | undefined): string {
  if (!ms || ms < 0) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function fmtBytes(kb: number | null | undefined): string {
  if (!kb) return '0 KB'
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`
  return `${kb} KB`
}

function statusBadge(status: number, error?: string | null) {
  if (error || status >= 400) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-bold bg-red-500/15 border border-red-500/40 text-destructive">
        <i className="fa-solid fa-circle-exclamation text-[9px]" /> {status || 'err'}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-bold bg-emerald-500/15 border border-emerald-500/40 text-success">
      <i className="fa-solid fa-check text-[9px]" /> {status}
    </span>
  )
}

export default function VoiceLogsPage() {
  const [loading, setLoading] = useState(true)
  const [entries, setEntries] = useState<VoiceLogEntry[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [filterKind, setFilterKind] = useState<Kind | 'all'>('all')
  const [search, setSearch] = useState('')
  const [errorsOnly, setErrorsOnly] = useState(false)
  const [selected, setSelected] = useState<VoiceLogEntry | null>(null)
  const [audioBlobUrl, setAudioBlobUrl] = useState<string | null>(null)
  const [audioLoading, setAudioLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    try {
      const data = await listVoiceLogs({
        kind: filterKind === 'all' ? undefined : filterKind,
        limit: 200,
      })
      setEntries(data.entries || [])
      setSummary(data.summary || null)
    } catch (err: any) {
      toast.error(err?.response?.data?.error || err?.message || 'Failed to load logs')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [filterKind])

  useEffect(() => { load() }, [load])

  // Revoke previous blob URL whenever we switch entries
  useEffect(() => {
    return () => {
      if (audioBlobUrl) URL.revokeObjectURL(audioBlobUrl)
    }
  }, [audioBlobUrl])

  const filtered = useMemo(() => {
    let list = entries
    if (errorsOnly) list = list.filter(e => !!e.error || (e.status || 200) >= 400)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(e => {
        const blobs = [
          e.raw_text, e.stripped_text, e.output_text, e.voice,
          e.model, e.user_name, e.dataset_id, e.target_language, e.language,
        ]
        return blobs.some(v => v && v.toLowerCase().includes(q))
      })
    }
    return list
  }, [entries, search, errorsOnly])

  const handleSelect = async (entry: VoiceLogEntry) => {
    setSelected(entry)
    if (audioBlobUrl) {
      URL.revokeObjectURL(audioBlobUrl)
      setAudioBlobUrl(null)
    }
    if (entry.kind !== 'tts' && entry.kind !== 'audio_overview') return
    if (!entry.audio_path) return
    setAudioLoading(true)
    try {
      const blob = await getVoiceLogAudio(entry.id)
      const url = URL.createObjectURL(blob)
      setAudioBlobUrl(url)
      requestAnimationFrame(() => {
        audioRef.current?.play().catch(() => { /* user gesture might be required */ })
      })
    } catch (err: any) {
      toast.error(err?.response?.data?.error || 'Failed to load audio')
    } finally {
      setAudioLoading(false)
    }
  }

  const handleDelete = async (entry: VoiceLogEntry) => {
    if (!confirm('Delete this log entry and its audio file?')) return
    try {
      await deleteVoiceLog(entry.id)
      toast.success('Entry deleted')
      if (selected?.id === entry.id) {
        setSelected(null)
        if (audioBlobUrl) URL.revokeObjectURL(audioBlobUrl)
        setAudioBlobUrl(null)
      }
      setEntries(prev => prev.filter(e => e.id !== entry.id))
    } catch (err: any) {
      toast.error(err?.response?.data?.error || 'Failed to delete')
    }
  }

  const handleClearAll = async () => {
    if (!confirm('Permanently delete ALL voice logs (and audio recordings)? This cannot be undone.')) return
    try {
      const res = await clearVoiceLogs()
      toast.success(`Removed ${res?.removed ?? 0} entries`)
      setEntries([])
      setSelected(null)
      if (audioBlobUrl) URL.revokeObjectURL(audioBlobUrl)
      setAudioBlobUrl(null)
      load(true)
    } catch (err: any) {
      toast.error(err?.response?.data?.error || 'Failed to clear logs')
    }
  }

  const handleDownloadAudio = async (entry: VoiceLogEntry) => {
    try {
      const blob = await getVoiceLogAudio(entry.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `tts-${entry.id}.mp3`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err: any) {
      toast.error(err?.response?.data?.error || 'Download failed')
    }
  }

  const handleCopyText = (text?: string) => {
    if (!text) return
    navigator.clipboard.writeText(text).then(
      () => toast.success('Copied'),
      () => toast.error('Copy failed'),
    )
  }

  if (loading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center gap-2 text-muted-foreground text-sm">
        <LogoSpinner size={18} /> Loading voice logs…
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-start gap-4 justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
            <i className="fa-solid fa-clipboard-list text-primary" />
            Voice activity log
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Every TTS, translation, and audio-overview request you make is recorded here so you (and the team)
            can replay the exact text + audio and debug issues.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-muted-foreground hover:bg-muted transition-colors flex items-center gap-2"
          >
            {refreshing ? <LogoSpinner size={12} /> : <i className="fa-solid fa-arrows-rotate text-[10px]" />}
            Refresh
          </button>
          <button
            onClick={handleClearAll}
            className="px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/40 text-red-200 text-xs font-semibold hover:bg-red-500/25 transition-colors flex items-center gap-2"
          >
            <i className="fa-solid fa-trash-can text-[10px]" />
            Clear all
          </button>
        </div>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <StatCard label="Entries"        value={String(summary.total)}    sub={`cap ${summary.max_entries}`} icon="fa-solid fa-database" tone="indigo" />
          <StatCard label="TTS requests"   value={String(summary.tts)}      icon="fa-solid fa-volume-high" tone="sky" />
          <StatCard label="Translations"   value={String(summary.translate)} icon="fa-solid fa-language" tone="purple" />
          <StatCard label="Overviews"      value={String(summary.overview)} icon="fa-solid fa-microphone-lines" tone="emerald" />
          <StatCard label="Errors"         value={String(summary.errors)}   icon="fa-solid fa-circle-exclamation" tone={summary.errors > 0 ? 'red' : 'slate'} />
          <StatCard label="Audio stored"   value={fmtBytes(summary.audio_kb)} sub={`keeps ${summary.audio_retention_days}d`} icon="fa-solid fa-file-audio" tone="amber" />
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center bg-card border border-border rounded-lg p-1">
          {(['all', 'tts', 'translate', 'overview'] as const).map(k => (
            <button
              key={k}
              onClick={() => setFilterKind(k)}
              className={`px-3 py-1.5 rounded-md text-[11px] font-semibold transition-colors capitalize ${
                filterKind === k ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {k}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-card border border-border text-[11px] text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={errorsOnly}
            onChange={(e) => setErrorsOnly(e.target.checked)}
            className="accent-red-500"
          />
          Errors only
        </label>
        <div className="flex-1 min-w-[220px]">
          <div className="relative">
            <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-xs" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search text, voice, model, dataset…"
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-card border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50"
            />
          </div>
        </div>
      </div>

      {/* Two-pane layout */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* List */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground font-semibold flex items-center justify-between">
            <span>Recent ({filtered.length})</span>
          </div>
          <div className="max-h-[70vh] overflow-y-auto divide-y divide-[#1d2238]">
            {filtered.length === 0 && (
              <div className="px-4 py-10 text-center text-sm text-muted-foreground">
                No log entries match your filter.
              </div>
            )}
            {filtered.map(entry => {
              const meta = entryKindMeta(entry.kind)
              const isSelected = selected?.id === entry.id
              const preview =
                entry.output_text ||
                entry.stripped_text ||
                entry.raw_text ||
                '(no text)'
              const langChip =
                entry.kind === 'tts'            ? entry.language :
                entry.kind === 'translate'      ? entry.target_language :
                entry.kind === 'overview'       ? entry.language :
                entry.kind === 'audio_overview' ? entry.language : undefined
              return (
                <button
                  key={entry.id}
                  onClick={() => handleSelect(entry)}
                  className={`w-full text-left px-4 py-3 transition-colors ${
                    isSelected ? 'bg-primary/10' : 'hover:bg-card'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-bold border ${meta.color}`}>
                      <i className={`${meta.icon} text-[9px]`} /> {meta.label}
                    </span>
                    {statusBadge(entry.status, entry.error)}
                    {hasPlayableAudio(entry) && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-card border border-border text-muted-foreground">
                        <i className="fa-solid fa-headphones text-[9px]" /> {fmtBytes(entry.audio_kb)}
                      </span>
                    )}
                    {langChip && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-card border border-border text-muted-foreground uppercase">
                        {langChip}
                      </span>
                    )}
                    <span className="ml-auto text-[10px] text-muted-foreground">{fmtTs(entry.ts)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 leading-snug" dir="auto">
                    {preview.slice(0, 200)}
                  </p>
                  <div className="mt-1.5 flex items-center gap-3 text-[10px] text-muted-foreground">
                    {entry.kind === 'tts' && (
                      <>
                        <span><i className="fa-solid fa-microphone text-[9px] mr-1" />{entry.voice || '—'}</span>
                        <span>{entry.stripped_chars ?? entry.input_chars ?? 0} chars</span>
                      </>
                    )}
                    {entry.kind === 'translate' && (
                      <>
                        <span>{entry.input_chars ?? 0} → {entry.output_chars ?? 0} chars</span>
                        <span><i className="fa-solid fa-robot text-[9px] mr-1" />{entry.model || '—'}</span>
                      </>
                    )}
                    {entry.kind === 'overview' && (
                      <>
                        <span>{entry.output_chars ?? 0} chars</span>
                        <span>~{entry.duration_seconds ?? 0}s narration</span>
                      </>
                    )}
                    {entry.kind === 'audio_overview' && (
                      <>
                        <span><i className="fa-solid fa-microphone text-[9px] mr-1" />{entry.voice || '—'}</span>
                        <span>{entry.output_chars ?? 0} chars</span>
                        <span>~{entry.duration_seconds ?? 0}s target</span>
                      </>
                    )}
                    <span className="ml-auto">{fmtDuration(entry.duration_ms)}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Detail */}
        <div className="lg:col-span-3 bg-card border border-border rounded-xl overflow-hidden">
          {!selected && (
            <div className="h-full min-h-[40vh] flex items-center justify-center text-muted-foreground text-sm">
              <div className="text-center">
                <i className="fa-solid fa-arrow-left text-2xl text-slate-700 mb-3 block" />
                Select an entry to inspect text + audio
              </div>
            </div>
          )}
          {selected && (
            <div className="p-5 space-y-4">
              <div className="flex items-start gap-3 justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-bold border ${entryKindMeta(selected.kind).color}`}>
                      <i className={`${entryKindMeta(selected.kind).icon} text-[9px]`} /> {entryKindMeta(selected.kind).label}
                    </span>
                    {statusBadge(selected.status, selected.error)}
                    <span className="text-[10px] text-muted-foreground">{fmtTs(selected.ts)}</span>
                  </div>
                  <h2 className="text-sm font-bold text-foreground">Request <span className="font-mono text-muted-foreground">#{selected.id}</span></h2>
                </div>
                <div className="flex items-center gap-1">
                  {hasPlayableAudio(selected) && (
                    <button
                      onClick={() => handleDownloadAudio(selected)}
                      className="px-2.5 py-1.5 rounded-md bg-card border border-border text-[10px] font-semibold text-muted-foreground hover:bg-muted transition-colors"
                      title="Download audio"
                    >
                      <i className="fa-solid fa-download mr-1" /> Audio
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(selected)}
                    className="px-2.5 py-1.5 rounded-md bg-red-500/15 border border-red-500/40 text-destructive text-[10px] font-semibold hover:bg-red-500/25 transition-colors"
                    title="Delete entry"
                  >
                    <i className="fa-solid fa-trash-can" />
                  </button>
                </div>
              </div>

              {/* Audio player */}
              {(selected.kind === 'tts' || selected.kind === 'audio_overview') && (
                <div className="bg-muted border border-border rounded-lg p-3">
                  {audioLoading && (
                    <div className="text-xs text-muted-foreground flex items-center gap-2">
                      <LogoSpinner size={14} /> Loading audio…
                    </div>
                  )}
                  {!audioLoading && audioBlobUrl && (
                    <audio
                      ref={audioRef}
                      src={audioBlobUrl}
                      controls
                      className="w-full"
                    />
                  )}
                  {!audioLoading && !audioBlobUrl && !selected.audio_path && (
                    <p className="text-xs text-muted-foreground">
                      No audio was stored for this entry (TTS may have failed, or audio recording is disabled).
                    </p>
                  )}
                </div>
              )}

              {/* Error */}
              {selected.error && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                  <p className="text-[11px] uppercase tracking-wider text-destructive font-bold mb-1">Error</p>
                  <code className="text-xs text-red-200 whitespace-pre-wrap break-words block">{selected.error}</code>
                </div>
              )}

              {/* Meta grid */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-[11px]">
                <Meta label="Status"        value={String(selected.status || '—')} />
                <Meta label="Took"          value={fmtDuration(selected.duration_ms)} />
                {selected.kind === 'tts' && <>
                  <Meta label="Voice"         value={selected.voice || '—'} mono />
                  <Meta label="Language"      value={selected.language || '—'} />
                  <Meta label="Speaking rate" value={selected.speaking_rate?.toString() || '—'} />
                  <Meta label="Pitch"         value={selected.pitch?.toString() || '—'} />
                  <Meta label="Input chars"   value={String(selected.input_chars ?? 0)} />
                  <Meta label="Spoken chars"  value={String(selected.stripped_chars ?? 0)} />
                  <Meta label="Audio size"    value={fmtBytes(selected.audio_kb)} />
                </>}
                {selected.kind === 'translate' && <>
                  <Meta label="Target language" value={selected.target_language || '—'} />
                  <Meta label="Style"           value={selected.style || '—'} />
                  <Meta label="Model"           value={selected.model || '—'} mono />
                  <Meta label="Input chars"     value={String(selected.input_chars ?? 0)} />
                  <Meta label="Output chars"    value={String(selected.output_chars ?? 0)} />
                </>}
                {selected.kind === 'overview' && <>
                  <Meta label="Dataset"            value={selected.dataset_id || '—'} mono />
                  <Meta label="Language"           value={selected.language || '—'} />
                  <Meta label="Style"              value={selected.style || '—'} />
                  <Meta label="Target duration"    value={`${selected.duration_seconds ?? 0}s`} />
                  <Meta label="Model"              value={selected.model || '—'} mono />
                  <Meta label="User name"          value={selected.user_name || '—'} />
                  <Meta label="Output chars"       value={String(selected.output_chars ?? 0)} />
                </>}
                {selected.kind === 'audio_overview' && <>
                  <Meta label="Dataset"            value={selected.dataset_id || '—'} mono />
                  <Meta label="Language"           value={selected.language || '—'} />
                  <Meta label="Style"              value={selected.style || '—'} />
                  <Meta label="Target duration"    value={`${selected.duration_seconds ?? 0}s`} />
                  <Meta label="Overview model"     value={selected.model || '—'} mono />
                  <Meta label="TTS model"          value={selected.extra?.tts_model || '—'} mono />
                  <Meta label="Voice"              value={selected.voice || '—'} mono />
                  <Meta label="User name"          value={selected.user_name || '—'} />
                  <Meta label="Output chars"       value={String(selected.output_chars ?? 0)} />
                  <Meta label="Audio size"         value={fmtBytes(selected.audio_kb)} />
                </>}
              </div>

              {/* Text panels */}
              {selected.kind === 'tts' && (
                <>
                  <TextBlock
                    title="Stripped text (sent to TTS)"
                    text={selected.stripped_text}
                    onCopy={() => handleCopyText(selected.stripped_text)}
                  />
                  {selected.raw_text && selected.raw_text !== selected.stripped_text && (
                    <TextBlock
                      title="Raw input (before markdown stripping)"
                      text={selected.raw_text}
                      onCopy={() => handleCopyText(selected.raw_text)}
                      muted
                    />
                  )}
                </>
              )}
              {selected.kind === 'translate' && (
                <>
                  <TextBlock
                    title="Source text"
                    text={selected.raw_text}
                    onCopy={() => handleCopyText(selected.raw_text)}
                    muted
                  />
                  <TextBlock
                    title="Translated output"
                    text={selected.output_text}
                    onCopy={() => handleCopyText(selected.output_text)}
                  />
                </>
              )}
              {selected.kind === 'overview' && (
                <TextBlock
                  title="Generated narration"
                  text={selected.output_text}
                  onCopy={() => handleCopyText(selected.output_text)}
                />
              )}
              {selected.kind === 'audio_overview' && (
                <TextBlock
                  title="Generated narration (spoken)"
                  text={selected.output_text}
                  onCopy={() => handleCopyText(selected.output_text)}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, sub, icon, tone }: {
  label: string
  value: string
  sub?: string
  icon: string
  tone: 'indigo' | 'sky' | 'purple' | 'emerald' | 'red' | 'slate' | 'amber'
}) {
  const tones: Record<string, string> = {
    indigo:  'from-[#5A5AF6]/15 to-[#5A5AF6]/5 border-primary/30 text-primary',
    sky:     'from-sky-500/15 to-sky-500/5 border-sky-500/30 text-info',
    purple:  'from-purple-500/15 to-purple-500/5 border-purple-500/30 text-purple-300',
    emerald: 'from-emerald-500/15 to-emerald-500/5 border-emerald-500/30 text-success',
    red:     'from-red-500/15 to-red-500/5 border-red-500/30 text-destructive',
    amber:   'from-amber-500/15 to-amber-500/5 border-amber-500/30 text-warning',
    slate:   'from-slate-700/30 to-slate-700/5 border-border/40 text-muted-foreground',
  }
  return (
    <div className={`bg-gradient-to-br ${tones[tone]} border rounded-xl px-3 py-2.5`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-wider opacity-70">{label}</p>
          <p className="text-lg font-bold mt-0.5">{value}</p>
          {sub && <p className="text-[10px] opacity-60">{sub}</p>}
        </div>
        <i className={`${icon} text-base opacity-70`} />
      </div>
    </div>
  )
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-muted border border-border rounded-md px-2.5 py-1.5 min-w-0">
      <p className="text-[9px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`text-[11px] text-foreground truncate ${mono ? 'font-mono' : ''}`} title={value}>{value}</p>
    </div>
  )
}

function TextBlock({ title, text, onCopy, muted }: {
  title: string
  text?: string
  onCopy?: () => void
  muted?: boolean
}) {
  return (
    <div className={`rounded-lg border overflow-hidden ${muted ? 'border-border bg-muted' : 'border-primary/30 bg-primary/5'}`}>
      <div className="px-3 py-1.5 flex items-center justify-between border-b border-border">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-bold">{title}</p>
        {onCopy && (
          <button
            onClick={onCopy}
            className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            title="Copy text"
          >
            <i className="fa-solid fa-copy" /> Copy
          </button>
        )}
      </div>
      <pre className="px-3 py-2.5 text-xs text-foreground whitespace-pre-wrap break-words max-h-[40vh] overflow-y-auto font-sans leading-relaxed" dir="auto">
        {text || '(empty)'}
      </pre>
    </div>
  )
}
