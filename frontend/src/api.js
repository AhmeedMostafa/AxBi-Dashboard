import axios from 'axios'
import { supabase } from './supabase-client'

const api = axios.create({
  baseURL: '/api',
})

async function getAuthHeader() {
  const { data, error } = await supabase.auth.getSession()

  if (error) {
    throw new Error(error.message)
  }

  const token = data?.session?.access_token
  if (!token) {
    throw new Error('Missing Supabase access token')
  }

  return { Authorization: `Bearer ${token}` }
}

export async function uploadFile(files, category, projectName) {
  const headers = await getAuthHeader()
  const formData = new FormData()
  const fileList = Array.isArray(files) ? files : [files]
  fileList.forEach((f) => formData.append('file', f))
  formData.append('category', category)
  if (projectName) formData.append('project_name', projectName)
  const response = await api.post('/file-upload/', formData, { headers })
  return response.data
}

export async function appendToDataset(datasetId, files) {
  const headers = await getAuthHeader()
  const formData = new FormData()
  const fileList = Array.isArray(files) ? files : [files]
  fileList.forEach((f) => formData.append('file', f))
  const response = await api.post(`/datasets/${datasetId}/append/`, formData, { headers })
  return response.data
}

export async function checkJobStatus(jobId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/check/${jobId}/`, { headers })
  return response.data

}

export async function AI_Model(datasetId, payload) {
  const headers = await getAuthHeader()
  const response = await api.post(`/datasets/${datasetId}/forecast/`, payload, { headers })
  return response.data

}

export async function getForecastHistory(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/forecasts/`, { headers })
  return response.data
}

// Poll an async ("accurate" mode) forecast job. Returns
// { status: 'pending' | 'completed' | 'failed', forecast?, error? }.
export async function getForecastStatus(jobId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/forecasts/status/${jobId}/`, { headers })
  return response.data
}

export async function getForecastDetail(forecastId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/forecasts/${forecastId}/`, { headers })
  return response.data
}

export async function getDatasetRows(datasetId, { limit = 500, offset = 0 } = {}) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/rows/`, {
    headers,
    params: { limit, offset },
  })
  return response.data
}

export async function getFeatureRecommendations(datasetId, { target, time } = {}) {
  const headers = await getAuthHeader()
  const response = await api.get(
    `/datasets/${datasetId}/feature-recommendations/`,
    { headers, params: { target, time } },
  )
  return response.data
}

export async function runSegmentation(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.post(`/datasets/${datasetId}/segmentation/`, {}, { headers })
  return response.data
}

export async function getSegmentationResults(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/segmentation/results/`, { headers })
  return response.data
}

export async function exportPdf(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.post(
    `/datasets/${datasetId}/export-pdf/`,
    {},
    { headers, responseType: 'blob' }
  )
  return response.data
}

export async function getDashboardStats() {
  const headers = await getAuthHeader()
  const response = await api.get('/dashboard/stats/', { headers })
  return response.data
}

export async function getForecastAccuracy(forecastId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/forecasts/${forecastId}/accuracy/`, { headers })
  return response.data
}

export async function deleteForecast(forecastId) {
  const headers = await getAuthHeader()
  const response = await api.delete(`/forecasts/${forecastId}/delete/`, { headers })
  return response.data
}

export async function listDatasets() {
  const headers = await getAuthHeader()
  const response = await api.get('/datasets/', { headers })
  return response.data
}

export async function deleteDataset(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.delete(`/datasets/${datasetId}/`, { headers })
  return response.data
}

export async function aggregateCharts(datasetId, charts) {
  const headers = await getAuthHeader()
  const response = await api.post(`/datasets/${datasetId}/aggregate/`, { charts }, { headers })
  return response.data
}

export async function customizeChart(datasetId, options) {
  const headers = await getAuthHeader()
  const response = await api.post(`/datasets/${datasetId}/customize-chart/`, options, { headers })
  return response.data
}

export async function getDatasetDashboard(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/dashboard/`, { headers })
  return response.data
}

export async function getRecommendations(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/recommendations/`, { headers })
  return response.data
}

export async function generateRecommendations(datasetId, force = false) {
  const headers = await getAuthHeader()
  const response = await api.post(
    `/datasets/${datasetId}/recommendations/generate/`,
    { force },
    { headers }
  )
  return response.data
}

export async function updateDatasetCategory(datasetId, category) {
  const headers = await getAuthHeader()
  const response = await api.patch(
    `/datasets/${datasetId}/category/`,
    { category },
    { headers }
  )
  return response.data
}

export async function detectDatasetCategory(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.post(`/datasets/${datasetId}/detect-category/`, {}, { headers })
  return response.data
}

export async function sendChatMessage(messages, datasetId = null, conversationId = null, signal = null) {
  const headers = await getAuthHeader()
  const body = { messages }
  if (datasetId) body.dataset_id = datasetId
  if (conversationId) body.conversation_id = conversationId
  const config = { headers }
  if (signal) config.signal = signal
  const response = await api.post('/chat/', body, config)
  return response.data
}

export async function sendChatMessageStream(messages, datasetId = null, conversationId = null, signal = null, onEvent = () => {}, options = {}) {
  const { data, error } = await supabase.auth.getSession()
  if (error) throw new Error(error.message)
  const token = data?.session?.access_token
  if (!token) throw new Error('Missing Supabase access token')

  const body = { messages }
  if (datasetId) body.dataset_id = datasetId
  if (conversationId) body.conversation_id = conversationId
  if (options.voiceMode) body.voice_mode = true
  if (options.voiceLanguage) body.voice_language = options.voiceLanguage

  const response = await fetch('/api/chat/stream/', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    let errMsg = `HTTP ${response.status}`
    try { const d = await response.json(); errMsg = d.error || errMsg } catch {}
    throw new Error(errMsg)
  }

  if (!response.body) throw new Error('No response body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data: ')) continue
        try { onEvent(JSON.parse(line.slice(6))) } catch {}
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function listConversations() {
  const headers = await getAuthHeader()
  const response = await api.get('/conversations/', { headers })
  return response.data
}

export async function createConversation(title) {
  const headers = await getAuthHeader()
  const response = await api.post('/conversations/', { title }, { headers })
  return response.data
}

export async function getConversation(conversationId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/conversations/${conversationId}/`, { headers })
  return response.data
}

export async function addConversationMessage(conversationId, message) {
  const headers = await getAuthHeader()
  const response = await api.post(`/conversations/${conversationId}/messages/`, message, { headers })
  return response.data
}

export async function shareConversation(conversationId) {
  const headers = await getAuthHeader()
  const response = await api.post(`/conversations/${conversationId}/share/`, {}, { headers })
  return response.data
}

export async function getSharedConversation(token) {
  const response = await api.get(`/share/${token}/`)
  return response.data
}

export async function deleteConversation(conversationId) {
  const headers = await getAuthHeader()
  const response = await api.delete(`/conversations/${conversationId}/delete/`, { headers })
  return response.data
}

export async function getModelCategoryStats() {
  const headers = await getAuthHeader()
  const response = await api.get('/model-category-stats/', { headers })
  return response.data
}

export async function getColumnCorrelations(datasetId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/datasets/${datasetId}/column-correlations/`, { headers })
  return response.data
}

export async function getTTSUsage() {
  const headers = await getAuthHeader()
  const response = await api.get('/tts/usage/', { headers })
  return response.data
}

// Rewrite text in a target language ("en" or "ar-EG") using Gemini, optimized
// for spoken delivery. Returns { text, output_chars, target_language, model }.
// Options: { style: 'formal' | 'casual' }
export async function translateForTTS(text, targetLanguage, options = {}) {
  const headers = await getAuthHeader()
  const body = { text, target_language: targetLanguage }
  if (options.style) body.style = options.style
  const response = await api.post('/translate-for-tts/', body, { headers })
  return response.data
}

// Generate a full analytical spoken overview for a dataset, with real numbers
// and insights pulled from columns + report. Returns { text, language, style, ... }
export async function generateDatasetAudioOverview(datasetId, options = {}) {
  const headers = await getAuthHeader()
  const body = {
    language: options.language || 'en',
    style: options.style || 'formal',
    duration_seconds: options.durationSeconds || 75,
  }
  if (options.userName) body.user_name = options.userName
  if (options.skipVoiceLog) body.skip_voice_log = true
  const response = await api.post(`/datasets/${datasetId}/audio-overview/`, body, { headers })
  return response.data
}

// ── Voice / TTS request logs (audit trail) ────────────────────────────────
// List recent TTS/translate/overview requests for the current user.
// options: { kind?: 'tts'|'translate'|'overview', limit?: number, offset?: number }
export async function listVoiceLogs(options = {}) {
  const headers = await getAuthHeader()
  const params = new URLSearchParams()
  if (options.kind) params.set('kind', options.kind)
  if (options.limit) params.set('limit', String(options.limit))
  if (options.offset) params.set('offset', String(options.offset))
  const qs = params.toString()
  const response = await api.get(`/voice-logs/${qs ? `?${qs}` : ''}`, { headers })
  return response.data
}

// Fetch the recorded MP3 for a given TTS log entry (returns Blob).
export async function getVoiceLogAudio(entryId) {
  const headers = await getAuthHeader()
  const response = await api.get(`/voice-logs/${entryId}/audio/`, {
    headers,
    responseType: 'blob',
  })
  return response.data
}

export async function deleteVoiceLog(entryId) {
  const headers = await getAuthHeader()
  const response = await api.delete(`/voice-logs/${entryId}/`, { headers })
  return response.data
}

export async function clearVoiceLogs() {
  const headers = await getAuthHeader()
  const response = await api.delete('/voice-logs/clear/', { headers })
  return response.data
}

// Synthesize speech. Options:
//   { voice, language, model, prompt, speakingRate, pitch }
//   - model:  'gemini' (Gemini 3.1 Flash TTS Preview, default) or 'chirp3'
//   - prompt: natural-language tone instruction (Gemini TTS only),
//             e.g. "Read aloud in a warm, welcoming tone."
// Returns a Blob (audio/wav for Gemini, audio/mpeg for Chirp 3 HD).
export async function getTTS(text, voice = '', language = 'en', options = {}) {
  const headers = await getAuthHeader()
  const body = { text, voice, language }
  if (options.model) body.model = options.model
  if (options.prompt) body.prompt = options.prompt
  if (options.speakingRate != null) body.speaking_rate = options.speakingRate
  if (options.pitch != null) body.pitch = options.pitch
  const response = await api.post('/tts/', body, {
    headers,
    responseType: 'blob',
  })
  return response.data
}

// Same as getTTS but returns { blob, usage } with full response headers
// (model, voice, prompt echo, daily char/cost stats).
export async function getTTSWithUsage(text, voice = '', language = 'en', options = {}) {
  const headers = await getAuthHeader()
  const body = { text, voice, language }
  if (options.model) body.model = options.model
  if (options.prompt) body.prompt = options.prompt
  if (options.speakingRate != null) body.speaking_rate = options.speakingRate
  if (options.pitch != null) body.pitch = options.pitch
  if (options.audioOverview) body.audio_overview = options.audioOverview
  const response = await api.post('/tts/', body, {
    headers,
    responseType: 'blob',
  })
  const h = response.headers || {}
  const num = (v) => (v === undefined || v === null || v === '' ? null : Number(v))
  return {
    blob: response.data,
    usage: {
      chars: num(h['x-tts-chars']),
      todayChars: num(h['x-tts-today-chars']),
      todayRequests: num(h['x-tts-today-requests']),
      todayCostUsd: num(h['x-tts-today-cost-usd']),
      capChars: num(h['x-tts-cap-chars']),
      voice: h['x-tts-voice'] || '',
      language: h['x-tts-language'] || '',
      model: h['x-tts-model'] || '',
      prompt: h['x-tts-prompt'] || '',
      audioFormat: h['x-tts-audio-format'] || 'mp3',
    },
  }
}
