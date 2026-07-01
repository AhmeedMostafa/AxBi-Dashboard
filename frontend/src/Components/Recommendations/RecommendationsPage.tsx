import { useEffect, useState } from 'react'
import { listDatasets, getRecommendations, generateRecommendations } from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'

interface Dataset {
  id: string
  filename: string
  category?: string
  resolved_category?: string
  status?: string
}

interface Signal {
  type: string
  severity: 'info' | 'warning' | 'critical'
  affected_entity: string
  confidence: number
  evidence: Record<string, unknown>
}

interface Recommendation {
  id: string
  title: string
  rationale: string
  priority: 'low' | 'medium' | 'high'
  triggered_by: string[]
  actions: string[]
  metrics: Record<string, unknown>
}

interface RecommendationsBlob {
  generated_at: string | null
  snapshot_hash: string | null
  signals: Signal[]
  recommendations: Recommendation[]
}

const PRIORITY_CONFIG = {
  high:   { label: 'High',   color: 'text-destructive',     bg: 'bg-destructive/10',     border: 'border-destructive/30',    dot: 'bg-destructive'   },
  medium: { label: 'Medium', color: 'text-warning',   bg: 'bg-warning/10',   border: 'border-amber-800/50',  dot: 'bg-amber-400' },
  low:    { label: 'Low',    color: 'text-muted-foreground',   bg: 'bg-card/20',   border: 'border-border/50',  dot: 'bg-slate-400' },
}

const SIGNAL_LABELS: Record<string, string> = {
  forecast_decline:         'Forecast Decline',
  forecast_growth:          'Forecast Growth',
  low_confidence_forecast:  'Low Confidence',
  severe_overfit:           'Model Overfit',
  shrinking_top_segment:    'Shrinking Top Segment',
  growing_at_risk_segment:  'Growing At-Risk Segment',
  concentration_risk:       'Concentration Risk',
  high_null_columns:        'Data Quality Issue',
  stale_data:               'Stale Data',
  high_forecast_error:      'High Forecast Error',
  missing_forecast:         'No Forecast Run',
  missing_segmentation:     'No Segmentation Run',
  report_insights:          'AI Report Insights',
}

const SIGNAL_SEVERITY_COLOR = {
  info:     'text-info bg-info/10 border-sky-800/40',
  warning:  'text-warning bg-warning/10 border-amber-800/40',
  critical: 'text-destructive bg-destructive/10 border-destructive/30',
}

function SignalBadge({ type }: { type: string }) {
  return (
    <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 border border-primary/30 text-primary">
      {SIGNAL_LABELS[type] || type.replace(/_/g, ' ')}
    </span>
  )
}

function RecommendationCard({ rec }: { rec: Recommendation }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = PRIORITY_CONFIG[rec.priority] || PRIORITY_CONFIG.medium
  const metricEntries = Object.entries(rec.metrics || {})

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} overflow-hidden`}>
      {/* Header */}
      <button
        className="w-full flex items-start gap-4 px-5 py-4 text-left"
        onClick={() => setExpanded(e => !e)}
      >
        {/* Priority indicator */}
        <div className="flex flex-col items-center gap-1 pt-0.5 shrink-0">
          <div className={`w-2.5 h-2.5 rounded-full ${cfg.dot}`} />
          <span className={`text-xs font-semibold uppercase tracking-wide ${cfg.color}`}>
            {cfg.label}
          </span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-foreground leading-snug">{rec.title}</p>
          <p className="text-sm text-muted-foreground mt-1 leading-relaxed">{rec.rationale}</p>

          {/* Triggered-by chips */}
          {rec.triggered_by?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {rec.triggered_by.map(t => <SignalBadge key={t} type={t} />)}
            </div>
          )}
        </div>

        <i className={`fa-solid fa-chevron-${expanded ? 'up' : 'down'} text-muted-foreground text-xs mt-1 shrink-0`} />
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="px-5 pb-5 border-t border-white/5">
          <div className="grid md:grid-cols-2 gap-6 pt-4">
            {/* Actions */}
            <div>
              <p className="text-xs text-muted-foreground uppercase tracking-wide mb-2">Next steps</p>
              <ul className="space-y-2">
                {(rec.actions || []).map((action, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground">
                    <span className="text-primary font-bold shrink-0 mt-0.5">{i + 1}.</span>
                    {action}
                  </li>
                ))}
              </ul>
            </div>

            {/* Key metrics */}
            {metricEntries.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide mb-2">Key numbers</p>
                <div className="grid grid-cols-2 gap-3">
                  {metricEntries.slice(0, 6).map(([k, v]) => (
                    <div key={k} className="bg-card/60 rounded-lg px-3 py-2">
                      <p className="text-xs text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</p>
                      <p className="text-sm font-semibold text-foreground mt-0.5">
                        {typeof v === 'number' ? v.toLocaleString() : String(v)}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function RecommendationsPage() {
  const [datasets, setDatasets]         = useState<Dataset[]>([])
  const [datasetsLoading, setDatasetsLoading] = useState(true)
  const [selectedId, setSelectedId]     = useState<string | null>(null)
  const [blob, setBlob]                 = useState<RecommendationsBlob | null>(null)
  const [loading, setLoading]           = useState(false)
  const [generating, setGenerating]     = useState(false)
  const [error, setError]               = useState<string | null>(null)

  useEffect(() => {
    listDatasets()
      .then((data: any) => {
        const all: Dataset[] = data?.datasets || data || []
        setDatasets(all)
        // Auto-select first completed dataset
        const first = all.find((d: Dataset) => d.status === 'completed') || all[0]
        if (first) {
          setSelectedId(first.id)
          loadRecommendations(first.id)
        }
      })
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadRecommendations = async (id: string) => {
    setLoading(true)
    setError(null)
    setBlob(null)
    try {
      const data = await getRecommendations(id)
      setBlob(data)
    } catch (e: any) {
      setError(e?.response?.data?.error || e?.message || 'Failed to load recommendations')
    } finally {
      setLoading(false)
    }
  }

  const selectDataset = (id: string) => {
    if (id === selectedId) return
    setSelectedId(id)
    loadRecommendations(id)
  }

  const handleGenerate = async (force = false) => {
    if (!selectedId) return
    setGenerating(true)
    setError(null)
    try {
      const data = await generateRecommendations(selectedId, force)
      setBlob(data)
    } catch (e: any) {
      setError(e?.response?.data?.message || e?.message || 'Generation failed')
    } finally {
      setGenerating(false)
    }
  }

  const selectedDataset = datasets.find(d => d.id === selectedId)
  const recs = blob?.recommendations || []
  const signals = blob?.signals || []
  const hasData = recs.length > 0 || signals.length > 0

  return (
    <div className="p-6 text-foreground">
      <div className="flex items-start justify-between mb-1 flex-wrap gap-3">
        <div>
          <h2 className="text-2xl font-bold">Recommendations</h2>
          <p className="text-muted-foreground text-sm mt-1">
            AI-generated action items based on your forecast, segmentation, and data quality signals.
          </p>
        </div>
      </div>

      {/* ── Dataset selector ── */}
      <div className="flex flex-wrap gap-3 my-6">
        {datasetsLoading ? (
          <p className="text-muted-foreground text-sm">Loading projects...</p>
        ) : datasets.length === 0 ? (
          <p className="text-muted-foreground text-sm">No datasets found. Upload a file first.</p>
        ) : (
          datasets.map(ds => {
            const active = ds.id === selectedId
            return (
              <button
                key={ds.id}
                onClick={() => selectDataset(ds.id)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-all ${
                  active
                    ? 'bg-primary/15 border-primary text-primary-foreground shadow-[0_0_0_1px_#5A5AF6]'
                    : 'bg-card border-border text-muted-foreground hover:border-primary/50 hover:text-foreground'
                }`}
              >
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm font-bold ${
                  active ? 'bg-primary text-primary-foreground' : 'bg-muted text-primary'
                }`}>
                  {ds.filename.slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <p className="text-sm font-semibold leading-tight max-w-[180px] truncate">{ds.filename}</p>
                  {(ds.resolved_category || ds.category) && (
                    <p className="text-xs text-muted-foreground capitalize">{ds.resolved_category || ds.category}</p>
                  )}
                </div>
                {active && (
                  <svg className="w-4 h-4 text-primary shrink-0 ml-1" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/>
                  </svg>
                )}
              </button>
            )
          })
        )}
      </div>

      {/* ── Action bar ── */}
      {selectedId && (
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => handleGenerate(false)}
            disabled={generating || loading}
            className="flex items-center gap-2 bg-primary hover:bg-primary/90 disabled:opacity-60 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            {generating ? (
              <><LogoSpinner size={14} /> Generating...</>
            ) : (
              <><i className="fa-solid fa-lightbulb" /> Generate Recommendations</>
            )}
          </button>
          {blob?.generated_at && (
            <button
              onClick={() => handleGenerate(true)}
              disabled={generating || loading}
              className="flex items-center gap-2 bg-card hover:bg-muted border border-border disabled:opacity-60 px-4 py-2 rounded-lg text-sm text-muted-foreground transition-colors"
            >
              <i className="fa-solid fa-arrows-rotate" /> Regenerate
            </button>
          )}
          {blob?.generated_at && (
            <span className="text-xs text-muted-foreground">
              Generated {new Date(blob.generated_at).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
              })}
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="bg-destructive/10 border border-destructive/30 rounded-lg p-3 text-destructive text-sm mb-4">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <LogoSpinner size={16} />
          Loading...
        </div>
      )}

      {/* ── Results ── */}
      {!loading && selectedId && blob && (
        <div className="max-w-4xl space-y-6">

          {/* Signals summary strip */}
          {signals.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-4">
              <p className="text-xs text-muted-foreground uppercase tracking-wide mb-3">
                {signals.length} signal{signals.length !== 1 ? 's' : ''} detected in {selectedDataset?.filename}
              </p>
              <div className="flex flex-wrap gap-2">
                {signals.map((sig, i) => {
                  const cls = SIGNAL_SEVERITY_COLOR[sig.severity] || SIGNAL_SEVERITY_COLOR.info
                  return (
                    <span
                      key={i}
                      className={`text-xs px-2.5 py-1 rounded-full border ${cls}`}
                      title={JSON.stringify(sig.evidence, null, 2)}
                    >
                      <i className={`fa-solid ${
                        sig.severity === 'critical' ? 'fa-circle-xmark' :
                        sig.severity === 'warning'  ? 'fa-triangle-exclamation' :
                        'fa-circle-info'
                      } mr-1`} />
                      {SIGNAL_LABELS[sig.type] || sig.type.replace(/_/g, ' ')}
                      {sig.affected_entity && (
                        <span className="ml-1 opacity-70">· {sig.affected_entity}</span>
                      )}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {recs.length > 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {recs.length} recommendation{recs.length !== 1 ? 's' : ''} for {selectedDataset?.filename}
              </p>
              {recs.map(rec => (
                <RecommendationCard key={rec.id} rec={rec} />
              ))}
            </div>
          ) : !generating && blob.generated_at ? (
            <div className="bg-success/10 border border-success/30 rounded-xl p-6 text-center">
              <i className="fa-solid fa-circle-check text-success text-2xl mb-2" />
              <p className="text-success font-semibold">No actionable signals detected</p>
              <p className="text-sm text-muted-foreground mt-1">
                Your data looks healthy — no significant forecast declines, at-risk segments, or data quality issues found.
              </p>
            </div>
          ) : null}

          {/* Empty: never generated */}
          {!blob.generated_at && recs.length === 0 && !generating && (
            <div className="bg-card border border-border rounded-xl p-8 text-center">
              <i className="fa-solid fa-lightbulb text-primary text-3xl mb-3" />
              <p className="text-foreground font-semibold text-lg mb-1">No recommendations yet</p>
              <p className="text-sm text-muted-foreground mb-4">
                Run a forecast or segmentation first, then click "Generate Recommendations"
                to get AI-powered action items for {selectedDataset?.resolved_category || selectedDataset?.category || 'your'} data.
              </p>
              <button
                onClick={() => handleGenerate(false)}
                className="bg-primary hover:bg-primary/90 px-5 py-2.5 rounded-lg text-sm font-medium"
              >
                Generate Now
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
