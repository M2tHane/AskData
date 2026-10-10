import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { ToastProvider } from './components/Toast'
import { initTheme } from './state/theme'
import './styles/tokens.css'
import './styles/legacy-aliases.css'
import './styles/components.css'
import './styles/app.css'
import './styles/chat.css'
import './styles/login.css'
import './styles/pages/semantic.css'
import './styles/pages/metrics.css'
import './styles/pages/feedback.css'
import './styles/pages/logs.css'
import './styles/pages/eval.css'
import './styles/pages/org.css'
import './styles/pages/models.css'
import './styles/pages/usage.css'
import './styles/pages/prompts.css'
import './styles/pages/permissions.css'

initTheme()

const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
