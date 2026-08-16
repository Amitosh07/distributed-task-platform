import React from 'react'

export interface BadgeProps {
  status: string
  size?: 'sm' | 'md'
  className?: string
}

export const getStatusStyle = (status: string) => {
  switch (status.toUpperCase()) {
    case 'SUCCESS':
    case 'ACTIVE':
      return 'bg-emerald-500/10 text-emerald-700 border-emerald-500/20'
    case 'RUNNING':
      return 'bg-sky-500/10 text-sky-700 border-sky-500/20 font-semibold animate-pulse'
    case 'QUEUED':
    case 'PENDING':
    case 'READY':
      return 'bg-amber-500/10 text-amber-700 border-amber-500/20'
    case 'RETRY_WAIT':
      return 'bg-amber-500/15 text-amber-800 border-amber-500/30'
    case 'FAILED':
    case 'DEAD_LETTER':
    case 'STALE':
      return 'bg-rose-500/10 text-rose-700 border-rose-500/20'
    case 'CANCELLED':
    case 'STOPPED':
    case 'SKIPPED':
      return 'bg-slate-500/10 text-slate-600 border-slate-500/20'
    case 'TIMED_OUT':
      return 'bg-orange-500/10 text-orange-700 border-orange-500/20'
    default:
      return 'bg-slate-500/10 text-slate-700 border-slate-500/20'
  }
}

export function Badge({ status, size = 'md', className = '' }: BadgeProps) {
  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-[11px]' : 'px-2.5 py-0.5 text-xs'
  const style = getStatusStyle(status)

  return (
    <span
      className={`inline-flex items-center rounded-full border font-mono font-medium tracking-tight ${sizeClasses} ${style} ${className}`}
    >
      <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {status.replace(/_/g, ' ')}
    </span>
  )
}
