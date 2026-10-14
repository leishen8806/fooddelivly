import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

type QueryValue = string | number | null | undefined;

export function useQueryState<T extends Record<string, QueryValue>>(defaults: T) {
  const [searchParams, setSearchParams] = useSearchParams();
  const state = useMemo(() => {
    const next = { ...defaults };
    (Object.keys(defaults) as Array<keyof T>).forEach((key) => {
      const value = searchParams.get(String(key));
      if (value !== null) next[key] = value as T[keyof T];
    });
    return next;
  }, [defaults, searchParams]);

  const setQueryState = useCallback((patch: Partial<T>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (value === null || value === undefined || value === '') next.delete(key);
      else next.set(key, String(value));
    });
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  return [state, setQueryState] as const;
}

