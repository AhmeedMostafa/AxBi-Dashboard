import toast from 'react-hot-toast'

export type VoiceLang = 'en' | 'ar-EG' | 'en-US' | string

interface LangScore {
  detected: 'en' | 'ar' | 'mixed' | 'unknown'
  confidence: number // 0..1
  arabicChars: number
  latinChars: number
  totalLetters: number
}

/**
 * Detect dominant script of a text snippet.
 * Uses Unicode block ranges — fast, no deps, good enough for EN vs AR.
 *   - Arabic block: U+0600..U+06FF (+ supplements U+0750..U+077F, U+08A0..U+08FF, U+FB50..U+FDFF, U+FE70..U+FEFF)
 *   - Latin block (English-ish): a-z, A-Z
 */
export function detectTextLanguage(text: string): LangScore {
  if (!text || !text.trim()) {
    return { detected: 'unknown', confidence: 0, arabicChars: 0, latinChars: 0, totalLetters: 0 }
  }
  let arabic = 0
  let latin = 0
  for (const ch of text) {
    const cp = ch.codePointAt(0) || 0
    if (
      (cp >= 0x0600 && cp <= 0x06FF) ||
      (cp >= 0x0750 && cp <= 0x077F) ||
      (cp >= 0x08A0 && cp <= 0x08FF) ||
      (cp >= 0xFB50 && cp <= 0xFDFF) ||
      (cp >= 0xFE70 && cp <= 0xFEFF)
    ) {
      arabic++
    } else if ((cp >= 65 && cp <= 90) || (cp >= 97 && cp <= 122)) {
      latin++
    }
  }
  const total = arabic + latin
  if (total === 0) return { detected: 'unknown', confidence: 0, arabicChars: 0, latinChars: 0, totalLetters: 0 }

  const arRatio = arabic / total
  const enRatio = latin / total

  let detected: LangScore['detected']
  let confidence: number
  if (arRatio >= 0.6) {
    detected = 'ar'
    confidence = arRatio
  } else if (enRatio >= 0.6) {
    detected = 'en'
    confidence = enRatio
  } else {
    detected = 'mixed'
    confidence = Math.max(arRatio, enRatio)
  }
  return { detected, confidence, arabicChars: arabic, latinChars: latin, totalLetters: total }
}

function selectedLangFamily(lang: string): 'en' | 'ar' | 'unknown' {
  if (!lang) return 'unknown'
  const l = lang.toLowerCase()
  if (l.startsWith('ar')) return 'ar'
  if (l.startsWith('en')) return 'en'
  return 'unknown'
}

/**
 * If the detected text language doesn't match the selected voice language,
 * show a non-blocking toast prompt with three actions:
 *   - "Use <detected>"           → switches the saved voice lang AND uses it now
 *   - "Keep <selected>"          → uses the original lang for this playback only
 *   - "Cancel"                   → aborts the TTS request (no quota burn)
 * If no mismatch, resolves immediately with the selected lang.
 *
 * Returns Promise<string | null> — language code to use, or null on cancel.
 */
export function confirmLanguageBeforeTTS(
  text: string,
  selectedLang: string,
  opts?: { minLetters?: number },
): Promise<string | null> {
  const minLetters = opts?.minLetters ?? 12
  const score = detectTextLanguage(text)

  if (score.totalLetters < minLetters || score.detected === 'unknown') {
    return Promise.resolve(selectedLang)
  }
  const sel = selectedLangFamily(selectedLang)
  if (sel === 'unknown') return Promise.resolve(selectedLang)
  if (score.detected === 'mixed') return Promise.resolve(selectedLang)
  if (score.detected === sel) return Promise.resolve(selectedLang)

  const detectedLabel = score.detected === 'ar' ? 'Arabic' : 'English'
  const selectedLabel = sel === 'ar' ? 'Egyptian Arabic' : 'English'
  const newLang = score.detected === 'ar' ? 'ar-EG' : 'en'

  return new Promise<string | null>((resolve) => {
    const toastId = toast.custom(
      (t) => (
        <div
          className={`max-w-md w-full bg-card border border-amber-500/40 rounded-xl px-4 py-3 shadow-2xl pointer-events-auto ${
            t.visible ? 'opacity-100' : 'opacity-0'
          } transition-opacity`}
          style={{ minWidth: 360 }}
        >
          <div className="flex items-start gap-3">
            <div className="text-warning text-lg leading-none mt-0.5">⚠</div>
            <div className="flex-1">
              <div className="text-sm font-semibold text-amber-200 mb-0.5">Language mismatch</div>
              <div className="text-xs text-muted-foreground leading-relaxed">
                The text looks like <b>{detectedLabel}</b> but the voice is set to{' '}
                <b>{selectedLabel}</b>. Which should I use?
              </div>
              <div className="mt-3 flex gap-2 flex-wrap">
                <button
                  onClick={() => {
                    toast.dismiss(toastId)
                    try { localStorage.setItem('bi-voice-language', newLang) } catch {
                      /* localStorage unavailable */
                    }
                    resolve(newLang)
                  }}
                  className="text-xs font-medium px-3 py-1.5 rounded-md bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/30 transition-colors"
                >
                  Use {detectedLabel}
                </button>
                <button
                  onClick={() => {
                    toast.dismiss(toastId)
                    resolve(selectedLang)
                  }}
                  className="text-xs font-medium px-3 py-1.5 rounded-md bg-muted border border-border text-muted-foreground hover:bg-muted transition-colors"
                >
                  Keep {selectedLabel}
                </button>
                <button
                  onClick={() => {
                    toast.dismiss(toastId)
                    resolve(null)
                  }}
                  className="text-xs font-medium px-3 py-1.5 rounded-md text-muted-foreground hover:text-muted-foreground transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      ),
      { duration: 15_000, position: 'top-center' },
    )
  })
}
