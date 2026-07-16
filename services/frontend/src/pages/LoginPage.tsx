import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useLogin, useSession } from '../hooks/useSession'

export function LoginPage() {
  const { data: session, isPending } = useSession()
  const login = useLogin()
  const [password, setPassword] = useState('')
  const location = useLocation()

  if (!isPending && session) {
    const redirectTo = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={redirectTo} replace />
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    login.mutate(password)
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-divider p-6"
      >
        <h1 className="text-xl font-semibold mb-1">ARIA</h1>
        <p className="text-sm text-subtle mb-4">Sign in to your household</p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Household password"
          autoFocus
          className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
        />
        {login.isError && (
          <p className="text-sm text-red-500 mb-3">
            {login.error instanceof ApiError ? login.error.message : 'Login failed'}
          </p>
        )}
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-2 font-medium disabled:opacity-50"
        >
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
