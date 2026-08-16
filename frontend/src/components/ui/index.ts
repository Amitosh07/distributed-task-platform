export * from './Badge'
export * from './Button'
export * from './Panel'
export * from './StatCard'
export * from './Modal'
export * from './EmptyState'
export * from './LoadingSkeleton'

export const formatDate = (date: string | null) =>
  date
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'medium',
      }).format(new Date(date))
    : '—'

export const formatAge = (date: string | null) => {
  if (!date) return '—'
  const seconds = Math.max(0, (Date.now() - new Date(date).getTime()) / 1000)
  if (seconds < 60) return `${seconds.toFixed(1)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}
