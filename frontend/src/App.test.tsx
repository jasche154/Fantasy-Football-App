import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { navigate } from './router/useHashLocation';
import { json, mockFetch } from './test/mockFetch';
import { META_ROUTE, setHash } from './test/render';

function mockEverything() {
  return mockFetch([
    META_ROUTE,
    { path: /^\/api\/insights\/(buy-low|sell-high)$/, reply: json({ season: 2025, through_week: 7, scoring: 'ppr', results: [] }) },
    { path: '/api/insights/trending', reply: json({ results: [] }) },
    { path: '/api/rankings', reply: json({ season: 2025, through_week: 7, scoring: 'ppr', weeks_remaining: 10, results: [] }) },
    { path: '/api/leaderboards/metrics', reply: json({ categories: [] }) },
    { path: '/api/integrations/status', reply: json({ fantasypros: false, yahoo_configured: false, yahoo_connected: false, llm: true }) },
  ]);
}

describe('App routing and shell', () => {
  it('renders the dashboard with landmarks and the active nav item', async () => {
    mockEverything();
    setHash('#/');
    render(<App />);
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: /Dashboard/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: /League settings/ })).toHaveTextContent('12-team · PPR');
    expect(await screen.findByText('No buy-low candidates right now.')).toBeInTheDocument();
  });

  it('switches pages on hash changes and moves focus to the new heading', async () => {
    mockEverything();
    setHash('#/');
    render(<App />);
    act(() => navigate('/rankings'));
    const heading = await screen.findByRole('heading', { level: 1, name: 'Rankings' });
    expect(heading).toHaveFocus();
    expect(document.title).toBe('Rankings · Gridiron Edge');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: /Rankings/ })).toHaveAttribute('aria-current', 'page');

    act(() => navigate('/stats'));
    expect(await screen.findByText('No leaderboard metrics are available.')).toBeInTheDocument();
    act(() => navigate('/settings'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    act(() => navigate('/market'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Buy low / Sell high' })).toBeInTheDocument();
    act(() => navigate('/ask'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Ask AI' })).toBeInTheDocument();
  });

  it('shows a not-found page for unknown routes', () => {
    mockEverything();
    setHash('#/does-not-exist');
    render(<App />);
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '#/');
  });

  it('opens and closes the phone "More" menu', async () => {
    const user = userEvent.setup();
    mockEverything();
    setHash('#/');
    render(<App />);
    const more = screen.getByRole('button', { name: 'More' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await user.click(more);
    await user.click(screen.getByRole('heading', { level: 1, name: 'Dashboard' }));
    expect(more).toHaveAttribute('aria-expanded', 'false');
  });

  it('returns focus to the "More" toggle on Escape and closes when focus leaves', async () => {
    const user = userEvent.setup();
    mockEverything();
    setHash('#/');
    render(<App />);
    const more = screen.getByRole('button', { name: 'More' });
    await user.click(more);
    const menuLinks = within(more.closest('li') as HTMLElement).getAllByRole('link');
    act(() => menuLinks[0].focus());
    await user.keyboard('{Escape}');
    expect(more).toHaveFocus();
    expect(more).toHaveAttribute('aria-expanded', 'false');

    await user.click(more);
    act(() => menuLinks[menuLinks.length - 1].focus());
    await user.tab();
    expect(more.closest('li')).not.toContainElement(document.activeElement as HTMLElement);
    expect(more).toHaveAttribute('aria-expanded', 'false');
  });

  it('moves focus to main content from the skip link', async () => {
    const user = userEvent.setup();
    mockEverything();
    setHash('#/');
    render(<App />);
    await user.click(screen.getByRole('link', { name: 'Skip to content' }));
    expect(screen.getByRole('main')).toHaveFocus();
    expect(window.location.hash).toBe('#/');
  });
});
