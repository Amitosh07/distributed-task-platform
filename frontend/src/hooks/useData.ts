import { useCallback, useEffect, useState } from 'react'
import { usePolling } from './usePolling'

export function useData<T>(
  load: () => Promise<T>,
  deps: unknown[],
  ms = 0,
  enabled = true,
  pollWhile: (value: T | undefined) => boolean = () => true,
) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setError('')
      const res = await load()
      setData(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load data')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    void refresh()
  }, [refresh])

  usePolling(refresh, ms, enabled && ms > 0 && pollWhile(data))

  return { data, error, loading, refresh, setData }
}

export const isTerminalStatus = (s: string) =>
  ['SUCCESS', 'FAILED', 'DEAD_LETTER', 'CANCELLED', 'TIMED_OUT', 'SKIPPED'].includes(s)
