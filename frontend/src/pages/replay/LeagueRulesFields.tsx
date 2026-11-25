/** Replay League setup: the league format (lineup presets and per-slot steppers) and the scoring key settings.
 * Bounds mirror backend/services/replay_slots.py and replay_scoring.py (the server checks them again). */
import { useState } from 'react';
import type { ReplayScoringRules, ReplaySlot, ReplayStarters, Scoring } from '../../api/types';
import { SelectField } from '../../components/SelectField';
import { SCORING_OPTIONS } from '../../state/leagueSettings';
import { slotLabel, STARTING_SLOTS } from './teamLineup';

type StartingSlot = Exclude<ReplaySlot, 'BN'>;
type FormatPreset = 'standard' | 'superflex' | 'double_flex';
const CUSTOM = 'custom';

const MAX_PER_SLOT = 3;
const MAX_STARTERS = 12;
export const DEFAULT_BENCH = 6;
const BENCH_RANGE = [1, 10] as const;
const RECEPTION_STEPS = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5];
const PRESET_RECEPTION: Record<Scoring, number> = { ppr: 1, half: 0.5, std: 0 };
export const STANDARD_STARTERS: Readonly<Record<StartingSlot, number>> = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, SUPERFLEX: 0, K: 1, DST: 1 };
const PRESETS: readonly { value: FormatPreset; title: string; starters: Readonly<Record<StartingSlot, number>> }[] = [
  { value: 'standard', title: 'Standard', starters: STANDARD_STARTERS },
  { value: 'superflex', title: 'Superflex', starters: { ...STANDARD_STARTERS, SUPERFLEX: 1 } },
  { value: 'double_flex', title: 'Double flex', starters: { ...STANDARD_STARTERS, FLEX: 2 } },
];
const SLOT_NAMES: Partial<Record<StartingSlot, string>> = { SUPERFLEX: 'superflex', FLEX: 'flex' };
const FORMAT_NOTES: Record<FormatPreset | typeof CUSTOM, string> = {
  standard: 'The usual lineup: QB, 2 RB, 2 WR, TE, one FLEX (RB, WR or TE), K and DST.',
  superflex: 'Standard plus a superflex (SFLX) spot that takes a QB, RB, WR or TE. Most teams start a second QB there, so quarterbacks get drafted early.',
  double_flex: 'Two FLEX spots (RB, WR or TE), so depth at running back and receiver matters more.',
  custom: 'Set each slot and the bench yourself: up to 3 per slot and 12 starters, with at least one QB or superflex. FLEX takes a RB, WR or TE; SFLX also takes a QB.',
};
const RULE_NOTES: Record<Scoring, string> = {
  ppr: '1 point per catch, 4-point passing TDs, -2 per turnover.',
  half: '0.5 points per catch, 4-point passing TDs, -2 per turnover.',
  std: 'No points per catch, 4-point passing TDs, -2 per turnover.',
};

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
  onBench: (bench: number) => void;
}

interface StepProps {
  label: string;
  isAllowed: boolean;
  onStep: () => void;
  children: string;
}

/** A step that would leave the bounds is aria-disabled, not disabled, so focus stays on it at a limit. */
function StepButton({ label, isAllowed, onStep, children }: StepProps) {
  return (
    <button type="button" className="btn btn--ghost btn--sm replay-stepper__btn" aria-label={label}
      aria-disabled={!isAllowed} onClick={() => { if (isAllowed) onStep(); }}>{children}</button>
  );
}

interface StepperProps {
  label: string;
  name: string;
  value: number;
  canStep: (delta: number) => boolean;
  onStep: (delta: number) => void;
}

function Stepper({ label, name, value, canStep, onStep }: StepperProps) {
  return (
    <div className="replay-stepper" role="group" aria-label={name}>
      <span className="replay-stepper__label" aria-hidden="true">{label}</span>
      <StepButton label={`Fewer ${name}`} isAllowed={canStep(-1)} onStep={() => onStep(-1)}>−</StepButton>
      <span className="replay-stepper__value" aria-live="polite">{value}</span>
      <StepButton label={`More ${name}`} isAllowed={canStep(1)} onStep={() => onStep(1)}>+</StepButton>
    </div>
  );
}

function PresetButton({ isActive, onClick, children }: { isActive: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" aria-pressed={isActive} className={`segmented__option${isActive ? ' is-active' : ''}`} onClick={onClick}>
      {children}
    </button>
  );
}

/** Preset buttons and a note on the chosen one; Custom shows a stepper per slot (a step out of bounds does nothing). */
export function LeagueFormatFields({ starters, bench, onChange, onBench }: FormatProps) {
  const matched = PRESETS.find((preset) => sameStarters(preset.starters, starters))?.value;
  const [isCustom, setIsCustom] = useState(matched === undefined || bench !== DEFAULT_BENCH);
  const active = isCustom || matched === undefined ? CUSTOM : matched;
  const total = STARTING_SLOTS.reduce((sum, slot) => sum + starters[slot], 0);
  const stepped = (slot: StartingSlot, delta: number) => ({ ...starters, [slot]: starters[slot] + delta });
  return (
    <fieldset className="fieldset">
      <legend className="field__label">League format</legend>
      <div className="segmented replay-segmented" role="group" aria-label="Lineup preset">
        {PRESETS.map((preset) => (
          <PresetButton key={preset.value} isActive={active === preset.value}
            onClick={() => { setIsCustom(false); onChange({ ...preset.starters }); onBench(DEFAULT_BENCH); }}>{preset.title}</PresetButton>
        ))}
        <PresetButton isActive={active === CUSTOM} onClick={() => setIsCustom(true)}>Custom</PresetButton>
      </div>
      <p className="hint">{FORMAT_NOTES[active]}</p>
      {active === CUSTOM && <div className="replay-slots" role="group" aria-label="Starters per slot">
        {STARTING_SLOTS.map((slot) => (
          <Stepper key={slot} label={slotLabel(slot)} name={`${SLOT_NAMES[slot] ?? slot} starters`} value={starters[slot]}
            canStep={(delta) => isLegal(stepped(slot, delta))} onStep={(delta) => onChange(stepped(slot, delta))} />
        ))}
        <Stepper label="BN" name="bench spots" value={bench}
          canStep={(delta) => bench + delta >= BENCH_RANGE[0] && bench + delta <= BENCH_RANGE[1]}
          onStep={(delta) => onBench(bench + delta)} />
      </div>}
      <p className="hint">{total} starters + {bench} bench = {total + bench} roster spots (and draft rounds).</p>
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

const SCORING_CHOICES = [...SCORING_OPTIONS, { value: CUSTOM, label: 'Custom' }];

/** A preset (which resets every rule to its values) or Custom, which shows the four key settings.
 * Custom keeps the last preset as the base the server converts projections from. */
export function ScoringFields({ scoring, rules, onScoring, onRules }: ScoringProps) {
  const [isCustom, setIsCustom] = useState(() => !sameRules(rules, presetRules(scoring)));
  const set = (key: keyof ReplayScoringRules) => (value: string) => onRules({ ...rules, [key]: Number(value) });
  const choose = (value: string) => {
    if (value === CUSTOM) { setIsCustom(true); return; }
    setIsCustom(false);
    onScoring(value as Scoring);
    onRules(presetRules(value as Scoring));
  };
  return (
    <fieldset className="fieldset">
      <legend className="field__label">Scoring rules</legend>
      <div className="replay-setup__narrow">
        <SelectField label="Scoring" value={isCustom ? CUSTOM : scoring} options={SCORING_CHOICES} onChange={choose} />
      </div>
      {!isCustom && <p className="hint">{RULE_NOTES[scoring]}</p>}
      {isCustom && <CustomRules rules={rules} set={set} />}
    </fieldset>
  );
}

function sameRules(a: ReplayScoringRules, b: ReplayScoringRules): boolean {
  return (Object.keys(b) as (keyof ReplayScoringRules)[]).every((key) => a[key] === b[key]);
}

function CustomRules({ rules, set }: { rules: ReplayScoringRules; set: (key: keyof ReplayScoringRules) => (value: string) => void }) {
  return (
    <>
      <div className="replay-setup__grid">
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
    </>
  );
}
