import { useState, type FormEvent } from 'react'
import { useHousehold, useUpdateHousehold } from '../hooks/useHousehold'
import { useSession } from '../hooks/useSession'
import { ApiError } from '../api/client'

export function HouseholdLocationCard() {
  const { data: session } = useSession()
  const { data: household } = useHousehold()
  const updateHousehold = useUpdateHousehold()
  const isOwner = session?.role === 'owner'
  const [editing, setEditing] = useState(false)
  const [city, setCity] = useState('')

  function startEditing() {
    setCity(household?.city ?? '')
    setEditing(true)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    updateHousehold.mutate({ city: city.trim() || null }, { onSuccess: () => setEditing(false) })
  }

  return (
    <div className="rounded-lg border border-divider p-6">
      <h2 className="text-sm font-semibold mb-1">Household location</h2>
      <p className="text-xs text-subtle mb-3">
        Used as the default location when you ask ARIA about the weather without naming a place.
      </p>

      {editing ? (
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <input
            autoFocus
            value={city}
            onChange={(e) => setCity(e.target.value)}
            placeholder="City (e.g. Lizella, GA)"
            className="flex-1 rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
          />
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
        </form>
      ) : (
        <div className="flex items-center justify-between">
          <span className="text-sm text-subtle">{household?.city ?? 'Not set'}</span>
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
