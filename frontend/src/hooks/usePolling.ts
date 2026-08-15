import { useCallback, useEffect, useRef } from 'react'
export function usePolling(refresh: () => void | Promise<void>, intervalMs: number, enabled = true) {
  const running = useRef(false); const current = useRef(refresh); current.current = refresh
  const tick = useCallback(async () => { if (running.current || document.hidden) return; running.current = true; try { await current.current() } finally { running.current = false } }, [])
  useEffect(() => { if (!enabled) return; void tick(); const id = window.setInterval(() => void tick(), intervalMs); return () => window.clearInterval(id) }, [enabled, intervalMs, tick])
  return tick
}
