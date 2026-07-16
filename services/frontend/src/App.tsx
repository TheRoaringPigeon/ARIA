import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Layout } from './components/Layout'
import { useSession } from './hooks/useSession'
import { DueSoonPage } from './pages/DueSoonPage'
import { EntityDetailPage } from './pages/EntityDetailPage'
import { EntityListPage } from './pages/EntityListPage'
import { HealthPage } from './pages/HealthPage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'

function RequireAuth({ children }: { children: ReactNode }) {
  const { data: session, isPending, isError } = useSession()
  const location = useLocation()

  if (isPending) {
    return <div className="p-6 text-subtle">Loading…</div>
  }
  if (isError || !session) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  return <>{children}</>
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<EntityListPage />} />
        <Route path="/entities/:entityId" element={<EntityDetailPage />} />
        <Route path="/due-soon" element={<DueSoonPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/profile" element={<ProfilePage />} />
      </Route>
    </Routes>
  )
}

export default App
