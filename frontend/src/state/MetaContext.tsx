import { createContext, useContext, type ReactNode } from 'react';
import { getMeta } from '../api/data';
import type { Meta } from '../api/types';
import { useAsync, type AsyncResult } from '../hooks/useAsync';

const MetaContext = createContext<AsyncResult<Meta> | null>(null);

/** Loads `/api/meta` once for the whole app (seasons, latest week, integrations). */
export function MetaProvider({ children }: { children: ReactNode }) {
  const meta = useAsync((signal) => getMeta(signal), []);
  return <MetaContext.Provider value={meta}>{children}</MetaContext.Provider>;
}

export function useMeta(): AsyncResult<Meta> {
  const context = useContext(MetaContext);
  if (!context) throw new Error('useMeta must be used inside <MetaProvider>.');
  return context;
}
