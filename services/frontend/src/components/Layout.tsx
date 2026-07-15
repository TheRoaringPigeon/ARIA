import { NavLink, Outlet } from 'react-router-dom'
import { useLogout, useSession } from '../hooks/useSession'

export function Layout() {
  const { data: session } = useSession()
  const logout = useLogout()

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium ${
      isActive
        ? 'bg-neutral-200 dark:bg-neutral-700'
        : 'text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800'
    }`

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 dark:border-neutral-700">
        <div className="mx-auto max-w-4xl px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-1">
            <span className="font-semibold mr-4">ARIA</span>
            <NavLink to="/" end className={linkClass}>
              Entities
            </NavLink>
            <NavLink to="/due-soon" className={linkClass}>
              What's Due
            </NavLink>
            <NavLink to="/health" className={linkClass}>
              Health
            </NavLink>
          </div>
          <div className="flex items-center gap-3 text-sm text-neutral-500">
            {session && <span>{session.user_name}</span>}
            <button
              type="button"
              className="rounded-md border border-neutral-300 dark:border-neutral-600 px-2 py-1 hover:bg-neutral-100 dark:hover:bg-neutral-800"
              onClick={() => logout.mutate()}
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl p-6">
        <Outlet />
      </main>
    </div>
  )
}
