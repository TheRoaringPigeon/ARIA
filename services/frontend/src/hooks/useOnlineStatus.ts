import { useEffect, useState } from 'react'

// navigator.onLine only reflects "has a network interface," not "can reach
// core-api" — it's good enough to drive a cosmetic banner, but the actual
// queue/replay decision (see api/client.ts's NetworkError) keys off real
// fetch() failures instead, which catch a genuinely unreachable API even
// when this reports true.
export function useOnlineStatus(): boolean {
  const [isOnline, setIsOnline] = useState(() => navigator.onLine)

  useEffect(() => {
    const handleOnline = () => setIsOnline(true)
    const handleOffline = () => setIsOnline(false)
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  return isOnline
}
