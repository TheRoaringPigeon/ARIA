import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getCurrentUser, updateCurrentUser } from '../api/users'

export function usePinnedEntities() {
  const queryClient = useQueryClient()
  const { data: user } = useQuery({ queryKey: ['user'], queryFn: getCurrentUser, retry: false })
  const [pinnedIds, setPinnedIds] = useState<string[]>([])

  useEffect(() => {
    if (user) setPinnedIds(user.pinned_entity_ids)
  }, [user])

  function togglePin(entityId: string) {
    const next = pinnedIds.includes(entityId)
      ? pinnedIds.filter((id) => id !== entityId)
      : [...pinnedIds, entityId]
    setPinnedIds(next)
    // Best-effort, same tradeoff as ThemeContext's setTheme — a failed save
    // just doesn't stick until the next successful one.
    updateCurrentUser({ pinned_entity_ids: next })
      .then((updated) => queryClient.setQueryData(['user'], updated))
      .catch(() => {})
  }

  return { pinnedIds: new Set(pinnedIds), togglePin }
}
