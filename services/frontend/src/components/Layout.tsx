import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useLogout, useSession } from '../hooks/useSession'
import { useLogSyncListener } from '../hooks/useLogSyncListener'
import { OfflineBanner } from './OfflineBanner'
import { OverdueBanner } from './OverdueBanner'
import { SearchBar } from './SearchBar'

export function Layout() {
  const { data: session } = useSession()
  const logout = useLogout()
  useLogSyncListener()

  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium ${
      isActive
        ? 'bg-active'
        : 'text-subtle hover:bg-surface-hover'
    }`

  const closeMenu = () => setMenuOpen(false)

  const navLinks = (
    <>
      <NavLink to="/" end className={linkClass} onClick={closeMenu}>
        Entities
      </NavLink>
      <NavLink to="/due-soon" className={linkClass} onClick={closeMenu}>
        What's Due
      </NavLink>
      <NavLink to="/chat" className={linkClass} onClick={closeMenu}>
        Chat
      </NavLink>
      {session?.role === 'owner' && (
        <NavLink to="/health" className={linkClass} onClick={closeMenu}>
          Health
        </NavLink>
      )}
      {session?.role === 'owner' && (
        <NavLink to="/trash" className={linkClass} onClick={closeMenu}>
          Trash
        </NavLink>
      )}
    </>
  )

  return (
    <div className="min-h-screen">
      <OfflineBanner />
      <OverdueBanner />
      <header className="border-b border-divider" ref={menuRef}>
        <div className="mx-auto max-w-4xl px-6 py-3 flex items-center gap-4">
          <span className="font-semibold">ARIA</span>
          <div className="hidden md:flex items-center gap-4 flex-1">
            <div className="flex items-center gap-1">{navLinks}</div>
            <div className="flex-1 flex justify-center">
              <SearchBar />
            </div>
            <div className="flex items-center gap-3 text-sm text-subtle shrink-0">
              {session && (
                <NavLink to="/profile" className={linkClass}>
                  {session.user_name}
                </NavLink>
              )}
              <button
                type="button"
                className="rounded-md border border-line px-2 py-1 hover:bg-surface-hover"
                onClick={() => logout.mutate()}
              >
                Log out
              </button>
            </div>
          </div>
          <button
            type="button"
            className="md:hidden ml-auto rounded-md border border-line px-2 py-1 text-lg leading-none"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
          >
            {menuOpen ? '✕' : '☰'}
          </button>
        </div>
        <div className="md:hidden px-6 pb-3">
          <SearchBar />
        </div>
        {menuOpen && (
          <div className="md:hidden border-t border-divider px-6 py-3 flex flex-col gap-1">
            {navLinks}
            <div className="border-t border-divider mt-2 pt-2 flex items-center justify-between text-sm text-subtle">
              {session && (
                <NavLink to="/profile" className={linkClass} onClick={closeMenu}>
                  {session.user_name}
                </NavLink>
              )}
              <button
                type="button"
                className="rounded-md border border-line px-2 py-1 hover:bg-surface-hover"
                onClick={() => logout.mutate()}
              >
                Log out
              </button>
            </div>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-4xl p-6">
        <Outlet />
      </main>
    </div>
  )
}
