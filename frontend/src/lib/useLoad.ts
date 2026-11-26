import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "@/lib/errors";

/**
 * Load data for a page section and keep what a failed load needs: the error
 * text and a way to try again (instead of a toast and a spinner forever).
 */
export function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);
  const reload = useCallback(async () => {
    const run = ++latest.current;
    setError(null);
    try {
      const value = await fetcher();
      if (run === latest.current) setData(value);
    } catch (err) {
      if (run === latest.current) setError(errorText(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, setData, error, reload };
}
