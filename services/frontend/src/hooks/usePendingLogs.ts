import { useQuery } from '@tanstack/react-query'
import { getPendingLogsForEntity } from '../lib/pendingLogs'

export function usePendingLogs(entityId: string | undefined) {
  return useQuery({
    queryKey: ['pending-logs', entityId],
    queryFn: () => getPendingLogsForEntity(entityId as string),
    enabled: entityId !== undefined,
  })
}
