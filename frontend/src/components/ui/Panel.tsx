import React, { ReactNode } from 'react'

export interface PanelProps {
  children: ReactNode
  title?: string
  subtitle?: string
  headerAction?: ReactNode
  className?: string
  bodyClassName?: string
  noPadding?: boolean
}

export function Panel({
  children,
  title,
  subtitle,
  headerAction,
  className = '',
  bodyClassName = '',
  noPadding = false,
}: PanelProps) {
  return (
    <section
      className={`rounded-xl border border-slate-200/80 bg-white shadow-sm transition-all ${className}`}
    >
      {(title || headerAction) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            {title && <h3 className="text-base font-semibold text-slate-900">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {headerAction && <div>{headerAction}</div>}
        </div>
      )}
      <div className={noPadding ? bodyClassName : `p-5 ${bodyClassName}`}>{children}</div>
    </section>
  )
}
