import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { AppProviders } from '../App';
import { meta } from './fixtures';
import { json, type MockRoute } from './mockFetch';

/** `/api/meta` is fetched by the provider tree, so most tests include it. */
export const META_ROUTE: MockRoute = { path: '/api/meta', reply: json(meta()) };

export function setHash(hash: string): void {
  window.history.replaceState(null, '', `/${hash}`);
}

export function renderWithProviders(ui: ReactElement, options: { hash?: string } = {}): RenderResult {
  if (options.hash) setHash(options.hash);
  return render(<AppProviders>{ui}</AppProviders>);
}
