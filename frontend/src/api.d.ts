// Type declarations for the JavaScript API client (api.js).
// Signatures are intentionally loose; callers treat responses as dynamic JSON.

export function uploadFile(files: File[] | FileList, category?: string, projectName?: string): Promise<any>;
export function appendToDataset(datasetId: string, files: File[] | FileList): Promise<any>;
export function checkJobStatus(jobId: string): Promise<any>;
export function AI_Model(datasetId: string, payload: any): Promise<any>;
export function getForecastHistory(datasetId: string): Promise<any>;
export function getForecastDetail(forecastId: string): Promise<any>;
export function getForecastStatus(jobId: string): Promise<any>;
export function getDatasetRows(datasetId: string, opts?: { limit?: number; offset?: number }): Promise<any>;
export function getFeatureRecommendations(datasetId: string, opts?: { target?: string; time?: string }): Promise<any>;
export function runSegmentation(datasetId: string): Promise<any>;
export function getSegmentationResults(datasetId: string): Promise<any>;
export function exportPdf(datasetId: string): Promise<any>;
export function getDashboardStats(): Promise<any>;
export function getForecastAccuracy(forecastId: string): Promise<any>;
export function deleteForecast(forecastId: string): Promise<any>;
export function listDatasets(): Promise<any>;
export function deleteDataset(datasetId: string): Promise<any>;
export function aggregateCharts(datasetId: string, charts: any[]): Promise<any>;
export function customizeChart(datasetId: string, options: any): Promise<any>;
export function getDatasetDashboard(datasetId: string): Promise<any>;
export function getRecommendations(datasetId: string): Promise<any>;
export function generateRecommendations(datasetId: string, force?: boolean): Promise<any>;
export function updateDatasetCategory(datasetId: string, category: string): Promise<any>;
export function detectDatasetCategory(datasetId: string): Promise<any>;
export function sendChatMessage(messages: any, datasetId?: string | null, conversationId?: string | null, signal?: AbortSignal | null): Promise<any>;
export function sendChatMessageStream(
    messages: any,
    datasetId?: string | null,
    conversationId?: string | null,
    signal?: AbortSignal | null,
    onEvent?: (event: any) => void,
    options?: any
): Promise<any>;
export function listConversations(): Promise<any>;
export function createConversation(title: string): Promise<any>;
export function getConversation(conversationId: string): Promise<any>;
export function addConversationMessage(conversationId: string, message: any): Promise<any>;
export function shareConversation(conversationId: string): Promise<any>;
export function getSharedConversation(token: string): Promise<any>;
export function deleteConversation(conversationId: string): Promise<any>;
export function getModelCategoryStats(): Promise<any>;
export function getColumnCorrelations(datasetId: string): Promise<any>;
export function getTTSUsage(): Promise<any>;
export function translateForTTS(text: string, targetLanguage: string, options?: any): Promise<any>;
export function generateDatasetAudioOverview(datasetId: string, options?: any): Promise<any>;
export function listVoiceLogs(options?: any): Promise<any>;
export function getVoiceLogAudio(entryId: string): Promise<any>;
export function deleteVoiceLog(entryId: string): Promise<any>;
export function clearVoiceLogs(): Promise<any>;
export function getTTS(text: string, voice?: string, language?: string, options?: any): Promise<any>;
export function getTTSWithUsage(text: string, voice?: string, language?: string, options?: any): Promise<any>;
