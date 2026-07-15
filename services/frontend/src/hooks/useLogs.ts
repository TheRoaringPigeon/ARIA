import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/logs'

export function useEntityLogs(entityId: string | undefined) {
  return useQuery({
    queryKey: ['logs', entityId],
    queryFn: () => api.listEntityLogs(entityId as string),
    enabled: entityId !== undefined,
  })
}

export function useCreateLog() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createLog,
    onSuccess: (log) => {
      queryClient.invalidateQueries({ queryKey: ['logs', log.entity_id] })
      if (log.schedule_id) {
        queryClient.invalidateQueries({ queryKey: ['schedules', log.entity_id] })
        queryClient.invalidateQueries({ queryKey: ['due-soon'] })
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
    },
  })
}
