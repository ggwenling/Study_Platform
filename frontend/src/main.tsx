import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { MotionConfig } from 'motion/react'
import { App } from './App'
import { ThemeProvider } from './Theme'
import '@/components/arc/foundation.css'
import './styles.css'
import './typography.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <MotionConfig reducedMotion="user">
        <ThemeProvider><App /></ThemeProvider>
      </MotionConfig>
    </BrowserRouter>
  </StrictMode>,
)
