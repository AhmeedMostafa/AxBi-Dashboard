import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { listDatasets } from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'

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
const PROJECT_NAME_KEY = 'axbi_project_name'

function projectName(ds: DatasetCard): string {
  return ds.name?.trim() || ds.filename?.replace(/\.[^.]+$/, '') || ds.filename || 'Untitled'
}

export default function ProjectSelector() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [datasets, setDatasets] = useState<DatasetCard[]>([])
  const [loading, setLoading] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(() => localStorage.getItem(LAST_DATASET_ID_KEY))
  const ref = useRef<HTMLDivElement>(null)

  const fetchDatasets = async () => {
    setLoading(true)
    try {
      const res = await listDatasets()
      setDatasets(res?.datasets || [])
    } catch {
      /* silent — selector just stays empty */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchDatasets() }, [])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const active = datasets.find(d => d.id === activeId) || null
  const label = active ? projectName(active) : (localStorage.getItem(PROJECT_NAME_KEY) || 'Select project')

  const handleSelect = (ds: DatasetCard) => {
    if (ds.status !== 'completed') {
      toast.error('This project is still processing.')
      return
    }
    localStorage.setItem(LAST_DATASET_ID_KEY, ds.id)
    localStorage.setItem(PROJECT_NAME_KEY, projectName(ds))
    setActiveId(ds.id)
    setOpen(false)
    toast.success(`Switched to ${projectName(ds)}`)
    navigate('/agent')
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen(o => !o); if (!open) fetchDatasets() }}
        className="flex items-center gap-2 max-w-[200px] px-3 py-1.5 rounded-lg border border-border bg-card hover:border-primary/50 transition-colors text-sm"
        title="Switch project"
      >
        <i className="fa-solid fa-folder text-primary text-xs shrink-0" />
        <span className="truncate text-foreground font-medium">{label}</span>
        <i className={`fa-solid fa-chevron-down text-[10px] text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-2 z-50 w-72 bg-popover border border-border rounded-xl shadow-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Your projects</span>
            <button onClick={fetchDatasets} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
              <i className="fa-solid fa-arrows-rotate" />
            </button>
          </div>

          <div className="max-h-72 overflow-y-auto py-1">
            {loading ? (
              <div className="flex items-center justify-center py-6">
                <LogoSpinner size={28} />
              </div>
            ) : datasets.length === 0 ? (
              <div className="text-center py-6 px-4">
                <i className="fa-solid fa-folder-open text-xl text-muted-foreground mb-1" />
                <p className="text-xs text-muted-foreground">No projects yet</p>
              </div>
            ) : (
              datasets.map(ds => {
                const isActive = ds.id === activeId
                const isCompleted = ds.status === 'completed'
                return (
                  <button
                    key={ds.id}
                    onClick={() => handleSelect(ds)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                      isActive ? 'bg-primary/10' : 'hover:bg-accent'
                    }`}
                  >
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${isActive ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground'}`}>
                      <i className={`fa-solid ${ds.filename?.endsWith('.csv') ? 'fa-file-csv' : ds.filename?.match(/\.xlsx?$/) ? 'fa-file-excel' : 'fa-database'} text-xs`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground font-medium truncate">{projectName(ds)}</p>
                      <p className="text-[10px] text-muted-foreground truncate">
                        {ds.category ? <span className="capitalize">{ds.category}</span> : 'Uncategorized'}
                        {ds.row_count != null && ` · ${ds.row_count.toLocaleString()} rows`}
                      </p>
                    </div>
                    {isActive ? (
                      <i className="fa-solid fa-check text-primary text-xs shrink-0" />
                    ) : !isCompleted ? (
                      <span className={`text-[9px] font-medium shrink-0 ${ds.status === 'failed' ? 'text-destructive' : 'text-warning'}`}>
                        {ds.status === 'failed' ? <i className="fa-solid fa-circle-xmark" /> : <LogoSpinner size={12} />}
                      </span>
                    ) : null}
                  </button>
                )
              })
            )}
          </div>

          <button
            onClick={() => { setOpen(false); navigate('/upload') }}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-sm text-primary hover:bg-accent border-t border-border transition-colors"
          >
            <i className="fa-solid fa-plus text-xs" />
            New project
          </button>
        </div>
      )}
    </div>
  )
}
