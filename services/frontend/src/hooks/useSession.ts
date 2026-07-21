import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { acceptInvite, getSession, login, logout, signup } from '../api/auth'

export function useSession() {
  return useQuery({
    queryKey: ['session'],
    queryFn: getSession,
    retry: false,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => login(email, password),
    onSuccess: (session) => {
      queryClient.setQueryData(['session'], session)
    },
  })
}

export function useSignup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      householdName,
      name,
      email,
      password,
      city,
    }: {
      householdName: string
      name: string
      email: string
      password: string
      city?: string
    }) => signup(householdName, name, email, password, city),
    onSuccess: (session) => {
      queryClient.setQueryData(['session'], session)
    },
  })
}

export function useAcceptInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      token,
      name,
      email,
      password,
    }: {
      token: string
      name: string
      email: string
      password: string
    }) => acceptInvite(token, name, email, password),
    onSuccess: (session) => {
      queryClient.setQueryData(['session'], session)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.setQueryData(['session'], null)
      queryClient.clear()
    },
  })
}
