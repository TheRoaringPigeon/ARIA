import { useSession } from '../hooks/useSession'
import { useTheme, type ThemeId } from '../context/ThemeContext'
import { useCurrentUser, useUpdateUser } from '../hooks/useUser'
import { HouseholdLocationCard } from '../components/HouseholdLocationCard'
import { HouseholdMembersCard } from '../components/HouseholdMembersCard'

export function ProfilePage() {
  const { data: session } = useSession()
  const { theme, setTheme, themes } = useTheme()
  const { data: user } = useCurrentUser()
  const updateUser = useUpdateUser()

  return (
    <div className="max-w-md flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Profile</h1>

      <div className="rounded-lg border border-divider p-6">
        <h2 className="text-sm font-semibold mb-1">Signed in as</h2>
        <p className="text-sm text-subtle">
          {session?.user_name}
          {session?.role && <span className="text-subtle"> ({session.role})</span>}
        </p>
      </div>

      <HouseholdLocationCard />

      <HouseholdMembersCard />

      <div className="rounded-lg border border-divider p-6">
        <h2 className="text-sm font-semibold mb-3">Theme</h2>
        <div className="flex flex-wrap gap-3">
          {themes.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTheme(t.id as ThemeId)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium ${
                theme === t.id ? 'border-primary bg-active' : 'border-line text-subtle hover:bg-surface-hover'
              }`}
            >
              <span className="w-4 h-4 rounded-full shrink-0" style={{ backgroundColor: t.primaryColor }} />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-divider p-6">
        <h2 className="text-sm font-semibold mb-1">Overdue items email</h2>
        <p className="text-sm text-subtle mb-3">
          Get a daily email listing anything overdue in your household.
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={user?.notify_overdue_email ?? false}
            onChange={(e) => updateUser.mutate({ notify_overdue_email: e.target.checked })}
          />
          Email me a daily overdue digest
        </label>
      </div>
    </div>
  )
}
