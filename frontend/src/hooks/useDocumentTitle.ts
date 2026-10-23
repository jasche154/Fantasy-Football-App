import { useEffect } from 'react';

export const APP_NAME = 'Gridiron Edge';

/** Sets the tab title as "<page> · Gridiron Edge". */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP_NAME}` : APP_NAME;
  }, [title]);
}
