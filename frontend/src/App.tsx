import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Outlet, Route, BrowserRouter as Router, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import { Layout } from './components/Layout'
import { Chat } from './routes/Chat'
import { Documents } from './routes/Documents'
import { Login } from './routes/Login'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The API client already refreshes once and replays on a 401. Retrying
      // on top of that turns one expired token into several requests and hides
      // real failures behind a delay.
      retry: false,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})

function RequireAuth() {
  const { user, loading } = useAuth()

  // Render nothing until the session restore settles. Redirecting during that
  // window bounces an authenticated user to the login screen on every reload.
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />

  return (
    <Layout>
      <Outlet />
    </Layout>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth />}>
              <Route path="/chat" element={<Chat />} />
              <Route path="/documents" element={<Documents />} />
            </Route>
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </AuthProvider>
      </Router>
    </QueryClientProvider>
  )
}
