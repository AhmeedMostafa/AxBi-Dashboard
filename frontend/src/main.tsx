import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import '@fortawesome/fontawesome-free/css/all.min.css'
import { initTheme, ThemeProvider } from './hooks/useTheme'

// Set theme class before first paint (no flash of wrong theme).
initTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
)
