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
        className="w-full max-w-sm rounded-lg border border-neutral-200 dark:border-neutral-700 p-6"
      >
        <h1 className="text-xl font-semibold mb-1">ARIA</h1>
        <p className="text-sm text-neutral-500 mb-4">Sign in to your household</p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Household password"
          autoFocus
          className="w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-3 py-2 mb-3"
        />
        {login.isError && (
          <p className="text-sm text-red-500 mb-3">
            {login.error instanceof ApiError ? login.error.message : 'Login failed'}
          </p>
        )}
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded-md bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-3 py-2 font-medium disabled:opacity-50"
        >
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
