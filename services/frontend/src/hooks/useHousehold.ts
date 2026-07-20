import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/households'

export function useMembers() {
  return useQuery({
    queryKey: ['members'],
    queryFn: api.listMembers,
  })
}

export function useInvites() {
  return useQuery({
    queryKey: ['invites'],
    queryFn: api.listInvites,
  })
}

export function useCreateInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createInvite,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['invites'] }),
  })
}

export function useRevokeInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.revokeInvite,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['invites'] }),
  })
}
