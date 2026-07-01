// AudioWorklet processor that forwards raw mono Float32 mic frames to the main
// thread. The main thread downsamples to 16kHz, converts to PCM16, and streams
// the result to the Gemini Live proxy. Kept deliberately tiny — no allocation
// beyond the per-frame copy required because the input buffer is reused.
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0]
    const channel = input && input[0]
    if (channel && channel.length) {
      // Copy: the underlying buffer is recycled by the audio thread.
      this.port.postMessage(channel.slice(0))
    }
    return true
  }
}

registerProcessor('pcm-capture', PCMCaptureProcessor)
