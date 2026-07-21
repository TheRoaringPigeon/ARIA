import { useQueryClient } from '@tanstack/react-query'
import { useCreateLog } from '../hooks/useLogs'
import { usePendingLogs } from '../hooks/usePendingLogs'
import { removePendingLog, type PendingLogRecord } from '../lib/pendingLogs'

interface Props {
  entityId: string
}

// Deliberately a separate list from the real ['logs', entityId] history
// below it, not merged in — these entries don't have a server-assigned id
// yet and may still fail, so they get their own clearly-labeled section
// rather than a fake/temp entry spliced into real history.
export function PendingLogList({ entityId }: Props) {
  const pendingQuery = usePendingLogs(entityId)
  const createLog = useCreateLog()
  const queryClient = useQueryClient()

  const pending = pendingQuery.data ?? []
  if (pending.length === 0) return null

  async function handleDiscard(localId: string) {
    await removePendingLog(localId)
    queryClient.invalidateQueries({ queryKey: ['pending-logs', entityId] })
  }

  function handleRetry(record: PendingLogRecord) {
    createLog.mutate(record.input, {
      onSuccess: async () => {
        await removePendingLog(record.localId)
        queryClient.invalidateQueries({ queryKey: ['pending-logs', entityId] })
      },
    })
  }

  return (
    <div className="mt-3 grid gap-2">
      <p className="text-sm font-medium text-subtle">Pending sync ({pending.length})</p>
      {pending.map((record) => (
        <div key={record.localId} className="rounded-lg border border-dashed border-line p-3">
          <div className="flex items-start justify-between gap-3">
            <p className="font-medium">{record.input.title}</p>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${
                record.status === 'failed'
                  ? 'bg-red-500/10 text-red-500'
                  : 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
              }`}
            >
              {record.status === 'failed' ? 'Failed to sync' : 'Pending sync'}
            </span>
          </div>
          <p className="text-sm text-subtle">
            {record.input.occurred_at} · {record.input.type}
          </p>
          {record.status === 'failed' && (
            <>
              <p className="mt-1 text-sm text-red-500">{record.errorMessage}</p>
              <div className="mt-2 flex gap-3">
                <button
                  type="button"
                  onClick={() => handleRetry(record)}
                  className="text-sm text-subtle hover:underline"
                >
                  Retry
                </button>
                <button
                  type="button"
                  onClick={() => handleDiscard(record.localId)}
                  className="text-sm text-red-500 hover:underline"
                >
                  Discard
                </button>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  )
}
