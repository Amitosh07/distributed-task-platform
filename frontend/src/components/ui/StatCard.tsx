import React, { ReactNode } from 'react'

export interface StatCardProps {
  label: string
  value: string | number
  subValue?: string
  icon?: ReactNode
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'info'
  className?: string
}

export function StatCard({
  label,
  value,
  subValue,
  icon,
  tone = 'default',
  className = '',
}: StatCardProps) {
  const toneClasses = {
    default: 'text-slate-900',
    success: 'text-emerald-700',
    warning: 'text-amber-700',
    danger: 'text-rose-700',
    info: 'text-sky-700',
  }[tone]

  return (
    <div
      className={`rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm transition-all hover:border-slate-300 ${className}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </span>
        {icon && <div className="text-slate-400">{icon}</div>}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className={`font-mono text-3xl font-bold tracking-tight ${toneClasses}`}>
          {value}
        </span>
        {subValue && <span className="text-xs font-medium text-slate-500">{subValue}</span>}
      </div>
    </div>
  )
}
