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
