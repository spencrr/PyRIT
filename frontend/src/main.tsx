import React from 'react'
import ReactDOM from 'react-dom/client'

import AppRouter from './AppRouter'
import { AuthProvider } from './auth/AuthProvider'
import { CompatibilityGate } from './components/CompatibilityGate'
import { ThemeProvider } from './hooks/useTheme'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <CompatibilityGate>
          <AppRouter />
        </CompatibilityGate>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
