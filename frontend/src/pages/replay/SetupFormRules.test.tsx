import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { actionResult, draftSummary, seasons } from './fixtures';
import { SetupForm } from './SetupForm';

const SEASONS_PATH = '/api/replay/seasons';
const GAMES_PATH = '/api/replay/games';

function renderForm() {
  const api = mockFetch([META_ROUTE, { path: SEASONS_PATH, reply: json(seasons()) },
    { method: 'POST', path: GAMES_PATH, reply: json(actionResult(draftSummary({ id: 12 }))) }]);
  const onCreated = vi.fn();
  renderWithProviders(<SetupForm onCreated={onCreated} />);
  return { api, onCreated, user: userEvent.setup() };
}

const count = (name: string) => within(screen.getByRole('group', { name: `${name} starters` })).getByText(/^\d$/).textContent;
const counts = () => ['QB', 'RB', 'WR', 'TE', 'flex', 'superflex', 'K', 'DST'].map(count);
const preset = (name: string) => screen.getByRole('button', { name });

async function submit(user: ReturnType<typeof userEvent.setup>, onCreated: ReturnType<typeof vi.fn>) {
  await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
  await vi.waitFor(() => expect(onCreated).toHaveBeenCalled());
}

describe('SetupForm league format and scoring', () => {
  it('starts standard, and the presets set the slot counts', async () => {
    const { user } = renderForm();
    await screen.findByLabelText('Season');
    expect(counts()).toEqual(['1', '2', '2', '1', '1', '0', '1', '1']);
    expect(preset('Standard')).toHaveAttribute('aria-pressed', 'true');

    await user.click(preset('Superflex'));
    expect(counts()).toEqual(['1', '2', '2', '1', '1', '1', '1', '1']);
    expect(preset('Superflex')).toHaveAttribute('aria-pressed', 'true');
    expect(preset('Standard')).toHaveAttribute('aria-pressed', 'false');

    await user.click(preset('Double flex'));
    expect(counts()).toEqual(['1', '2', '2', '1', '2', '0', '1', '1']);
    expect(screen.getByText(/10 starters \+ 6 bench = 16 roster spots/)).toBeInTheDocument();
  });

  it('steps a slot within the bounds the server accepts', async () => {
    const { user } = renderForm();
    await screen.findByLabelText('Season');
    const fewerQb = screen.getByRole('button', { name: 'Fewer QB starters' });
    expect(fewerQb).toBeDisabled();                                    // a lineup needs a QB or a superflex
    await user.click(screen.getByRole('button', { name: 'More superflex starters' }));
    await user.click(fewerQb);
    expect([count('QB'), count('superflex')]).toEqual(['0', '1']);

    const moreWr = screen.getByRole('button', { name: 'More WR starters' });
    await user.click(moreWr);
    expect(count('WR')).toBe('3');
    expect(moreWr).toBeDisabled();                                     // at most 3 per slot
    await user.click(screen.getByRole('button', { name: 'More RB starters' }));
    await user.click(screen.getByRole('button', { name: 'More TE starters' }));
    expect(screen.getByText(/12 starters \+ 6 bench/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More K starters' })).toBeDisabled();     // 12 starters in all
  });

  it('sends the lineup and the custom rules only when they differ from the defaults', async () => {
    const { api, onCreated, user } = renderForm();
    await screen.findByLabelText('Season');
    await user.click(preset('Superflex'));
    await user.selectOptions(screen.getByLabelText('Scoring'), 'ppr');
    expect(screen.getByLabelText('Points per reception')).toHaveValue('1');
    await user.selectOptions(screen.getByLabelText('TE premium'), '0.5');
    await user.selectOptions(screen.getByLabelText('Passing TD'), '6');
    await user.selectOptions(screen.getByLabelText('Turnovers (INT, fumble lost)'), '-1');
    expect(screen.getByText(/projections are estimates converted/)).toBeInTheDocument();
    await submit(user, onCreated);

    const body = api.callsTo(GAMES_PATH, 'POST')[0].body as Record<string, unknown>;
    expect(body.scoring).toBe('ppr');
    expect(body.starters).toEqual({ QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, SUPERFLEX: 1, K: 1, DST: 1 });
    expect(body.scoring_rules).toEqual({ reception: 1, te_premium: 0.5, pass_td: 6, turnover: -1 });
  });

  it('leaves the rules out when only the base preset changes, and sends a custom reception value', async () => {
    const { api, onCreated, user } = renderForm();
    await screen.findByLabelText('Season');
    await user.selectOptions(screen.getByLabelText('Scoring'), 'std');
    await submit(user, onCreated);
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).not.toHaveProperty('scoring_rules');
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).not.toHaveProperty('starters');

    await user.selectOptions(screen.getByLabelText('Points per reception'), '0.75');
    expect(within(screen.getByLabelText('Points per reception')).getAllByRole('option')).toHaveLength(7);
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
    await vi.waitFor(() => expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(2));
    expect(api.callsTo(GAMES_PATH, 'POST')[1].body).toMatchObject({
      scoring: 'std', scoring_rules: { reception: 0.75, te_premium: 0, pass_td: 4, turnover: -2 },
    });
  });
});
