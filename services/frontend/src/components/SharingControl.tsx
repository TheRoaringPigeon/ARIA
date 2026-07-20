import type { SharedWith } from '../api/types'
import { useMembers } from '../hooks/useHousehold'
import { useSession } from '../hooks/useSession'

// Read-only "who can see this" indicator for detail/list views — editing
// always happens through <SharingControl> itself, on a form.
export function SharedWithLabel({ sharedWith }: { sharedWith: SharedWith }) {
  if (sharedWith === 'household') return <span>Shared with household</span>
  const count = sharedWith.length
  return (
    <span>
      Shared with {count} {count === 1 ? 'member' : 'members'}
    </span>
  )
}

interface Props {
  value: SharedWith
  onChange: (value: SharedWith) => void
  disabled?: boolean
}

// Reused by EntityForm and DocumentUploadForm so the two can't drift on how
// this control looks or behaves — one "who can see this" picker for every
// top-level record that has a `shared_with` field.
export function SharingControl({ value, onChange, disabled }: Props) {
  const { data: session } = useSession()
  const { data: members } = useMembers()
  const isHousehold = value === 'household'

  // The current user always has access regardless of shared_with (see
  // aria_auth.sharing.has_shared_access), so there's no reason to show
  // them as a togglable option.
  const otherMembers = (members ?? []).filter((m) => m.id !== session?.user_id)

  function toggleMember(memberId: string, checked: boolean) {
    const current = Array.isArray(value) ? value : []
    onChange(checked ? [...current, memberId] : current.filter((id) => id !== memberId))
  }

  return (
    <fieldset className="rounded-md border border-divider p-3" disabled={disabled}>
      <legend className="text-sm font-medium px-1">Shared with</legend>
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            checked={isHousehold}
            onChange={() => onChange('household')}
            disabled={disabled}
          />
          Whole household
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            checked={!isHousehold}
            onChange={() => onChange([])}
            disabled={disabled}
          />
          Specific members
        </label>
        {!isHousehold && (
          <div className="ml-6 flex flex-col gap-1">
            {otherMembers.length === 0 && (
              <p className="text-sm text-subtle">No other members in this household yet.</p>
            )}
            {otherMembers.map((member) => (
              <label key={member.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={Array.isArray(value) && value.includes(member.id)}
                  onChange={(e) => toggleMember(member.id, e.target.checked)}
                  disabled={disabled}
                />
                {member.name} ({member.email})
              </label>
            ))}
          </div>
        )}
      </div>
      {disabled && (
        <p className="mt-2 text-xs text-subtle">
          Only the creator or the household owner can change sharing.
        </p>
      )}
    </fieldset>
  )
}
