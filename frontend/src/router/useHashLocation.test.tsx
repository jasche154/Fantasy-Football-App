import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { navigate, useHashLocation } from './useHashLocation';

function Probe() {
  const location = useHashLocation();
  return (
    <p>
      {location.route.name}|{location.path}|{JSON.stringify(location.query)}
    </p>
  );
}

describe('useHashLocation', () => {
  it('reads the current hash and updates on replace navigation', () => {
    window.history.replaceState(null, '', '/#/rankings');
    render(<Probe />);
    expect(screen.getByText('rankings|/rankings|{}')).toBeInTheDocument();

    act(() => navigate('/trade', { give: '1,2' }, { replace: true }));

    expect(window.location.hash).toBe('#/trade?give=1,2');
    expect(screen.getByText('trade|/trade|{"give":"1,2"}')).toBeInTheDocument();
  });

  it('updates on push navigation via hashchange', async () => {
    render(<Probe />);
    act(() => navigate('/player/9'));
    expect(await screen.findByText('player|/player/9|{}')).toBeInTheDocument();
  });

  it('does nothing when navigating to the current hash', () => {
    window.history.replaceState(null, '', '/#/stats');
    const before = window.history.length;
    navigate('/stats');
    expect(window.history.length).toBe(before);
    expect(window.location.hash).toBe('#/stats');
  });
});
