// ============================================================================
// IMS 2.0 - load-then-save guard for settings forms (audit L1-F01/F05, R43)
// ============================================================================
// A settings form may only be edited and saved once the STORED values are on
// screen. A failed read used to fall back to code defaults (or an empty form)
// with Save still enabled, so one press wrote defaults over the real
// configuration. Every settings form reads through this hook instead:
//   status 'loading' -> spinner; 'error' -> SettingsLoadError + Retry, no form;
//   'ready' -> `data` holds what the server returned and may be edited.
// A refresh that fails AFTER a successful load keeps the values already on
// screen (they are the last known / just-saved ones) and stays 'ready'.

import { useCallback, useEffect, useRef, useState } from 'react';

/** The one sentence every guarded settings form shows on a failed read. */
export const settingsLoadErrorText = (what: string) =>
  `Could not load the saved ${what}, so nothing here can be changed or saved. Press Retry.`;

export type LoadStatus = 'loading' | 'ready' | 'error';

export function useLoadedSettings<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<LoadStatus>('loading');
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });
  const seq = useRef(0);

  const run = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const next = await loaderRef.current();
      if (mine !== seq.current) return;
      setData(next);
      setStatus('ready');
    } catch {
      if (mine !== seq.current) return;
      setStatus((s) => (s === 'ready' ? s : 'error'));
    }
  }, []);

  /** Read again (Retry, or a refresh after a save). */
  const reload = useCallback(() => {
    setStatus((s) => (s === 'ready' ? s : 'loading'));
    return run();
  }, [run]);

  useEffect(() => {
    void run(); // the first read; status starts as 'loading'
    return () => {
      seq.current += 1; // an answer that lands after unmount is dropped
    };
  }, [run]);

  /** Edit the loaded values; a no-op until they are loaded. */
  const update = useCallback((fn: (prev: T) => T) => {
    setData((prev) => (prev === null ? prev : fn(prev)));
  }, []);

  return { data, status, reload, update, setData };
}
