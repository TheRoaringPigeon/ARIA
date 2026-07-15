import { useQuery } from '@tanstack/react-query'

const CORE_API_URL = import.meta.env.VITE_CORE_API_URL ?? 'http://localhost:8000'
const AI_SERVICE_URL = import.meta.env.VITE_AI_SERVICE_URL ?? 'http://localhost:8001'

type HealthResponse = Record<string, string>

function useServiceHealth(name: string, url: string) {
  return useQuery({
    queryKey: ['health', name],
    queryFn: async (): Promise<HealthResponse> => {
      const res = await fetch(`${url}/health`)
      if (!res.ok) throw new Error(`${name} returned ${res.status}`)
      return res.json()
    },
    retry: false,
  })
}

function ServiceStatusCard({ name, url }: { name: string; url: string }) {
  const { data, isPending, isError, error } = useServiceHealth(name, url)

  const dotColor = isPending ? 'bg-neutral-400' : isError ? 'bg-red-500' : 'bg-green-500'

  return (
    <div className="rounded-lg border border-neutral-200 dark:border-neutral-700 p-4">
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${dotColor}`} />
        <h2 className="font-medium">{name}</h2>
      </div>
      <p className="mt-1 text-sm text-neutral-500">{url}</p>
      {isError && <p className="mt-2 text-sm text-red-500">{(error as Error).message}</p>}
      {data && (
        <pre className="mt-2 text-xs bg-neutral-100 dark:bg-neutral-800 rounded p-2 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function HealthPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Service health</h1>
      <p className="mt-1 text-neutral-500">
        core-api must stay healthy even if ai-service is down — that's the point.
      </p>
      <div className="mt-6 grid gap-4">
        <ServiceStatusCard name="core-api" url={CORE_API_URL} />
        <ServiceStatusCard name="ai-service" url={AI_SERVICE_URL} />
      </div>
    </div>
  )
}
