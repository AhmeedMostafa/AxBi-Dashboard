import './App.css'
import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { supabase } from './supabase-client'
import { LogoLoader } from './Components/ui/LogoLoader'
import Register from './Components/Register/Register'
import Login from './Components/Login/Login'
import ProjectWizard from './Components/Onboarding/ProjectWizard'
import AgentPage from './Components/Agent/AgentPage'
import AIModel from './Components/AIModel/AIModel'
import ReportPage from './Components/Report/ReportPage'
import ForecastHistoryPage from './Components/ForecastHistory/ForecastHistoryPage'
import RecommendationsPage from './Components/Recommendations/RecommendationsPage'
import Projects from './Components/Projects/Projects'
import Layout from './Components/Layout/Layout'
import ConversationPage from './Components/Conversation/ConversationPage'
import SharePage from './Components/Conversation/SharePage'
import ProfilePage from './Components/Profile/ProfilePage'
import VoiceLogsPage from './Components/VoiceLogs/VoiceLogsPage'
import { Toaster } from 'react-hot-toast'

function App() {
  const [booting, setBooting] = useState(true)

  useEffect(() => {
    // Resolve the initial auth session before showing the app, so we can
    // display a branded loading screen on first paint.
    supabase.auth.getSession().finally(() => setBooting(false))
  }, [])

  if (booting) {
    return <LogoLoader fullScreen message="Starting AxBi" />
  }

  return (
    <>
      <BrowserRouter>
        <Toaster position="top-right" />
        <Routes>
          <Route path='/' element={<Login />} />
          <Route path='/register' element={<Register />} />
          <Route path='/login' element={<Login />} />
          <Route path='/onboarding' element={<ProjectWizard />} />
          <Route path='/share/:token' element={<SharePage />} />
          <Route element={<Layout />}>
            <Route path='/BI-Dashboard/:datasetId?' element={<Projects />} />
            <Route path='/agent' element={<AgentPage />} />
            <Route path='/upload' element={<ProjectWizard />} />
            <Route path='/AI-Insights' element={<AIModel />} />
            <Route path='/forecast-history' element={<ForecastHistoryPage />} />
            <Route path='/recommendations' element={<RecommendationsPage />} />
            <Route path='/report' element={<ReportPage />} />
            <Route path='/profile' element={<ProfilePage />} />
            <Route path='/voice-logs' element={<VoiceLogsPage />} />
            <Route path='/conversation/:id' element={<ConversationPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </>
  )
}

export default App
