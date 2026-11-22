import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { Tabs } from './Tabs';

const TABS = [
  { id: 'a', label: 'One', panel: <p>First panel</p> },
  { id: 'b', label: 'Two', panel: <p>Second panel</p> },
  { id: 'c', label: 'Three', panel: <p>Third panel</p> },
];

function renderTabs() {
  render(<Tabs label="Stats" tabs={TABS} />);
  return { tab: (name: string) => screen.getByRole('tab', { name }) };
}

function expectOpen(name: string) {
  expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name })).toHaveFocus();
  expect(screen.getByRole('tabpanel', { name })).toBeVisible();
}

describe('Tabs', () => {
  it('opens the first tab and switches on click', async () => {
    const { tab } = renderTabs();
    expect(tab('One')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Second panel')).not.toBeVisible();

    await userEvent.setup().click(tab('Two'));

    expect(tab('Two')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Second panel')).toBeVisible();
    expect(screen.getByText('First panel')).not.toBeVisible();
  });

  it('moves with the arrow keys (wrapping), Home and End', () => {
    const { tab } = renderTabs();
    tab('One').focus();

    fireEvent.keyDown(tab('One'), { key: 'ArrowLeft' });
    expectOpen('Three');
    fireEvent.keyDown(tab('Three'), { key: 'ArrowRight' });
    expectOpen('One');
    fireEvent.keyDown(tab('One'), { key: 'End' });
    expectOpen('Three');
    fireEvent.keyDown(tab('Three'), { key: 'Home' });
    expectOpen('One');
  });

  it('leaves Alt, Ctrl and Meta arrows to the browser', () => {
    const { tab } = renderTabs();
    tab('One').focus();

    for (const modifier of ['altKey', 'ctrlKey', 'metaKey']) {
      const handled = !fireEvent.keyDown(tab('One'), { key: 'ArrowLeft', [modifier]: true });
      expect(handled).toBe(false);
    }
    expectOpen('One');
  });
});
