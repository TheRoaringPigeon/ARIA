import { useState, type FormEvent } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAcceptInvite, useSession } from '../hooks/useSession'

export function AcceptInvitePage() {
  const { token } = useParams<{ token: string }>()
  const { data: session, isPending: sessionPending } = useSession()
  const acceptInvite = useAcceptInvite()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  if (!sessionPending && session) {
    return <Navigate to="/" replace />
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!token) return
    acceptInvite.mutate({ token, name, email, password })
  }

  const isInvalidOrExpired =
    acceptInvite.isError &&
    acceptInvite.error instanceof ApiError &&
    (acceptInvite.error.status === 404 || acceptInvite.error.status === 410)

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-lg border border-divider p-6">
        <h1 className="text-xl font-semibold mb-1">Join a household</h1>
        <p className="text-sm text-subtle mb-4">You've been invited as a member.</p>

        {isInvalidOrExpired ? (
          <p className="text-sm text-red-500">
            This invite is invalid or has expired — ask the household owner for a new one.
          </p>
        ) : (
          <form onSubmit={handleSubmit}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Your name"
              autoFocus
              className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
            />
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email"
              className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
            />
            {acceptInvite.isError && (
              <p className="text-sm text-red-500 mb-3">
                {acceptInvite.error instanceof ApiError ? acceptInvite.error.message : 'Failed to join'}
              </p>
            )}
            <button
              type="submit"
              disabled={acceptInvite.isPending}
              className="w-full rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-2 font-medium disabled:opacity-50"
            >
              {acceptInvite.isPending ? 'Joining…' : 'Join household'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
