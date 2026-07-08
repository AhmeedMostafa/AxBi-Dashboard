// Gemini Live voice client (frontend half).
//
// Talks to the Django Channels proxy at /ws/live/ which bridges to the Gemini
// Live API. Responsibilities:
//   - capture mic audio via an AudioWorklet, downsample to 16kHz PCM16, base64,
//     and stream it to the proxy;
//   - receive 24kHz PCM16 audio from the proxy and play it back gaplessly;
//   - handle barge-in (clear the playback queue when the model is interrupted);
//   - surface status + live transcripts to the UI via callbacks.
//
// The API key never reaches the browser — the proxy injects it server-side.

export type LiveStatus = 'connecting' | 'listening' | 'processing' | 'speaking' | 'closed' | 'error'

export interface LiveAction {
  type: 'navigate' | 'toast' | 'refresh'
  payload: Record<string, any>
}

export interface LiveClientCallbacks {
  onStatus?: (status: LiveStatus) => void
  onUserTranscript?: (text: string) => void
  onAssistantTranscript?: (text: string) => void
  onError?: (message: string) => void
  onClose?: (reason: string) => void
  // Tool side-effects relayed from the proxy when the voice agent takes an action.
  onAction?: (action: LiveAction) => void
  onChart?: (chart: any) => void
  onVisual3D?: (visual: any) => void
  onMetrics?: (metrics: any) => void
  // Fired when the assistant finishes a turn (good boundary to commit text).
  onTurnComplete?: () => void
}

export interface LiveClientOptions {
  token: string
  lang: string // 'ar-EG' | 'en-US'
  datasetId?: string | null
  voice?: string
}

const TARGET_INPUT_RATE = 16000
const OUTPUT_RATE = 24000

function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    let s = Math.max(-1, Math.min(1, input[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

// Linear-interpolation downsample from inRate to TARGET_INPUT_RATE.
function downsample(buffer: Float32Array, inRate: number): Float32Array {
  if (inRate === TARGET_INPUT_RATE) return buffer
  const ratio = inRate / TARGET_INPUT_RATE
  const outLen = Math.floor(buffer.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const idx = i * ratio
    const lo = Math.floor(idx)
    const hi = Math.min(lo + 1, buffer.length - 1)
    const frac = idx - lo
    out[i] = buffer[lo] * (1 - frac) + buffer[hi] * frac
  }
  return out
}

function int16ToBase64(int16: Int16Array): string {
  const bytes = new Uint8Array(int16.buffer)
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunk)) as any)
  }
  return btoa(binary)
}

function base64ToInt16(b64: string): Int16Array {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Int16Array(bytes.buffer, 0, Math.floor(bytes.byteLength / 2))
}

// Schedules incoming 24kHz PCM16 chunks for gapless playback and supports an
// immediate flush for barge-in.
class PlaybackScheduler {
  private ctx: AudioContext
  private nextStart = 0
  private active = new Set<AudioBufferSourceNode>()
  onPlaying?: (playing: boolean) => void

  constructor() {
    this.ctx = new AudioContext()
  }

  resume() {
    if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {})
  }

  enqueue(int16: Int16Array) {
    if (!int16.length) return
    const float = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) float[i] = int16[i] / 0x8000
    const buffer = this.ctx.createBuffer(1, float.length, OUTPUT_RATE)
    buffer.copyToChannel(float, 0)

    const src = this.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(this.ctx.destination)

    const now = this.ctx.currentTime
    const startAt = Math.max(now, this.nextStart)
    src.start(startAt)
    this.nextStart = startAt + buffer.duration

    this.active.add(src)
    if (this.active.size === 1) this.onPlaying?.(true)
    src.onended = () => {
      this.active.delete(src)
      if (this.active.size === 0) this.onPlaying?.(false)
    }
  }

  flush() {
    this.active.forEach((s) => {
      try { s.onended = null; s.stop() } catch { /* already stopped */ }
    })
    this.active.clear()
    this.nextStart = 0
    this.onPlaying?.(false)
  }

  async close() {
    this.flush()
    try { await this.ctx.close() } catch { /* noop */ }
  }

  get playing() {
    return this.active.size > 0
  }
}

export class LiveClient {
  private ws: WebSocket | null = null
  private captureCtx: AudioContext | null = null
  private workletNode: AudioWorkletNode | null = null
  private micSource: MediaStreamAudioSourceNode | null = null
  private stream: MediaStream | null = null
  private player: PlaybackScheduler | null = null
  private cb: LiveClientCallbacks
  private opts: LiveClientOptions
  private closed = false
  /** When true, mic frames are not streamed (push-to-talk "send" pressed). */
  private micMuted = false
  private turnCompletePending = false

  constructor(opts: LiveClientOptions, cb: LiveClientCallbacks) {
    this.opts = opts
    this.cb = cb
  }

  async start() {
    this.closed = false
    this.micMuted = false
    this.turnCompletePending = false
    this.cb.onStatus?.('connecting')

    // 1) Mic + capture graph (16kHz target). Must be created from a user
    //    gesture upstream so the AudioContext is allowed to run.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    // Guard against being closed while awaiting (e.g. StrictMode double-mount).
    if (this.closed) { this.cleanup(); return }

    // Try to open the capture context directly at 16kHz; fall back to native
    // rate + manual downsample if the browser refuses.
    try {
      this.captureCtx = new AudioContext({ sampleRate: TARGET_INPUT_RATE })
    } catch {
      this.captureCtx = new AudioContext()
    }
    const ctx = this.captureCtx
    await ctx.audioWorklet.addModule('/pcm-capture-worklet.js')
    if (this.closed) { this.cleanup(); return }
    this.micSource = ctx.createMediaStreamSource(this.stream)
    this.workletNode = new AudioWorkletNode(ctx, 'pcm-capture')
    // Pull the graph without echoing the mic to the speakers (gain 0 sink).
    const sink = ctx.createGain()
    sink.gain.value = 0
    this.micSource.connect(this.workletNode)
    this.workletNode.connect(sink)
    sink.connect(ctx.destination)

    this.player = new PlaybackScheduler()
    this.player.onPlaying = (playing) => {
      if (this.closed) return
      if (playing) {
        this.cb.onStatus?.('speaking')
      } else {
        this.cb.onStatus?.(this.micMuted ? 'processing' : 'listening')
        this.maybeFinishTurn()
      }
    }

    // 2) Open the proxy WebSocket.
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const params = new URLSearchParams({
      token: this.opts.token,
      lang: this.opts.lang,
    })
    if (this.opts.datasetId) params.set('dataset', this.opts.datasetId)
    if (this.opts.voice) params.set('voice', this.opts.voice)
    const url = `${proto}://${location.host}/ws/live/?${params.toString()}`

    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(url)
      this.ws = ws
      let opened = false
      ws.onopen = () => { opened = true; resolve() }
      ws.onerror = () => { if (!opened) reject(new Error('WebSocket connection failed')) }
      ws.onclose = (ev) => {
        if (!opened) { reject(new Error('WebSocket closed before open')); return }
        // Ignore close events from a socket that's no longer current. This
        // happens during a language switch: the old socket is closed and a new
        // one opened, and the old socket's onclose fires after this.ws has been
        // reassigned. Without this guard it would wrongly end the new session.
        if (this.ws !== ws) return
        if (!this.closed) {
          this.cb.onClose?.(ev.reason || 'connection closed')
          this.cleanup()
        }
      }
      ws.onmessage = (ev) => {
        if (this.ws !== ws) return
        this.handleMessage(ev.data)
      }
    })

    // If we were closed while the socket was connecting, tear everything down
    // now so we don't leave an orphaned upstream Live session running.
    if (this.closed) { this.cleanup(); return }

    // 3) Stream mic frames once the socket is up.
    const inRate = ctx.sampleRate
    this.workletNode.port.onmessage = (e: MessageEvent) => {
      if (this.closed || this.micMuted || !this.ws || this.ws.readyState !== WebSocket.OPEN) return
      const frame = e.data as Float32Array
      const ds = downsample(frame, inRate)
      const pcm = floatTo16BitPCM(ds)
      const b64 = int16ToBase64(pcm)
      this.ws.send(JSON.stringify({ type: 'audio', data: b64 }))
    }
  }

  private handleMessage(raw: any) {
    let msg: any
    try { msg = JSON.parse(raw) } catch { return }
    switch (msg.type) {
      case 'ready':
        this.player?.resume()
        this.cb.onStatus?.('listening')
        break
      case 'audio':
        this.player?.enqueue(base64ToInt16(msg.data))
        break
      case 'interrupted':
        // Barge-in: stop whatever is playing immediately.
        this.player?.flush()
        this.cb.onStatus?.(this.micMuted ? 'processing' : 'listening')
        break
      case 'user_transcript':
        this.cb.onUserTranscript?.(msg.text || '')
        break
      case 'assistant_transcript':
        this.cb.onAssistantTranscript?.(msg.text || '')
        break
      case 'action':
        if (msg.action) this.cb.onAction?.(msg.action)
        break
      case 'chart':
        if (msg.data) this.cb.onChart?.(msg.data)
        break
      case 'visual3d':
        if (msg.data) this.cb.onVisual3D?.(msg.data)
        break
      case 'metrics':
        if (msg.data) this.cb.onMetrics?.(msg.data)
        break
      case 'turn_complete':
        this.turnCompletePending = true
        this.cb.onStatus?.(this.micMuted ? 'processing' : 'listening')
        this.cb.onTurnComplete?.()
        this.maybeFinishTurn()
        break
      case 'error':
        this.cb.onError?.(msg.message || 'Live error')
        break
      case 'closed':
        this.cb.onClose?.(msg.reason || 'closed')
        this.close()
        break
    }
  }

  // Switch language mid-conversation: the proxy builds the system instruction +
  // speech config at connect time, so we tear down and reopen with the new lang.
  async switchLanguage(lang: string) {
    this.opts.lang = lang
    const wasClosed = this.closed
    this.closed = true
    this.cleanup()
    this.closed = false
    if (!wasClosed) await this.start()
  }

  sendText(text: string) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'text', text }))
    }
  }

  /**
   * Mute the mic (call-style): stop streaming frames and signal end-of-turn so
   * Gemini processes what was said. The session stays open and the AI keeps
   * replying; we just stop sending audio.
   */
  muteMic() {
    if (this.closed) return
    this.micMuted = true
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'audio_end' }))
    }
    // If the AI is currently speaking, keep 'speaking'; otherwise show processing.
    this.cb.onStatus?.(this.isPlaying() ? 'speaking' : 'processing')
  }

  /**
   * Unmute the mic: resume streaming frames. We do NOT force-stop playback here —
   * barge-in happens naturally once the user actually speaks (Gemini's VAD fires
   * an "interrupted" event, which flushes playback).
   */
  unmuteMic() {
    if (this.closed) return
    this.micMuted = false
    this.cb.onStatus?.('listening')
  }

  isMuted() {
    return this.micMuted
  }

  isPlaying() {
    return this.player?.playing ?? false
  }

  private maybeFinishTurn() {
    if (!this.turnCompletePending || this.closed) return
    if (this.isPlaying()) return
    this.turnCompletePending = false
  }

  private cleanup() {
    try { this.workletNode?.port && (this.workletNode.port.onmessage = null) } catch { /* noop */ }
    try { this.workletNode?.disconnect() } catch { /* noop */ }
    try { this.micSource?.disconnect() } catch { /* noop */ }
    try { this.stream?.getTracks().forEach((t) => t.stop()) } catch { /* noop */ }
    try { this.captureCtx?.close() } catch { /* noop */ }
    try { this.player?.close() } catch { /* noop */ }
    try {
      if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close()
    } catch { /* noop */ }
    this.workletNode = null
    this.micSource = null
    this.stream = null
    this.captureCtx = null
    this.player = null
    this.ws = null
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.micMuted = false
    this.turnCompletePending = false
    this.cleanup()
    this.cb.onStatus?.('closed')
  }
}
