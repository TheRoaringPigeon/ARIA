import { useState, type FormEvent } from 'react'
import { useHousehold, useUpdateHousehold } from '../hooks/useHousehold'
import { useSession } from '../hooks/useSession'
import { ApiError } from '../api/client'

const TIMEZONES = Intl.supportedValuesOf('timeZone')

export function HouseholdSettingsCard() {
  const { data: session } = useSession()
  const { data: household } = useHousehold()
  const updateHousehold = useUpdateHousehold()
  const isOwner = session?.role === 'owner'
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [timezone, setTimezone] = useState('')

  function startEditing() {
    setName(household?.name ?? '')
    // Suggest the browser's own zone as a starting point when nothing is
    // set yet — not saved until the owner actually submits.
    setTimezone(household?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone)
    setEditing(true)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    updateHousehold.mutate(
      { name: name.trim(), timezone: timezone || null },
      { onSuccess: () => setEditing(false) }
    )
  }

  return (
    <div className="rounded-lg border border-divider p-6">
      <h2 className="text-sm font-semibold mb-1">Household settings</h2>
      <p className="text-xs text-subtle mb-3">
        The timezone determines what counts as "today" for due dates, the calendar, and the
        overdue digest.
      </p>

      {editing ? (
        <form onSubmit={handleSubmit} className="flex flex-col gap-2">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Household name"
            required
            className="rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
          />
          <select
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            className="rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
          >
            <option value="">Not set (defaults to UTC)</option>
            {TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={updateHousehold.isPending}
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium disabled:opacity-50"
            >
              {updateHousehold.isPending ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-sm text-subtle hover:underline shrink-0"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="flex items-center justify-between">
          <div className="text-sm text-subtle">
            <p>{household?.name}</p>
            <p>{household?.timezone ?? 'Timezone not set (UTC)'}</p>
          </div>
          {isOwner && (
            <button type="button" onClick={startEditing} className="text-sm text-subtle hover:underline">
              Edit
            </button>
          )}
        </div>
      )}

      {updateHousehold.isError && (
        <p className="mt-2 text-xs text-red-500">
          {updateHousehold.error instanceof ApiError ? updateHousehold.error.message : "Couldn't save — try again."}
        </p>
      )}
    </div>
  )
}
