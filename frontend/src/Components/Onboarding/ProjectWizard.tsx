import { useRef, useState, useCallback, type ChangeEvent, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadFile, checkJobStatus } from '../../api.js'
import toast from 'react-hot-toast'
import AxBiLogo from '../ui/AxBiLogo'
import { LogoSpinner } from '../ui/LogoSpinner'

type ProgressType = {
  current_step: number
  total_steps?: number
  progress_percent?: number
  progress_message: string
  status: string
  error_log?: string
  dataset_id?: string
}

type WizardStep = 'name' | 'upload' | 'department' | 'confirm' | 'processing' | 'ready'

const DEFAULT_TOTAL_STEPS = 8
const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'
const PROJECT_NAME_KEY = 'axbi_project_name'
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

// The 5 visible milestones on the progress rail.
const RAIL: { key: WizardStep; label: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'upload', label: 'Upload' },
  { key: 'department', label: 'Department' },
  { key: 'confirm', label: 'Analyze' },
  { key: 'ready', label: 'Done' },
]
const railIndex = (s: WizardStep): number => {
  if (s === 'processing') return 3 // shown as part of "Analyze"
  return RAIL.findIndex(r => r.key === s)
}

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

export default function ProjectWizard() {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const extraFilesInputRef = useRef<HTMLInputElement>(null)

  const [step, setStep] = useState<WizardStep>('name')
  const [projectName, setProjectName] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [extraFiles, setExtraFiles] = useState<File[]>([])
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [customCategory, setCustomCategory] = useState('')
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
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
      const combined = totalFilesSize() + picked.reduce((s, f) => s + f.size, 0)
      if (combined > MAX_UPLOAD_BYTES) {
        toast.error(`Combined file size exceeds ${MAX_UPLOAD_MB} MB limit.`)
        return
      }
      const valid = picked.filter(validateFile)
      setExtraFiles((prev) => [...prev, ...valid])
    } else {
      const [first, ...rest] = picked
      if (!validateFile(first)) {
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
      setSelectedFile(first)
      setExtraFiles(rest.filter(validateFile))
      if (first) setStep('department')
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
    setStep('department')
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

    // Persist the project name so downstream pages (agent greeting, conversation
    // titles) can reference it. Default to the primary filename if left blank.
    const finalName = projectName.trim() || selectedFile.name.replace(/\.[^.]+$/, '')
    localStorage.setItem(PROJECT_NAME_KEY, finalName)

    setStep('processing')
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
      const res = await uploadFile(filesToUpload, category, finalName)
      if (res?.dataset_id) localStorage.setItem(LAST_DATASET_ID_KEY, String(res.dataset_id))

      if (res?.job_id) {
        const jobId = String(res.job_id)
        localStorage.setItem('bi_dashboard_last_job_id', jobId)
        const interval = setInterval(async () => {
          try {
            const finalRes = await checkJobStatus(jobId)
            if (!finalRes) return
            setProgress(finalRes)
            if (finalRes?.dataset_id) localStorage.setItem(LAST_DATASET_ID_KEY, String(finalRes.dataset_id))
            if (finalRes.status === 'completed') {
              setIsUploading(false)
              clearInterval(interval)
              setStep('ready')
            }
            if (finalRes.status === 'failed') {
              setIsUploading(false)
              clearInterval(interval)
              toast.error(finalRes.error_log || 'Processing failed.')
              setStep('confirm')
            }
          } catch (err: any) {
            const msg = err?.response?.data?.error || err?.message
            if (msg) toast.error(msg)
          }
        }, 2000)
      } else {
        setIsUploading(false)
        toast.error('Upload did not return a valid job ID.')
        setStep('confirm')
      }
    } catch (err: any) {
      const message = err?.response?.data?.message || err?.response?.data?.error || err?.message || 'Upload failed.'
      toast.error(message)
      setIsUploading(false)
      setStep('confirm')
    }
  }

  const progressPercent = getProgressPercent(progress)
  const currentStepNum = progress?.current_step ?? 1
  const currentLabel = STEP_LABELS[currentStepNum] ?? `Processing step ${currentStepNum}...`
  const elapsedS = startTime ? Math.round((Date.now() - startTime) / 1000) : 0
  const estimatedTotal = DEFAULT_TOTAL_STEPS * AVG_STEP_DURATION_S
  const estimatedRemaining = Math.max(0, estimatedTotal - elapsedS)

  const activeRail = railIndex(step)

  return (
    <div className="min-h-screen flex flex-col bg-background">
      {/* Branded header */}
      <header className="flex items-center gap-3 px-6 md:px-10 py-4 border-b border-border">
        <AxBiLogo className="h-7" />
        <span className="text-sm text-muted-foreground border-l border-border pl-3">New Project</span>
      </header>

      <div className="flex-1 px-6 md:px-20 py-10 flex flex-col">
        {/* Progress rail */}
        {step !== 'ready' && (
          <div className="flex items-center justify-center gap-2 mb-10">
            {RAIL.map((r, i) => (
              <div key={r.key} className="flex items-center gap-2">
                <div className="flex flex-col items-center gap-1">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all duration-300 ${
                    activeRail >= i ? 'bg-primary text-primary-foreground scale-110' : 'bg-muted text-muted-foreground'
                  }`}>
                    {activeRail > i ? <i className="fa-solid fa-check text-xs" /> : i + 1}
                  </div>
                  <span className={`text-[10px] hidden sm:block ${activeRail >= i ? 'text-foreground font-medium' : 'text-muted-foreground'}`}>{r.label}</span>
                </div>
                {i < RAIL.length - 1 && <div className={`w-10 md:w-12 h-0.5 mb-4 transition-colors duration-300 ${activeRail > i ? 'bg-primary' : 'bg-muted'}`} />}
              </div>
            ))}
          </div>
        )}

        {/* Step: Name */}
        {step === 'name' && (
          <div className="flex-1 flex flex-col items-center justify-center max-w-xl mx-auto w-full">
            <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-5">
              <i className="fa-solid fa-folder-plus text-2xl text-primary" />
            </div>
            <h1 className="text-3xl font-extrabold mb-2 text-center">Create your project</h1>
            <p className="text-muted-foreground text-sm mb-8 text-center">Give your project a name. You can analyze your data with AxBi once it&apos;s ready.</p>

            <form
              className="w-full"
              onSubmit={(e) => { e.preventDefault(); if (projectName.trim()) setStep('upload') }}
            >
              <input
                autoFocus
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="e.g. Q3 Sales Performance"
                maxLength={80}
                className="w-full px-5 py-4 rounded-2xl bg-card border border-border text-base text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary transition-colors"
              />
              <button
                type="submit"
                disabled={!projectName.trim()}
                className="mt-5 w-full py-4 rounded-2xl bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-lg transition-all hover:scale-[1.01] active:scale-[0.99] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
              >
                Continue <i className="fa-solid fa-arrow-right ml-1" />
              </button>
            </form>
          </div>
        )}

        {/* Step: Upload */}
        {step === 'upload' && (
          <div className="flex-1 flex flex-col items-center justify-center max-w-2xl mx-auto w-full">
            <h1 className="text-3xl font-extrabold mb-2 text-center">Upload your data</h1>
            <p className="text-muted-foreground text-sm mb-8 text-center">Drag &amp; drop or browse to upload your dataset</p>

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
                  <p className="text-lg font-bold mb-1">Drag &amp; drop your file here</p>
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
            <button onClick={() => setStep('name')} className="mt-6 text-sm text-muted-foreground hover:text-foreground transition-colors">
              <i className="fa-solid fa-arrow-left mr-1" /> Back
            </button>
          </div>
        )}

        {/* Step: Department */}
        {step === 'department' && (
          <div className="flex-1 flex flex-col items-center max-w-2xl mx-auto w-full">
            <h1 className="text-3xl font-extrabold mb-2 text-center">Select department</h1>
            <p className="text-muted-foreground text-sm mb-8 text-center">This helps AI suggest relevant dashboard templates</p>

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
                          setStep('upload')
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

            <div className="w-full grid grid-cols-2 gap-4 mb-6">
              {DEPARTMENTS.map((dept) => (
                <button
                  key={dept.value}
                  onClick={() => { setSelectedCategory(dept.value); setStep('confirm') }}
                  className={`p-5 rounded-2xl border-2 text-left transition-all duration-200 hover:scale-[1.02] ${
                    selectedCategory === dept.value ? 'border-primary bg-primary/10' : dept.color
                  }`}
                >
                  <i className={`${dept.icon} text-xl mb-2 block`} />
                  <p className="font-semibold">{dept.label}</p>
                </button>
              ))}
            </div>

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
                    onClick={() => { if (customCategory.trim()) setStep('confirm') }}
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

        {/* Step: Confirm */}
        {step === 'confirm' && (
          <div className="flex-1 flex flex-col items-center max-w-lg mx-auto w-full">
            <h1 className="text-3xl font-extrabold mb-2 text-center">Ready to analyze</h1>
            <p className="text-muted-foreground text-sm mb-8 text-center">Review your selections and start the AI pipeline</p>

            <div className="w-full p-6 rounded-2xl bg-card border border-border space-y-5 mb-8">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center">
                  <i className="fa-solid fa-folder text-primary text-xl" />
                </div>
                <div>
                  <p className="font-semibold text-sm">{projectName.trim() || selectedFile?.name}</p>
                  <p className="text-xs text-muted-foreground">Project name</p>
                </div>
              </div>
              <div className="h-px bg-muted" />
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
            <button onClick={() => setStep('department')} className="mt-4 text-sm text-muted-foreground hover:text-foreground transition-colors">
              <i className="fa-solid fa-arrow-left mr-1" /> Go back
            </button>
          </div>
        )}

        {/* Step: Processing */}
        {step === 'processing' && (
          <div className="flex-1 flex flex-col items-center justify-center max-w-md mx-auto w-full gap-0">
            <div className="w-28 mb-5 flex flex-col items-center justify-center gap-2">
              <LogoSpinner size={84} />
              <span className="text-2xl font-bold text-foreground">{progressPercent}%</span>
            </div>

            <div className="w-full max-w-xs h-1 bg-muted rounded-full overflow-hidden mb-3">
              <div
                className="h-full bg-gradient-to-r from-[#5A5AF6] to-[#8B5CF6] rounded-full transition-all duration-700 ease-out will-change-transform"
                style={{ width: `${progressPercent}%` }}
              />
            </div>

            <div className="text-center mb-4">
              <p className="text-base font-semibold text-foreground" style={{ animation: 'pulse-opacity 2s infinite' }}>{currentLabel}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {progress?.status === 'failed'
                  ? 'Processing failed'
                  : estimatedRemaining > 0
                    ? `~${estimatedRemaining}s remaining`
                    : 'Almost done...'}
              </p>
            </div>

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

        {/* Step: Everything ready */}
        {step === 'ready' && (
          <div className="flex-1 flex flex-col items-center justify-center max-w-lg mx-auto w-full text-center">
            <div className="relative w-24 h-24 mb-6 flex items-center justify-center">
              <div className="absolute inset-0 rounded-full bg-success/15" style={{ animation: 'pulse-opacity 2s infinite' }} />
              <div className="w-20 h-20 rounded-full bg-success/20 flex items-center justify-center">
                <i className="fa-solid fa-check text-success text-3xl" />
              </div>
            </div>
            <h1 className="text-3xl font-extrabold mb-2">Everything is ready!</h1>
            <p className="text-muted-foreground text-sm mb-2">
              <span className="text-foreground font-semibold">{projectName.trim() || selectedFile?.name}</span> has been processed and analyzed.
            </p>
            <p className="text-muted-foreground text-sm mb-8 max-w-sm">
              Start your journey with your data — ask AxBi anything by text or voice.
            </p>

            <button
              onClick={() => navigate('/agent')}
              className="w-full py-4 rounded-2xl bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-lg transition-all hover:scale-[1.02] active:scale-[0.98] flex items-center justify-center gap-2"
            >
              <i className="fa-solid fa-wand-magic-sparkles" />
              Start with your data
            </button>
            <button
              onClick={() => navigate('/BI-Dashboard')}
              className="mt-4 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Go to projects instead
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
