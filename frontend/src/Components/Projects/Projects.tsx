import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { listDatasets, deleteDataset, getDatasetDashboard, appendToDataset } from '../../api.js'
import DashboardTemplate from '../Hero/DashboardTemplate/DashboardTemplate.js'
import { LoadingSkeleton } from '../ui/LoadingSkeleton'
import { LogoSpinner } from '../ui/LogoSpinner'
import toast from 'react-hot-toast'

type DatasetCard = {
  id: string
  created_at: string
  name?: string | null
  filename: string
  category: string | null
  status: string
  row_count: number | null
  column_count: number | null
}

const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'
const MAX_PROJECTS = 4
const TERMINAL_STATUS = new Set(['completed', 'failed'])

const CATEGORY_COLORS: Record<string, string> = {
  sales:        'bg-sales/12 text-sales border-sales/30',
  retail_sales: 'bg-sales/12 text-sales border-sales/30',
  marketing:    'bg-marketing/12 text-marketing border-marketing/30',
  hr:           'bg-hr/12 text-hr border-hr/30',
  operation:    'bg-operations/12 text-operations border-operations/30',
}
function categoryStyle(cat: string | null) {
  if (!cat) return 'bg-muted text-muted-foreground border-border'
  return CATEGORY_COLORS[cat.toLowerCase()] ?? 'bg-primary/12 text-primary border-primary/30'
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return iso }
}

// File-type icon colour based on filename extension
function FileIcon({ name }: { name: string }) {
  const ext = name.split('.').pop()?.toLowerCase()
  const color = ext === 'csv' ? 'text-sales' : ext === 'xlsx' || ext === 'xls' ? 'text-hr' : 'text-primary'
  const icon  = ext === 'csv' ? 'fa-file-csv' : ext === 'xlsx' || ext === 'xls' ? 'fa-file-excel' : 'fa-database'
  return (
    <div className={`w-11 h-11 rounded-xl bg-muted flex items-center justify-center ${color}`}>
      <i className={`fa-solid ${icon} text-lg`}></i>
    </div>
  )
}

const MAX_APPEND_BYTES = 50 * 1024 * 1024

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
}

export default function Projects() {
  const navigate = useNavigate()
  const { datasetId: routeDatasetId } = useParams<{ datasetId?: string }>()

  const [datasets, setDatasets]           = useState<DatasetCard[]>([])
  const [loading, setLoading]             = useState(true)
  const [deletingId, setDeletingId]       = useState<string | null>(null)
  const [confirmId, setConfirmId]         = useState<string | null>(null)
  const [dashboardData, setDashboardData] = useState<any>(null)
  const [loadingCard, setLoadingCard]     = useState<string | null>(null)
  const [showFailed, setShowFailed]       = useState(false)
  const [appendTargetId, setAppendTargetId] = useState<string | null>(null)
  const [appendFiles, setAppendFiles]       = useState<File[]>([])
  const [isAppending, setIsAppending]       = useState(false)
  const appendInputRef = useRef<HTMLInputElement>(null)
  // Previous per-dataset status, to detect processing → completed/failed transitions.
  const prevStatusRef = useRef<Record<string, string>>({})

  useEffect(() => { fetchDatasets() }, [])

  // URL carries the open project (/BI-Dashboard/:id) so refresh keeps the dashboard.
  useEffect(() => {
    if (!routeDatasetId) {
      setDashboardData(null)
      return
    }
    if (loading) return

    if (dashboardData?.dataset_id === routeDatasetId) return

    const ds = datasets.find((d) => d.id === routeDatasetId)
    if (ds && ds.status !== 'completed') {
      toast.error('This dataset is still processing.')
      navigate('/BI-Dashboard', { replace: true })
      return
    }

    let cancelled = false
    const load = async () => {
      setLoadingCard(routeDatasetId)
      try {
        localStorage.setItem(LAST_DATASET_ID_KEY, routeDatasetId)
        const res = await getDatasetDashboard(routeDatasetId)
        if (!cancelled) setDashboardData(res)
      } catch {
        if (!cancelled) {
          toast.error('Could not load dashboard.')
          navigate('/BI-Dashboard', { replace: true })
        }
      } finally {
        if (!cancelled) setLoadingCard(null)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [routeDatasetId, loading, datasets, navigate])

  const fetchDatasets = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res = await listDatasets()
      const cards: DatasetCard[] = res.datasets || []
      // Toast on any card that just transitioned into a terminal state.
      for (const c of cards) {
        const prev = prevStatusRef.current[c.id]
        if (prev && !TERMINAL_STATUS.has(prev) && TERMINAL_STATUS.has(c.status)) {
          const label = c.name || c.filename
          if (c.status === 'completed') toast.success(`"${label}" finished processing.`)
          else toast.error(`"${label}" failed to process.`)
        }
      }
      prevStatusRef.current = Object.fromEntries(cards.map(c => [c.id, c.status]))
      setDatasets(cards)
    } catch {
      if (!silent) toast.error('Could not load projects.')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  // Auto-poll while any card is still processing so status flips to
  // completed/failed on its own — no manual refresh needed (covers append
  // and page-refresh-mid-processing). listDatasets returns ≤4 lightweight
  // metadata cards, so a silent 4s poll is cheap.
  useEffect(() => {
    const hasPending = datasets.some(d => !TERMINAL_STATUS.has(d.status))
    if (!hasPending) return
    const id = setInterval(() => { void fetchDatasets(true) }, 4000)
    return () => clearInterval(id)
  }, [datasets])

  const handleCardClick = (ds: DatasetCard) => {
    if (ds.status !== 'completed') { toast.error('This dataset is still processing.'); return }
    navigate(`/BI-Dashboard/${ds.id}`)
  }

  const handleDelete = async () => {
    if (!confirmId) return
    const id = confirmId
    setConfirmId(null)
    setDeletingId(id)
    try {
      await deleteDataset(id)
      setDatasets(prev => prev.filter(d => d.id !== id))
      if (routeDatasetId === id) {
        setDashboardData(null)
        navigate('/BI-Dashboard', { replace: true })
      }
      toast.success('Project deleted.')
    } catch {
      toast.error('Failed to delete project.')
    } finally {
      setDeletingId(null)
    }
  }

  const handleAppendFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || [])
    const combined = picked.reduce((s, f) => s + f.size, 0)
    if (combined > MAX_APPEND_BYTES) {
      toast.error('Combined file size exceeds 50 MB.')
      return
    }
    setAppendFiles(picked)
    e.target.value = ''
  }

  const handleAppendConfirm = async () => {
    if (!appendTargetId || appendFiles.length === 0 || isAppending) return
    setIsAppending(true)
    try {
      const res = await appendToDataset(appendTargetId, appendFiles)
      const accepted = res?.accepted_files?.length ?? appendFiles.length
      const rejected = res?.rejected_files?.length ?? 0
      toast.success(
        rejected > 0
          ? `Appended ${accepted} file(s). ${rejected} file(s) skipped (schema mismatch). Re-processing started.`
          : `Appended ${accepted} file(s). Re-processing pipeline started.`
      )
      setAppendTargetId(null)
      setAppendFiles([])
      await fetchDatasets()
    } catch (err: any) {
      toast.error(err?.response?.data?.error || 'Failed to append data.')
    } finally {
      setIsAppending(false)
    }
  }

  const atLimit = datasets.length >= MAX_PROJECTS
  const activeDatasets = datasets.filter(d => d.status !== 'failed')
  const failedDatasets = datasets.filter(d => d.status === 'failed')

  // ── Loading dashboard from URL (refresh / direct link) ─────────────────────
  if (routeDatasetId && !dashboardData) {
    return (
      <div className='px-20 py-10 flex items-center justify-center min-h-[50vh]'>
        <LogoSpinner size={44} />
      </div>
    )
  }

  // ── Dashboard view ──────────────────────────────────────────────────────────
  if (dashboardData) {
    return (
      <div className='px-20 py-10'>
        <button
          onClick={() => { setDashboardData(null); navigate('/BI-Dashboard', { replace: true }) }}
          className='mb-6 flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors'
        >
          <i className='fa-solid fa-arrow-left'></i> Back to Projects
        </button>
        <DashboardTemplate data={dashboardData} />
      </div>
    )
  }

  // ── Projects grid ───────────────────────────────────────────────────────────
  return (
    <div className='min-h-screen'>

      {/* ── Delete confirmation modal ── */}
      {confirmId && (
        <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm'>
          <div className='bg-popover border border-border rounded-2xl p-7 max-w-sm w-full mx-4 shadow-2xl'>
            <div className='flex items-center gap-3 mb-4'>
              <div className='w-10 h-10 rounded-full bg-destructive/10 flex items-center justify-center shrink-0'>
                <i className='fa-solid fa-trash text-destructive'></i>
              </div>
              <h3 className='text-lg font-bold'>Delete Project?</h3>
            </div>
            <p className='text-muted-foreground text-sm mb-6'>
              This will permanently remove the dataset, all processed files, forecast history, and the dashboard. This action cannot be undone.
            </p>
            <div className='flex gap-3 justify-end'>
              <button
                onClick={() => setConfirmId(null)}
                className='px-4 py-2 text-sm font-medium rounded-xl border border-border text-foreground hover:bg-accent transition-colors'
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                className='px-4 py-2 text-sm font-medium rounded-xl bg-red-600 hover:bg-red-700 text-primary-foreground transition-colors'
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Append modal ── */}
      {appendTargetId && (
        <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm'>
          <div className='bg-popover border border-border rounded-2xl p-7 max-w-sm w-full mx-4 shadow-2xl'>
            <div className='flex items-center gap-3 mb-4'>
              <div className='w-10 h-10 rounded-full bg-primary/15 flex items-center justify-center shrink-0'>
                <i className='fa-solid fa-file-circle-plus text-primary'></i>
              </div>
              <h3 className='text-lg font-bold'>Add More Data</h3>
            </div>
            <p className='text-muted-foreground text-sm mb-4'>
              Select CSV/XLSX files with the same schema. Matching rows will be merged in; mismatched files will be skipped.
            </p>
            {appendFiles.length === 0 ? (
              <button
                onClick={() => appendInputRef.current?.click()}
                className='w-full py-8 rounded-xl border-2 border-dashed border-border hover:border-primary/50 text-muted-foreground hover:text-foreground transition-colors flex flex-col items-center gap-2 text-sm mb-4'
              >
                <i className='fa-solid fa-cloud-arrow-up text-2xl' />
                Click to browse files
              </button>
            ) : (
              <div className='mb-4 rounded-xl border border-border divide-y divide-border overflow-hidden'>
                {appendFiles.map((f, i) => (
                  <div key={i} className='flex items-center gap-3 px-3 py-2.5'>
                    <i className={`fa-solid ${f.name.endsWith('.csv') ? 'fa-file-csv text-sales' : 'fa-file-excel text-hr'} text-sm`} />
                    <span className='flex-1 text-xs text-foreground truncate'>{f.name}</span>
                    <span className='text-xs text-muted-foreground'>{formatFileSize(f.size)}</span>
                    <button onClick={() => setAppendFiles(prev => prev.filter((_, j) => j !== i))} className='text-muted-foreground hover:text-destructive transition-colors'>
                      <i className='fa-solid fa-xmark text-xs' />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <input ref={appendInputRef} type='file' accept='.csv,.xlsx,.xls' multiple onChange={handleAppendFileChange} className='hidden' />
            <div className='flex gap-3 justify-end'>
              <button
                onClick={() => { setAppendTargetId(null); setAppendFiles([]) }}
                disabled={isAppending}
                className='px-4 py-2 text-sm font-medium rounded-xl border border-border text-foreground hover:bg-accent transition-colors disabled:opacity-50'
              >
                Cancel
              </button>
              {appendFiles.length > 0 && (
                <button
                  onClick={handleAppendConfirm}
                  disabled={isAppending}
                  className='px-4 py-2 text-sm font-medium rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground transition-colors disabled:opacity-50 flex items-center gap-2'
                >
                  {isAppending ? <LogoSpinner size={14} /> : <i className='fa-solid fa-upload text-xs' />}
                  {isAppending ? 'Uploading…' : 'Append'}
                </button>
              )}
              {appendFiles.length === 0 && (
                <button
                  onClick={() => appendInputRef.current?.click()}
                  className='px-4 py-2 text-sm font-medium rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground transition-colors'
                >
                  Browse Files
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className='px-10 py-10'>

        {/* Header */}
        <div className='flex items-center justify-between mb-8'>
          <div>
            <h1 className='text-3xl font-extrabold'>Projects</h1>
            <p className='text-muted-foreground text-sm mt-1'>Click a project to open its dashboard</p>
          </div>
          <div className='flex items-center gap-4'>
            <span className='text-sm text-muted-foreground font-medium'>{datasets.length} / {MAX_PROJECTS}</span>
            <button
              onClick={() => navigate('/upload')}
              disabled={atLimit}
              className='flex items-center gap-2 bg-primary hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed text-primary-foreground text-sm font-semibold px-4 py-2.5 rounded-xl transition-colors'
              title={atLimit ? `Project limit reached (${MAX_PROJECTS})` : 'Upload a new dataset'}
            >
              <i className='fa-solid fa-plus text-xs'></i>
              New Project
            </button>
          </div>
        </div>

        {/* Limit banner */}
        {atLimit && (
          <div className='mb-6 flex items-center gap-2 bg-warning/10 border border-warning/30 text-warning text-sm font-medium px-4 py-3 rounded-xl'>
            <i className='fa-solid fa-triangle-exclamation'></i>
            You have reached the {MAX_PROJECTS}-project limit. Delete a project to upload a new file.
          </div>
        )}

        {/* Loading skeleton */}
        {loading ? (
          <div className='grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'>
            <LoadingSkeleton variant="card" count={4} />
          </div>
        ) : activeDatasets.length === 0 && failedDatasets.length === 0 ? (
          /* Empty state */
          <div className='flex flex-col items-center justify-center py-24 border border-dashed border-border rounded-2xl text-center'>
            <div className='w-16 h-16 rounded-2xl bg-muted flex items-center justify-center mb-4'>
              <i className='fa-solid fa-folder-open text-2xl text-muted-foreground'></i>
            </div>
            <h3 className='text-foreground font-semibold mb-1'>No projects yet</h3>
            <p className='text-muted-foreground text-sm mb-6 max-w-xs'>Upload a CSV or Excel file to create your first AI-powered dashboard</p>
            <button
              onClick={() => navigate('/upload')}
              className='flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-semibold px-5 py-2.5 rounded-xl transition-colors'
            >
              <i className='fa-solid fa-upload text-xs'></i>
              Upload Dataset
            </button>
          </div>
        ) : (
          <>
          {/* Cards grid — active datasets only */}
          {activeDatasets.length > 0 && (
          <div className='grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'>
            {activeDatasets.map(ds => (
              <div
                key={ds.id}
                onClick={() => handleCardClick(ds)}
                className={`
                  relative group bg-card border border-border
                  hover:border-primary/60 hover:shadow-lg hover:shadow-primary/10
                  rounded-2xl p-5 flex flex-col gap-3 cursor-pointer
                  transition-all duration-200 hover:-translate-y-0.5
                  ${(deletingId === ds.id || loadingCard === ds.id) ? 'opacity-50 pointer-events-none' : ''}
                `}
              >
                {/* Spinner overlay while loading dashboard */}
                {loadingCard === ds.id && (
                  <div className='absolute inset-0 flex items-center justify-center rounded-2xl bg-black/50 backdrop-blur-sm z-20'>
                    <LogoSpinner size={44} />
                  </div>
                )}

                {/* Action buttons — appear on hover */}
                <div className='absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-all duration-150 z-10'>
                  {ds.status === 'completed' && (
                    <button
                      onClick={e => { e.stopPropagation(); setAppendTargetId(ds.id); setAppendFiles([]) }}
                      className='w-7 h-7 rounded-lg bg-transparent hover:bg-primary/15 text-muted-foreground hover:text-primary flex items-center justify-center transition-all duration-150'
                      title='Add more data'
                    >
                      <i className='fa-solid fa-file-circle-plus text-xs'></i>
                    </button>
                  )}
                  <button
                    onClick={e => { e.stopPropagation(); setConfirmId(ds.id) }}
                    className='w-7 h-7 rounded-lg bg-transparent hover:bg-destructive/10 text-muted-foreground hover:text-destructive flex items-center justify-center transition-all duration-150'
                    title='Delete project'
                  >
                    {deletingId === ds.id
                      ? <LogoSpinner size={14} />
                      : <i className='fa-solid fa-trash text-xs'></i>}
                  </button>
                </div>

                {/* Icon */}
                <FileIcon name={ds.filename} />

                {/* Name + date */}
                <div className='flex-1'>
                  <p className='font-semibold text-sm text-foreground leading-snug line-clamp-2 pr-5' title={ds.name || ds.filename}>
                    {ds.name || ds.filename}
                  </p>
                  {ds.name && (
                    <p className='text-[11px] text-muted-foreground mt-0.5 truncate pr-5' title={ds.filename}>
                      <i className='fa-solid fa-file-lines opacity-60 mr-1'></i>{ds.filename}
                    </p>
                  )}
                  <p className='text-xs text-muted-foreground mt-1'>{formatDate(ds.created_at)}</p>
                </div>

                {/* Row / col stats */}
                <div className='flex gap-3 text-xs text-muted-foreground'>
                  {ds.row_count != null && (
                    <span className='flex items-center gap-1'>
                      <i className='fa-solid fa-list-ol opacity-60'></i>
                      {ds.row_count.toLocaleString()} rows
                    </span>
                  )}
                  {ds.column_count != null && (
                    <span className='flex items-center gap-1'>
                      <i className='fa-solid fa-table-columns opacity-60'></i>
                      {ds.column_count} cols
                    </span>
                  )}
                </div>

                {/* Footer: category badge + status */}
                <div className='flex items-center justify-between pt-2 border-t border-border'>
                  {ds.category
                    ? <span className={`text-xs font-medium px-2.5 py-0.5 rounded-full border ${categoryStyle(ds.category)}`}>{ds.category}</span>
                    : <span />}
                  <span className={`text-xs font-medium flex items-center gap-1 ${ds.status === 'completed' ? 'text-success' : ds.status === 'failed' ? 'text-destructive' : 'text-warning'}`}>
                    {ds.status === 'completed'
                      ? <i className='fa-solid text-[9px] fa-circle-check'></i>
                      : ds.status === 'failed'
                        ? <i className='fa-solid text-[9px] fa-circle-xmark'></i>
                        : <LogoSpinner size={12} />}
                    {ds.status}
                  </span>
                </div>
              </div>
            ))}

            {/* "Add new" placeholder card — only shown when under limit */}
            {!atLimit && (
              <div
                onClick={() => navigate('/upload')}
                className='bg-transparent border-2 border-dashed border-border hover:border-primary/50 rounded-2xl p-5 flex flex-col items-center justify-center gap-2 cursor-pointer transition-all duration-200 hover:-translate-y-0.5 min-h-[180px]'
              >
                <div className='w-10 h-10 rounded-xl bg-muted flex items-center justify-center'>
                  <i className='fa-solid fa-plus text-primary'></i>
                </div>
                <p className='text-sm text-muted-foreground font-medium'>Upload new dataset</p>
              </div>
            )}
          </div>
          )}

          {/* Failed uploads — collapsible section */}
          {failedDatasets.length > 0 && (
            <div className='mt-8'>
              <button
                onClick={() => setShowFailed(prev => !prev)}
                className='flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors mb-3'
              >
                <i className={`fa-solid fa-chevron-${showFailed ? 'up' : 'down'} text-xs`} />
                <span>Failed Uploads ({failedDatasets.length})</span>
              </button>
              {showFailed && (
                <div className='space-y-2'>
                  {failedDatasets.map(ds => (
                    <div key={ds.id} className='flex items-center gap-4 px-4 py-3 bg-card border border-border rounded-xl'>
                      <div className='w-8 h-8 rounded-lg bg-destructive/10 flex items-center justify-center'>
                        <i className='fa-solid fa-circle-xmark text-destructive text-xs' />
                      </div>
                      <div className='flex-1 min-w-0'>
                        <p className='text-sm font-medium text-foreground truncate'>{ds.name || ds.filename}</p>
                        <p className='text-xs text-muted-foreground'>{formatDate(ds.created_at)}</p>
                      </div>
                      {ds.category && (
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${categoryStyle(ds.category)}`}>{ds.category}</span>
                      )}
                      <button
                        onClick={() => setConfirmId(ds.id)}
                        className='w-7 h-7 rounded-lg hover:bg-destructive/10 text-muted-foreground hover:text-destructive flex items-center justify-center transition-all'
                        title='Delete'
                      >
                        {deletingId === ds.id
                          ? <LogoSpinner size={14} />
                          : <i className='fa-solid fa-trash text-xs' />}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          </>
        )}
      </div>
    </div>
  )
}
