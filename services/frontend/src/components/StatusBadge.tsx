export function StatusBadge({ status, archived }: { status: string; archived?: boolean }) {
  const label = archived ? 'archived' : status
  const color = archived
    ? 'bg-active text-subtle'
    : status === 'active'
      ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
      : 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300'

  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {label.replace(/_/g, ' ')}
    </span>
  )
}
