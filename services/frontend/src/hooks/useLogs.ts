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
