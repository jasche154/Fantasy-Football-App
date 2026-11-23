/** Replay League setup: the league format (lineup presets and per-slot steppers) and the scoring key settings.
 * Bounds mirror backend/services/replay_slots.py and replay_scoring.py (the server checks them again). */
import type { ReplayScoringRules, ReplaySlot, ReplayStarters, Scoring } from '../../api/types';
import { SelectField } from '../../components/SelectField';
import { SCORING_OPTIONS } from '../../state/leagueSettings';
import { slotLabel, STARTING_SLOTS } from './teamLineup';

type StartingSlot = Exclude<ReplaySlot, 'BN'>;
type FormatPreset = 'standard' | 'superflex' | 'double_flex';

const MAX_PER_SLOT = 3;
const MAX_STARTERS = 12;
const RECEPTION_STEPS = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5];
const PRESET_RECEPTION: Record<Scoring, number> = { ppr: 1, half: 0.5, std: 0 };
export const STANDARD_STARTERS: Readonly<Record<StartingSlot, number>> = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, SUPERFLEX: 0, K: 1, DST: 1 };
const PRESETS: readonly { value: FormatPreset; title: string; starters: Readonly<Record<StartingSlot, number>> }[] = [
  { value: 'standard', title: 'Standard', starters: STANDARD_STARTERS },
  { value: 'superflex', title: 'Superflex', starters: { ...STANDARD_STARTERS, SUPERFLEX: 1 } },
  { value: 'double_flex', title: 'Double flex', starters: { ...STANDARD_STARTERS, FLEX: 2 } },
];
const SLOT_NAMES: Partial<Record<StartingSlot, string>> = { SUPERFLEX: 'superflex', FLEX: 'flex' };

/** The preset's rules: what a league with no custom scoring plays. */
export function presetRules(scoring: Scoring): ReplayScoringRules {
  return { reception: PRESET_RECEPTION[scoring], te_premium: 0, pass_td: 4, turnover: -2 };
}

export function sameStarters(a: Readonly<Record<StartingSlot, number>>, b: Readonly<Record<StartingSlot, number>>): boolean {
  return STARTING_SLOTS.every((slot) => a[slot] === b[slot]);
}

/** Only the slots that start someone, as the server stores them. */
export function startersBody(starters: Readonly<Record<StartingSlot, number>>): ReplayStarters {
  return Object.fromEntries(STARTING_SLOTS.filter((slot) => starters[slot] > 0).map((slot) => [slot, starters[slot]]));
}

/** Is this lineup inside the bounds the server accepts? */
function isLegal(starters: Readonly<Record<StartingSlot, number>>): boolean {
  const counts = STARTING_SLOTS.map((slot) => starters[slot]);
  const skill = starters.RB + starters.WR + starters.TE + starters.FLEX;
  return counts.every((count) => count >= 0 && count <= MAX_PER_SLOT) && starters.QB + starters.SUPERFLEX >= 1
    && skill >= 1 && counts.reduce((sum, count) => sum + count, 0) <= MAX_STARTERS;
}

interface FormatProps {
  starters: Readonly<Record<StartingSlot, number>>;
  bench: number;
  onChange: (starters: Record<StartingSlot, number>) => void;
}

interface StepProps {
  label: string;
  next: Readonly<Record<StartingSlot, number>>;
  onChange: (starters: Record<StartingSlot, number>) => void;
  children: string;
}

/** A step that would leave the bounds is aria-disabled, not disabled, so focus stays on it at a limit. */
function StepButton({ label, next, onChange, children }: StepProps) {
  const allowed = isLegal(next);
  return (
    <button type="button" className="btn btn--ghost btn--sm replay-stepper__btn" aria-label={label}
      aria-disabled={!allowed} onClick={() => { if (allowed) onChange({ ...next }); }}>{children}</button>
  );
}

/** Preset buttons, then a stepper per slot; a step that would leave the bounds does nothing. */
export function LeagueFormatFields({ starters, bench, onChange }: FormatProps) {
  const total = STARTING_SLOTS.reduce((sum, slot) => sum + starters[slot], 0);
  const stepped = (slot: StartingSlot, delta: number) => ({ ...starters, [slot]: starters[slot] + delta });
  return (
    <fieldset className="fieldset">
      <legend className="field__label">League format</legend>
      <div className="segmented replay-segmented" role="group" aria-label="Lineup preset">
        {PRESETS.map((preset) => {
          const isActive = sameStarters(preset.starters, starters);
          return (
            <button key={preset.value} type="button" aria-pressed={isActive}
              className={`segmented__option${isActive ? ' is-active' : ''}`} onClick={() => onChange({ ...preset.starters })}>
              {preset.title}
            </button>
          );
        })}
      </div>
      <div className="replay-slots" role="group" aria-label="Starters per slot">
        {STARTING_SLOTS.map((slot) => {
          const name = SLOT_NAMES[slot] ?? slot;
          return (
            <div key={slot} className="replay-stepper" role="group" aria-label={`${name} starters`}>
              <span className="replay-stepper__label" aria-hidden="true">{slotLabel(slot)}</span>
              <StepButton label={`Fewer ${name} starters`} next={stepped(slot, -1)} onChange={onChange}>−</StepButton>
              <span className="replay-stepper__value" aria-live="polite">{starters[slot]}</span>
              <StepButton label={`More ${name} starters`} next={stepped(slot, 1)} onChange={onChange}>+</StepButton>
            </div>
          );
        })}
      </div>
      <p className="hint">
        {total} starters + {bench} bench = {total + bench} roster spots (and draft rounds). FLEX takes a RB, WR or TE;
        SFLX (superflex) also takes a QB, and CPU managers draft quarterbacks much earlier for it.
      </p>
    </fieldset>
  );
}

interface ScoringProps {
  scoring: Scoring;
  rules: ReplayScoringRules;
  onScoring: (scoring: Scoring) => void;
  onRules: (rules: ReplayScoringRules) => void;
}

const signed = (value: number) => (value > 0 ? `+${value}` : String(value));

/** The base preset (which resets the reception value) and the four key settings. */
export function ScoringFields({ scoring, rules, onScoring, onRules }: ScoringProps) {
  const set = (key: keyof ReplayScoringRules) => (value: string) => onRules({ ...rules, [key]: Number(value) });
  return (
    <fieldset className="fieldset">
      <legend className="field__label">Scoring rules</legend>
      <div className="replay-setup__grid">
        <SelectField label="Scoring" value={scoring} options={SCORING_OPTIONS}
          onChange={(value) => { onScoring(value as Scoring); onRules({ ...rules, reception: PRESET_RECEPTION[value as Scoring] }); }} />
        <SelectField label="Points per reception" value={rules.reception} onChange={set('reception')}
          options={RECEPTION_STEPS.map((value) => ({ value, label: String(value) }))} />
        <SelectField label="TE premium" value={rules.te_premium} onChange={set('te_premium')}
          options={[0, 0.5, 1].map((value) => ({ value, label: value ? `+${value} per TE catch` : 'None' }))} />
        <SelectField label="Passing TD" value={rules.pass_td} onChange={set('pass_td')}
          options={[4, 6].map((value) => ({ value, label: `${value} points` }))} />
        <SelectField label="Turnovers (INT, fumble lost)" value={rules.turnover} onChange={set('turnover')}
          options={[-2, -1].map((value) => ({ value, label: `${signed(value)} points` }))} />
      </div>
      <p className="hint">
        Played games always score exactly under these rules; with custom settings, projections are estimates converted from
        the sites&apos; PPR and standard numbers.
      </p>
    </fieldset>
  );
}
