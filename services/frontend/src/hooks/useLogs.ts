import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/logs'
import { NetworkError } from '../api/client'
import { addPendingLog } from '../lib/pendingLogs'
import { dedupeInfinitePages } from '../lib/pagination'

const LOGS_PAGE_SIZE = 50

export function useEntityLogs(entityId: string | undefined) {
  return useInfiniteQuery({
    queryKey: ['logs', entityId],
    queryFn: ({ pageParam }) =>
      api.listEntityLogs(entityId as string, { limit: LOGS_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * LOGS_PAGE_SIZE : undefined,
    enabled: entityId !== undefined,
    select: dedupeInfinitePages,
  })
}

// Thrown instead of the real NetworkError when a log creation gets queued
// for background sync — lets callers show "queued" UI instead of a real
// error (see EntityDetailPage.tsx's createLog.mutate call sites).
export class LogQueuedError extends Error {
  localId: string

  constructor(localId: string) {
    super('Offline — this log entry has been queued and will sync automatically.')
    this.localId = localId
  }
}

export function useCreateLog() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: api.LogCreateInput) => {
      const localId = crypto.randomUUID()
      try {
        return await api.createLog(input, { localId })
      } catch (err) {
        if (!(err instanceof NetworkError)) throw err
        await addPendingLog({
          localId,
          input,
          entityId: input.entity_id,
          queuedAt: new Date().toISOString(),
          status: 'pending',
        })
        queryClient.invalidateQueries({ queryKey: ['pending-logs', input.entity_id] })
        throw new LogQueuedError(localId)
      }
    },
    onSuccess: (log) => {
      queryClient.invalidateQueries({ queryKey: ['logs', log.entity_id] })
      if (log.schedule_id) {
        queryClient.invalidateQueries({ queryKey: ['schedules', log.entity_id] })
        queryClient.invalidateQueries({ queryKey: ['due-soon'] })
        queryClient.invalidateQueries({ queryKey: ['schedule-calendar'] })
      }
    },
  })
}

// Editing/deleting a log can change which log satisfies a schedule (see
// core-api's _resync_schedule), so both mutations invalidate schedules/
// due-soon unconditionally rather than trying to know upfront whether the
// touched log was schedule-linked.
export function useUpdateLog(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: api.LogUpdateInput }) => api.updateLog(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['logs', entityId] })
      queryClient.invalidateQueries({ queryKey: ['schedules', entityId] })
      queryClient.invalidateQueries({ queryKey: ['due-soon'] })
      queryClient.invalidateQueries({ queryKey: ['schedule-calendar'] })
    },
  })
}

export function useDeleteLog(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteLog,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['logs', entityId] })
      queryClient.invalidateQueries({ queryKey: ['schedules', entityId] })
      queryClient.invalidateQueries({ queryKey: ['due-soon'] })
      queryClient.invalidateQueries({ queryKey: ['schedule-calendar'] })
    },
  })
}
