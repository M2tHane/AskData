import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { fetchMe, logout } from './api/hooks'
import { navFor, ROUTES } from './lib/nav'
import { useAuth } from './state/auth'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { DatasourcesPage } from './pages/DatasourcesPage'
import { EvalPage } from './pages/EvalPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { LoginPage } from './pages/LoginPage'
import { LogsPage } from './pages/LogsPage'
import { McpPage } from './pages/McpPage'
import { MetricsPage } from './pages/MetricsPage'
import { ModelsPage } from './pages/ModelsPage'
import { OrgPage } from './pages/OrgPage'
import { PermissionsPage } from './pages/PermissionsPage'
import { PromptsPage } from './pages/PromptsPage'
import { UsagePage } from './pages/UsagePage'
import { SemanticPage } from './pages/SemanticPage'
import { Shell } from './pages/Shell'

function Chat() {
  const { id } = useParams()
  const navigate = useNavigate()
  return <ChatPage key={id ?? 'none'} conversationId={id ? Number(id) : null} onSelect={(next) => navigate(next ? `/chat/${next}` : '/chat')} />
}

export function App() {
  const { access, user, setUser, clear, refresh } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()

  useEffect(() => {
    if (access && !user) fetchMe().then(setUser).catch(() => clear())
  }, [access, user, setUser, clear])

  if (!access) return <LoginPage />
  if (!user) return null

  const { routes } = navFor(user.role)
  const active = routes.find((r) => location.pathname.startsWith(r.path)) ?? routes[0]
  if (!active) return null

  const pages: Record<string, JSX.Element> = {
    dashboard: <DashboardPage />,
    datasources: <DatasourcesPage />,
    permissions: <PermissionsPage teamId={user.team_id ?? null} />,
    org: <OrgPage />,
    models: <ModelsPage role={user.role} teamId={user.team_id ?? null} />,
    prompts: <PromptsPage />,
    usage: <UsagePage />,
    semantic: <SemanticPage role={user.role} />,
    metrics: <MetricsPage role={user.role} />,
    feedback: <FeedbackPage />,
    logs: <LogsPage role={user.role} />,
    eval: <EvalPage role={user.role} />,
    mcp: <McpPage />,
  }

  return (
    <Shell
      user={user}
      active={active}
      onNavigate={navigate}
      onLogout={() => {
        if (refresh) void logout(refresh).catch(() => undefined)
        clear()
        qc.clear()
      }}
    >
      <Routes>
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:id" element={<Chat />} />
        {routes
          .filter((r) => r.id !== 'chat')
          .map((r) => (
            <Route key={r.id} path={r.path} element={pages[r.id]} />
          ))}
        <Route path="*" element={<Navigate to={ROUTES[0]?.path ?? '/chat'} replace />} />
      </Routes>
    </Shell>
  )
}
