import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/schedules'
import type { EntityDomain } from '../domains'

export function useEntitySchedules(entityId: string | undefined) {
  return useQuery({
    queryKey: ['schedules', entityId],
    queryFn: () => api.listEntitySchedules(entityId as string),
    enabled: entityId !== undefined,
  })
}

export function useCreateSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createSchedule,
    onSuccess: (schedule) => {
      queryClient.invalidateQueries({ queryKey: ['schedules', schedule.entity_id] })
      queryClient.invalidateQueries({ queryKey: ['due-soon'] })
    },
  })
}

export function useUpdateSchedule(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: api.ScheduleUpdateInput }) =>
      api.updateSchedule(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules', entityId] })
      queryClient.invalidateQueries({ queryKey: ['due-soon'] })
    },
  })
}

export function useDeleteSchedule(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules', entityId] })
      queryClient.invalidateQueries({ queryKey: ['due-soon'] })
    },
  })
}

export function useDueSoon(withinDays?: number, domain?: EntityDomain) {
  return useQuery({
    queryKey: ['due-soon', withinDays ?? 30, domain ?? null],
    queryFn: () => api.listDueSoon(withinDays, domain),
  })
}
