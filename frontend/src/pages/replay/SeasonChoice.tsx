/** Replay League setup: what to play (a past season, the live season or next year's simulated one), how a live league
 * starts, and the radio cards the setup form uses. Rules mirror backend/services/sim_setup.py (the server checks again). */
import { useId } from 'react';
import type { ReplaySimulatedSeason, ReplayStart } from '../../api/types';

export const PAST = 'past';
/** A league that starts now gets the smallest bracket by default (sim_setup.NOW_PLAYOFF_TEAMS). */
export const NOW_PLAYOFF_TEAMS = 4;
const MIN_WEEKS_LEFT = 3;          // sim_setup.MIN_WEEKS_LEFT

export interface RadioOption<T extends string> {
  value: T;
  title: string;
  text: string;
  disabled?: boolean;
}

interface RadioCardsProps<T extends string> {
  legend: string;
  options: readonly RadioOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

export function RadioCards<T extends string>({ legend, options, value, onChange }: RadioCardsProps<T>) {
  const name = useId();
  return (
    <fieldset className="fieldset">
      <legend className="field__label">{legend}</legend>
      <div className="radio-cards">
        {options.map((option) => (
          <label key={option.value} className="radio-card">
            <input type="radio" name={name} value={option.value} checked={option.value === value} disabled={option.disabled}
              onChange={() => onChange(option.value)} />
            <span className="radio-card__title">{option.title}</span>
            <span className="radio-card__text">{option.text}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function simulatedOption(row: ReplaySimulatedSeason): RadioOption<string> {
  const isLive = row.kind === 'live';
  const ready = isLive
    ? `Real through week ${row.through}, then simulated: every league plays out its own rest of the season.`
    : 'Next year’s rookies and schedule, the whole season simulated: every league different.';
  return { value: String(row.season), title: `${row.season} · ${isLive ? 'live' : 'simulated season'}`,
    text: row.ready ? ready : (row.reason ?? 'Not ready yet.'), disabled: !row.ready };
}

interface SeasonKindProps {
  simulated: readonly ReplaySimulatedSeason[];
  /** PAST, or the simulated season's number as a string. */
  value: string;
  onChange: (value: string) => void;
}

/** Past seasons (real results) set apart from the simulated ones; a simulated season that isn't ready says why. */
export function SeasonKind({ simulated, value, onChange }: SeasonKindProps) {
  const options: RadioOption<string>[] = [
    { value: PAST, title: 'Past season', text: 'A finished NFL season, replayed week by week with the real results.' },
    ...simulated.map(simulatedOption),
  ];
  return <RadioCards legend="What to play" options={options} value={value} onChange={onChange} />;
}

/** The regular-season weeks a league starting after week `through` would have, and its trade deadline. */
function nowWindow(through: number, championshipWeek: number, playoffTeams: number) {
  const start = through + 1;
  const regular = championshipWeek - (playoffTeams <= 4 ? 2 : 3);
  const left = regular - start + 1;
  return { start, regular, left, deadline: start + Math.floor((2 * left) / 3) - 1 };
}

/** "Draft before week 10, play weeks 10-15 (6 weeks), playoffs in weeks 16-17, trade deadline week 13." */
export function nowSummary(through: number, championshipWeek: number, playoffTeams: number): string {
  const { start, regular, left, deadline } = nowWindow(through, championshipWeek, playoffTeams);
  if (left < MIN_WEEKS_LEFT) {
    return `Only ${Math.max(left, 0)} regular-season week${left === 1 ? '' : 's'} would be left after week ${through}, and a league `
      + `that starts now needs ${MIN_WEEKS_LEFT}: pick a later championship week or fewer playoff teams.`;
  }
  return `Your league drafts today on the current market and plays weeks ${start}-${regular} (${left} weeks), then the playoffs `
    + `in weeks ${regular + 1}-${championshipWeek}. Trade deadline: week ${deadline}.`;
}

interface StartChoiceProps {
  live: ReplaySimulatedSeason;
  value: ReplayStart;
  onChange: (value: ReplayStart) => void;
}

/** How a live league starts: a week-0 draft and the whole season, or a draft today and the weeks still to play. */
export function StartChoice({ live, value, onChange }: StartChoiceProps) {
  const next = live.through + 1;
  const options: RadioOption<ReplayStart>[] = [
    { value: 'full', title: 'Full season (draft from week 0)',
      text: `Weeks 1-${live.through} replay the real results, then the season turns simulated.` },
    { value: 'now', title: `Jump in now (draft today, play week ${next} on)`, disabled: !live.can_start_now,
      text: live.can_start_now
        ? `Draft on today’s market, knowing everything through week ${live.through}. Week ${next}’s injury tags and expert `
          + 'ranks are already simulated, so they can differ from the real ones on the dashboard.'
        : `Not available: ${live.reason ?? `too few regular-season weeks are left after week ${live.through}.`}` },
  ];
  return <RadioCards legend="Start" options={options} value={live.can_start_now ? value : 'full'} onChange={onChange} />;
}
