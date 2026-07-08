/** Merge streaming user-side transcript chunks. */
export function mergeLiveTranscript(prev: string, next: string): string {
  const p = prev.trimEnd()
  const n = next.trim()
  if (!n) return p
  if (!p) return n
  if (n.startsWith(p) || p.startsWith(n)) {
    return n.length >= p.length ? n : p
  }
  if (p.endsWith(n)) return p
  const needSpace = !/\s$/.test(p) && !/^\s/.test(n)
  return p + (needSpace ? ' ' : '') + n
}

/** Count letter-like characters by script (for voice-language guard). */
function scriptCounts(text: string) {
  let arabic = 0
  let latin = 0
  let devanagari = 0
  let cyrillic = 0
  let other = 0
  for (const ch of text) {
    const cp = ch.codePointAt(0) || 0
    if (
      (cp >= 0x0600 && cp <= 0x06ff) ||
      (cp >= 0x0750 && cp <= 0x077f) ||
      (cp >= 0x08a0 && cp <= 0x08ff) ||
      (cp >= 0xfb50 && cp <= 0xfdff) ||
      (cp >= 0xfe70 && cp <= 0xfeff)
    ) {
      arabic++
    } else if ((cp >= 65 && cp <= 90) || (cp >= 97 && cp <= 122)) {
      latin++
    } else if (cp >= 0x0900 && cp <= 0x097f) {
      devanagari++
    } else if (cp >= 0x0400 && cp <= 0x04ff) {
      cyrillic++
    } else if (/\s|\d|[.,!?;:'"()[\]{}_\-/@#$%&*+=<>]/.test(ch)) {
      continue
    } else {
      other++
    }
  }
  return { arabic, latin, devanagari, cyrillic, other }
}

/** True when a user transcript chunk matches the selected voice language. */
export function matchesVoiceLanguage(text: string, voiceLang: string): boolean {
  const t = text.trim()
  if (!t) return false

  const { arabic, latin, devanagari, cyrillic, other } = scriptCounts(t)
  const letters = arabic + latin + devanagari + cyrillic + other
  if (letters === 0) return true

  // Gemini Live sometimes mis-detects Arabic/English as Hindi or Cyrillic — drop these.
  if (devanagari > 0 || cyrillic > 0) return false

  const lang = (voiceLang || 'en').toLowerCase()
  if (lang.startsWith('ar')) {
    if (arabic === 0 && latin > 0) return false
    return arabic > 0 || latin <= arabic * 2
  }
  if (lang.startsWith('en')) {
    if (latin === 0 && arabic > 0) return false
    return latin > 0 || arabic <= latin
  }
  return true
}

/** Merge user transcript chunks but ignore updates in the wrong language/script. */
export function mergeFilteredUserTranscript(prev: string, next: string, voiceLang: string): string {
  const n = next.trim()
  if (!n) return prev
  if (!matchesVoiceLanguage(n, voiceLang)) return prev
  return mergeLiveTranscript(prev, n)
}

/** Assistant output transcription is usually cumulative — prefer the longest prefix. */
export function mergeAssistantTranscript(prev: string, next: string): string {
  const p = prev.trimEnd()
  const n = next.trim()
  if (!n) return p
  if (!p) return n
  if (n.startsWith(p)) return n
  if (p.startsWith(n)) return p
  if (p.endsWith(n)) return p
  return mergeLiveTranscript(p, n)
}

/** Strip bidi control chars that break Arabic rendering in LTR containers. */
export function normalizeTranscriptText(text: string): string {
  return text
    .replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/** Detect dominant direction for mixed voice captions. */
export function captionDir(text: string): 'rtl' | 'ltr' | 'auto' {
  const arabic = (text.match(/[\u0600-\u06FF]/g) || []).length
  const latin = (text.match(/[A-Za-z]/g) || []).length
  if (arabic > latin) return 'rtl'
  if (latin > arabic) return 'ltr'
  return 'auto'
}
