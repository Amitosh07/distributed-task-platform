import React from 'react'

export function LoadingSkeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-lg bg-slate-200/80 ${className}`}
    />
  )
}

export function TableSkeleton({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="w-full space-y-3 py-2">
      <div className="flex gap-4 border-b border-slate-100 pb-3">
        {Array.from({ length: cols }).map((_, i) => (
          <LoadingSkeleton key={`th-${i}`} className="h-4 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={`tr-${r}`} className="flex items-center gap-4 py-2">
          {Array.from({ length: cols }).map((_, c) => (
            <LoadingSkeleton key={`td-${r}-${c}`} className="h-5 flex-1" />
          ))}
        </div>
      ))}
    </div>
  )
}
