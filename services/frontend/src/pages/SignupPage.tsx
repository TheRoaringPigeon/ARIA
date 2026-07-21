import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useSession, useSignup } from '../hooks/useSession'

export function SignupPage() {
  const { data: session, isPending } = useSession()
  const signup = useSignup()
  const [householdName, setHouseholdName] = useState('')
  const [city, setCity] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const location = useLocation()

  if (!isPending && session) {
    const redirectTo = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={redirectTo} replace />
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    signup.mutate({ householdName, name, email, password, city })
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-divider p-6"
      >
        <h1 className="text-xl font-semibold mb-1">Create a household</h1>
        <p className="text-sm text-subtle mb-4">You'll be its owner, signed in right away.</p>
        <input
          value={householdName}
          onChange={(e) => setHouseholdName(e.target.value)}
          placeholder="Household name"
          autoFocus
          className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
        />
        <input
          value={city}
          onChange={(e) => setCity(e.target.value)}
          placeholder="City (optional — used to default weather answers in chat)"
          className="w-full rounded-md border border-line bg-transparent px-3 py-2 mb-3"
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Your name"
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
        {signup.isError && (
          <p className="text-sm text-red-500 mb-3">
            {signup.error instanceof ApiError ? signup.error.message : 'Signup failed'}
          </p>
        )}
        <button
          type="submit"
          disabled={signup.isPending}
          className="w-full rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-2 font-medium disabled:opacity-50"
        >
          {signup.isPending ? 'Creating…' : 'Create household'}
        </button>
        <p className="mt-3 text-sm text-subtle text-center">
          Already have a household? <Link to="/login" className="text-primary hover:underline">Sign in</Link>
        </p>
      </form>
    </div>
  )
}
