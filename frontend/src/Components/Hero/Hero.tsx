import { useRef, useState, useCallback, type ChangeEvent, type DragEvent } from 'react'
import { uploadFile, checkJobStatus } from '../../api.js'
import DashboardTemplate from './DashboardTemplate/DashboardTemplate.js'
import { LogoSpinner } from '../ui/LogoSpinner'
import toast from 'react-hot-toast'

type ProgressType = {
  current_step: number
  total_steps?: number
  progress_percent?: number
  progress_message: string
  status: string
  error_log?: string
  dataset_id?: string
}

const DEFAULT_TOTAL_STEPS = 8
const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'
const MAX_UPLOAD_MB = 50
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

const STEP_LABELS: Record<number, string> = {
  1: 'Uploading file...',
  2: 'Queuing for processing...',
  3: 'Cleaning & preparing data...',
  4: 'Profiling columns...',
  5: 'AI semantic analysis...',
  6: 'Smart preprocessing...',
  7: 'Generating dashboard blueprint...',
  8: 'Writing business report...',
}

const DEPARTMENTS = [
  { value: 'sales', label: 'Sales', icon: 'fa-solid fa-chart-line', color: 'border-sales/40 bg-sales/10 hover:border-sales' },
  { value: 'marketing', label: 'Marketing', icon: 'fa-solid fa-bullhorn', color: 'border-marketing/40 bg-marketing/10 hover:border-marketing' },
  { value: 'operation', label: 'Operations', icon: 'fa-solid fa-gears', color: 'border-operations/40 bg-operations/10 hover:border-operations' },
  { value: 'hr', label: 'HR', icon: 'fa-solid fa-users', color: 'border-hr/40 bg-hr/10 hover:border-hr' },
]

const AVG_STEP_DURATION_S = 8

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
}

function getProgressPercent(progress: ProgressType | null): number {
  if (!progress) return 0
  if (progress.status === 'completed') return 100
  if (typeof progress.progress_percent === 'number') {
    const p = Math.max(0, Math.min(100, Math.round(progress.progress_percent)))
    return p >= 100 && progress.status !== 'completed' ? 99 : p
  }
  const total = progress.total_steps && progress.total_steps > 0 ? progress.total_steps : DEFAULT_TOTAL_STEPS
  const current = typeof progress.current_step === 'number' ? progress.current_step : 1
  const p = Math.max(0, Math.min(100, Math.round((current / total) * 100)))
  return p >= 100 && progress.status !== 'completed' ? 99 : p
}

export default function Hero() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const extraFilesInputRef = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [extraFiles, setExtraFiles] = useState<File[]>([])
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [customCategory, setCustomCategory] = useState('')
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [charts, setCharts] = useState(false)
  const [data, setData] = useState<any>(null)
  const [progress, setProgress] = useState<ProgressType | null>(null)
  const [startTime, setStartTime] = useState<number | null>(null)

  const validateFile = useCallback((file: File): boolean => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!ext || !['csv', 'xlsx', 'xls'].includes(ext)) {
      toast.error('Unsupported file format. Please upload CSV or XLSX.')
      return false
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(`File is too large. Maximum allowed size is ${MAX_UPLOAD_MB}MB.`)
      return false
    }
    return true
  }, [])

  const allFiles = (): File[] => (selectedFile ? [selectedFile, ...extraFiles] : [])

  const totalFilesSize = (): number => allFiles().reduce((acc, f) => acc + f.size, 0)

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || [])
    if (picked.length === 0) return
    if (selectedFile) {
      // Adding more files to existing selection
      const combined = totalFilesSize() + picked.reduce((s, f) => s + f.size, 0)
      if (combined > MAX_UPLOAD_BYTES) {
        toast.error(`Combined file size exceeds ${MAX_UPLOAD_MB} MB limit.`)
        return
      }
      const valid = picked.filter(validateFile)
      setExtraFiles((prev) => [...prev, ...valid])
    } else {
      // Initial selection
      const [first, ...rest] = picked
      if (!validateFile(first)) {
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
      setSelectedFile(first)
      setExtraFiles(rest.filter(validateFile))
      if (first) setStep(2)
    }
    e.target.value = ''
  }

  const handleDragOver = (e: DragEvent) => { e.preventDefault(); setIsDragOver(true) }
  const handleDragLeave = (e: DragEvent) => { e.preventDefault(); setIsDragOver(false) }
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const picked = Array.from(e.dataTransfer.files || [])
    if (picked.length === 0) return
    const [first, ...rest] = picked
    if (!validateFile(first)) return
    setSelectedFile(first)
    setExtraFiles(rest.filter(validateFile))
    setStep(2)
  }

  const handleBrowse = () => fileInputRef.current?.click()

  const getCategory = (): string | null => {
    if (selectedCategory === '__custom') return customCategory.trim() || null
    return selectedCategory
  }

  const handleStart = async () => {
    const category = getCategory()
    if (!selectedFile || !category || isUploading) return
    const filesToUpload = allFiles()

    setStep(4)
    setIsUploading(true)
    setStartTime(Date.now())
    setProgress({
      current_step: 1,
      total_steps: DEFAULT_TOTAL_STEPS,
      progress_percent: Math.round((1 / DEFAULT_TOTAL_STEPS) * 100),
      progress_message: 'Uploading file...',
      status: 'pending',
    })

    try {
      const res = await uploadFile(filesToUpload, category)
      if (res?.dataset_id) localStorage.setItem(LAST_DATASET_ID_KEY, String(res.dataset_id))

      if (res?.job_id) {
        const jobId = String(res.job_id)
        localStorage.setItem('bi_dashboard_last_job_id', jobId)
        const interval = setInterval(async () => {
          try {
            const finalRes = await checkJobStatus(jobId)
            if (!finalRes) return
            setData(finalRes)
            setProgress(finalRes)
            if (finalRes?.dataset_id) localStorage.setItem(LAST_DATASET_ID_KEY, String(finalRes.dataset_id))
            if (finalRes.status === 'completed') {
              setIsUploading(false)
              clearInterval(interval)
              setCharts(true)
            }
            if (finalRes.status === 'failed') {
              setIsUploading(false)
              clearInterval(interval)
              toast.error(finalRes.error_log || 'Processing failed.')
            }
          } catch (err: any) {
            const msg = err?.response?.data?.error || err?.message
            if (msg) toast.error(msg)
          }
        }, 2000)
      } else {
        setIsUploading(false)
        toast.error('Upload did not return a valid job ID.')
        setStep(3)
      }
    } catch (err: any) {
      const message = err?.response?.data?.message || err?.response?.data?.error || err?.message || 'Upload failed.'
      toast.error(message)
      setIsUploading(false)
      setStep(3)
    }
  }

  const progressPercent = getProgressPercent(progress)
  const currentStepNum = progress?.current_step ?? 1
  const currentLabel = STEP_LABELS[currentStepNum] ?? `Processing step ${currentStepNum}...`
  const elapsedS = startTime ? Math.round((Date.now() - startTime) / 1000) : 0
  const estimatedTotal = DEFAULT_TOTAL_STEPS * AVG_STEP_DURATION_S
  const estimatedRemaining = Math.max(0, estimatedTotal - elapsedS)

  if (charts) {
    return (
      <div className="px-20 py-10">
        <DashboardTemplate data={data} />
      </div>
    )
  }

  return (
    <div className="px-6 md:px-20 py-10 min-h-screen flex flex-col">
      {/* Step indicator */}
      <div className="flex items-center justify-center gap-2 mb-10">
        {[1, 2, 3, 4].map((s) => (
          <div key={s} className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all duration-300 ${
              step >= s ? 'bg-primary text-primary-foreground scale-110' : 'bg-muted text-muted-foreground'
            }`}>
              {step > s ? <i className="fa-solid fa-check text-xs" /> : s}
            </div>
            {s < 4 && <div className={`w-12 h-0.5 transition-colors duration-300 ${step > s ? 'bg-primary' : 'bg-muted'}`} />}
          </div>
        ))}
      </div>

      {/* Step 1: Upload */}
      {step === 1 && (
        <div className="flex-1 flex flex-col items-center justify-center max-w-2xl mx-auto w-full">
          <h1 className="text-3xl font-extrabold mb-2 text-center">Upload Your Data</h1>
          <p className="text-muted-foreground text-sm mb-8 text-center">Drag & drop or browse to upload your dataset</p>

          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={handleBrowse}
            className={`w-full cursor-pointer rounded-3xl border-2 border-dashed p-16 flex flex-col items-center justify-center transition-all duration-300 ${
              isDragOver
                ? 'border-primary bg-primary/10 scale-[1.02]'
                : 'border-border bg-card hover:border-primary/50 hover:bg-accent'
            }`}
          >
            <div className={`w-20 h-20 rounded-full flex items-center justify-center mb-5 transition-all duration-300 ${
              isDragOver ? 'bg-primary/15' : 'bg-muted'
            }`}>
              <i className={`fa-solid fa-cloud-arrow-up text-3xl transition-colors ${isDragOver ? 'text-primary' : 'text-muted-foreground'}`} />
            </div>
            {isDragOver ? (
              <p className="text-lg font-bold text-primary">Drop your file here</p>
            ) : (
              <>
                <p className="text-lg font-bold mb-1">Drag & drop your file here</p>
                <p className="text-muted-foreground text-sm mb-4">or click to browse</p>
                <div className="flex gap-2">
                  <span className="text-xs px-3 py-1 rounded-full bg-success/10 text-success border border-success/30">.csv</span>
                  <span className="text-xs px-3 py-1 rounded-full bg-hr/10 text-hr border border-hr/30">.xlsx</span>
                  <span className="text-xs px-3 py-1 rounded-full bg-muted text-muted-foreground border border-border">Max 50MB</span>
                </div>
              </>
            )}
          </div>
          <input ref={fileInputRef} onChange={handleFileChange} type="file" accept=".csv,.xlsx,.xls" multiple className="hidden" />
        </div>
      )}

      {/* Step 2: Department */}
      {step === 2 && (
        <div className="flex-1 flex flex-col items-center max-w-2xl mx-auto w-full">
          <h1 className="text-3xl font-extrabold mb-2 text-center">Select Department</h1>
          <p className="text-muted-foreground text-sm mb-8 text-center">This helps AI suggest relevant dashboard templates</p>

          {/* File preview */}
          <div className="w-full mb-4 rounded-2xl bg-card border border-border divide-y divide-border overflow-hidden">
            {[selectedFile, ...extraFiles].filter(Boolean).map((f, idx) => (
              <div key={idx} className="p-4 flex items-center gap-4">
                <div className="w-10 h-10 rounded-xl bg-muted flex items-center justify-center flex-shrink-0">
                  <i className={`fa-solid ${f!.name.endsWith('.csv') ? 'fa-file-csv text-success' : 'fa-file-excel text-hr'}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold truncate">{f!.name}</p>
                  <p className="text-xs text-muted-foreground">{formatFileSize(f!.size)}</p>
                </div>
                <button
                  onClick={() => {
                    if (idx === 0) {
                      if (extraFiles.length > 0) {
                        setSelectedFile(extraFiles[0])
                        setExtraFiles(extraFiles.slice(1))
                      } else {
                        setSelectedFile(null)
                        setStep(1)
                      }
                    } else {
                      setExtraFiles((prev) => prev.filter((_, i) => i !== idx - 1))
                    }
                  }}
                  className="text-muted-foreground hover:text-destructive transition-colors flex-shrink-0"
                >
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            ))}
          </div>
          <div className="w-full mb-6 flex items-center justify-between">
            <p className="text-xs text-muted-foreground">{allFiles().length} file{allFiles().length !== 1 ? 's' : ''} · {formatFileSize(totalFilesSize())} total</p>
            <button
              onClick={() => extraFilesInputRef.current?.click()}
              className="text-xs text-primary hover:text-primary transition-colors flex items-center gap-1"
            >
              <i className="fa-solid fa-plus text-[10px]" /> Add more files
            </button>
          </div>
          <input ref={extraFilesInputRef} onChange={handleFileChange} type="file" accept=".csv,.xlsx,.xls" multiple className="hidden" />

          {/* Department cards */}
          <div className="w-full grid grid-cols-2 gap-4 mb-6">
            {DEPARTMENTS.map((dept) => (
              <button
                key={dept.value}
                onClick={() => { setSelectedCategory(dept.value); setStep(3) }}
                className={`p-5 rounded-2xl border-2 text-left transition-all duration-200 hover:scale-[1.02] ${
                  selectedCategory === dept.value
                    ? 'border-primary bg-primary/10'
                    : dept.color
                }`}
              >
                <i className={`${dept.icon} text-xl mb-2 block`} />
                <p className="font-semibold">{dept.label}</p>
              </button>
            ))}
          </div>

          {/* Custom category */}
          <div className="w-full">
            <button
              onClick={() => setSelectedCategory('__custom')}
              className={`w-full p-4 rounded-2xl border-2 border-dashed text-left transition-all ${
                selectedCategory === '__custom' ? 'border-primary bg-primary/10' : 'border-border hover:border-primary/50'
              }`}
            >
              <div className="flex items-center gap-3">
                <i className="fa-solid fa-plus text-primary" />
                <span className="text-sm text-foreground">Custom department</span>
              </div>
            </button>
            {selectedCategory === '__custom' && (
              <div className="mt-3 flex gap-3">
                <input
                  type="text"
                  value={customCategory}
                  onChange={(e) => setCustomCategory(e.target.value)}
                  placeholder="Enter department name..."
                  className="flex-1 px-4 py-2.5 rounded-xl bg-card border border-border text-sm text-foreground placeholder-gray-500 focus:outline-none focus:border-primary"
                />
                <button
                  onClick={() => { if (customCategory.trim()) setStep(3) }}
                  disabled={!customCategory.trim()}
                  className="px-5 py-2.5 rounded-xl bg-primary hover:bg-primary/90 text-sm font-semibold text-primary-foreground disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Step 3: Confirm */}
      {step === 3 && (
        <div className="flex-1 flex flex-col items-center max-w-lg mx-auto w-full">
          <h1 className="text-3xl font-extrabold mb-2 text-center">Ready to Analyze</h1>
          <p className="text-muted-foreground text-sm mb-8 text-center">Review your selections and start the AI pipeline</p>

          <div className="w-full p-6 rounded-2xl bg-card border border-border space-y-5 mb-8">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center">
                <i className={`fa-solid ${allFiles().length > 1 ? 'fa-copy text-primary' : selectedFile?.name.endsWith('.csv') ? 'fa-file-csv text-success' : 'fa-file-excel text-hr'} text-xl`} />
              </div>
              <div>
                {allFiles().length > 1 ? (
                  <>
                    <p className="font-semibold text-sm">{allFiles().length} files selected</p>
                    <p className="text-xs text-muted-foreground">{formatFileSize(totalFilesSize())} combined</p>
                  </>
                ) : (
                  <>
                    <p className="font-semibold text-sm">{selectedFile?.name}</p>
                    <p className="text-xs text-muted-foreground">{selectedFile ? formatFileSize(selectedFile.size) : ''}</p>
                  </>
                )}
              </div>
            </div>
            <div className="h-px bg-muted" />
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center">
                <i className="fa-solid fa-building text-primary text-xl" />
              </div>
              <div>
                <p className="font-semibold text-sm capitalize">{getCategory()}</p>
                <p className="text-xs text-muted-foreground">Target department</p>
              </div>
            </div>
          </div>

          <button
            onClick={handleStart}
            className="w-full py-4 rounded-2xl bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-lg transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <i className="fa-solid fa-wand-magic-sparkles mr-2" />
            Start Analysis
          </button>

          <button
            onClick={() => setStep(2)}
            className="mt-4 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <i className="fa-solid fa-arrow-left mr-1" /> Go back
          </button>
        </div>
      )}

      {/* Step 4: Processing */}
      {step === 4 && (
        <div className="flex-1 flex flex-col items-center justify-center max-w-md mx-auto w-full gap-0">
          {/* Branded logo loader + progress */}
          <div className="w-28 mb-5 flex flex-col items-center justify-center gap-2">
            <LogoSpinner size={84} />
            <span className="text-2xl font-bold text-foreground">{progressPercent}%</span>
          </div>

          {/* Progress bar */}
          <div className="w-full max-w-xs h-1 bg-muted rounded-full overflow-hidden mb-3">
            <div
              className="h-full bg-gradient-to-r from-[#5A5AF6] to-[#8B5CF6] rounded-full transition-all duration-700 ease-out will-change-transform"
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Current stage label */}
          <div className="text-center mb-4">
            <p className="text-base font-semibold text-foreground" style={{ animation: 'pulse-opacity 2s infinite' }}>{currentLabel}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {progress?.status === 'failed'
                ? 'Processing failed'
                : estimatedRemaining > 0
                  ? `~${estimatedRemaining}s remaining`
                  : 'Almost done...'
              }
            </p>
          </div>

          {/* Step progress dots */}
          <div className="flex gap-1.5 mb-5">
            {Array.from({ length: DEFAULT_TOTAL_STEPS }, (_, i) => (
              <div
                key={i}
                className={`w-2 h-2 rounded-full transition-all duration-300 ${
                  i + 1 < currentStepNum ? 'bg-primary'
                  : i + 1 === currentStepNum ? 'bg-primary scale-125'
                  : 'bg-muted'
                }`}
                style={i + 1 === currentStepNum ? { animation: 'pulse-opacity 1.2s infinite' } : undefined}
              />
            ))}
          </div>

          {/* Pipeline stages list */}
          <div className="w-full max-h-44 overflow-y-auto no-scrollbar">
            {Object.entries(STEP_LABELS).map(([num, label]) => {
              const n = Number(num)
              const isActive = n === currentStepNum
              const isDone = n < currentStepNum
              const stateKey = isDone ? 'd' : isActive ? 'a' : 'p'
              return (
                <div
                  key={`${num}-${stateKey}`}
                  className="flex items-center gap-2 px-3 rounded-lg text-xs overflow-hidden"
                  style={{
                    border: `1px solid ${isActive ? 'rgba(90,90,246,0.22)' : 'transparent'}`,
                    backgroundColor: isActive ? 'rgba(90,90,246,0.09)' : 'transparent',
                    opacity: !isActive && !isDone ? 0.28 : undefined,
                    marginBottom: isDone ? undefined : '4px',
                    animation: isActive
                      ? 'step-enter 0.45s cubic-bezier(0.22,1,0.36,1) both'
                      : isDone
                      ? 'step-complete 0.6s ease both'
                      : undefined,
                    paddingTop: isDone ? undefined : '0.375rem',
                    paddingBottom: isDone ? undefined : '0.375rem',
                  }}
                >
                  {isDone ? (
                    <i className="fa-solid fa-circle-check text-success text-[10px]" />
                  ) : isActive ? (
                    <LogoSpinner size={12} className="shrink-0" />
                  ) : (
                    <div className="w-2.5 h-2.5 rounded-full bg-muted shrink-0" />
                  )}
                  <span className={isActive ? 'text-foreground font-medium' : 'text-muted-foreground'}>{label}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
