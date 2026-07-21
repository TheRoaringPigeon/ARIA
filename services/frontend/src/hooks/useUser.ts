import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/users'

export function useCurrentUser() {
  return useQuery({
    queryKey: ['user'],
    queryFn: api.getCurrentUser,
    retry: false,
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.updateCurrentUser,
    onSuccess: (user) => {
      queryClient.setQueryData(['user'], user)
    },
  })
}
