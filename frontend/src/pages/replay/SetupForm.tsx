import { useEffect, useId, useState, type FormEvent } from 'react';
import { errorMessage } from '../../api/client';
import { createReplayGame, getReplaySeasons, prepareReplaySeason } from '../../api/replay';
import type {
  ReplayDifficulty, ReplayPrepareStatus, ReplayReadiness, ReplaySeasons, ReplaySummary, ReplayWaiverMode, Scoring,
} from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { Skeleton } from '../../components/StatusViews';
import { useAction } from '../../hooks/useAction';
import { useAsync, type AsyncResult } from '../../hooks/useAsync';
import { SCORING_OPTIONS } from '../../state/leagueSettings';

const POLL_MS = 3000;
const RANDOM = 'random';
const TEAM_COUNTS = [8, 10, 12, 14];
const MAX_NAME_LENGTH = 60;
const STEP_LABELS: Record<string, string> = { starting: 'starting', game_results: 'game results', adp: 'ADP', projections: 'projections',
  injuries: 'injury reports' };

const WAIVER_MODES: readonly { value: ReplayWaiverMode; title: string; text: string }[] = [
  { value: 'wednesday', title: 'Wednesday waivers', text: 'After each week, unrostered players sit on waivers until Wednesday, when claims settle in priority order.' },
  { value: 'none', title: 'Free agency only', text: 'No weekly waiver run: unrostered players are free agents you can add right away (a dropped player frees up the next week).' },
];
const DIFFICULTIES: readonly { value: ReplayDifficulty; title: string; text: string }[] = [
  { value: 'normal', title: 'Normal', text: 'CPU managers reach and slide around ADP in the draft, and trade with you at even value.' },
  { value: 'hard', title: 'Hard', text: 'CPU managers stick closer to ADP (half the randomness) and want about 8% more value back in a trade with you.' },
];

function prepareText(prepare: ReplayPrepareStatus): string {
  if (prepare.status === 'running') {
    const step = prepare.step ? `: ${STEP_LABELS[prepare.step] ?? prepare.step}` : '';
    return `Preparing ${prepare.season}${step}…`;
  }
  if (prepare.status === 'failed') return `Preparing ${prepare.season} failed (${prepare.error ?? 'unknown error'}). Try again.`;
  return prepare.error ? `${prepare.season} was prepared, with problems: ${prepare.error}.` : `${prepare.season} is prepared.`;
}

interface RadioCardsProps<T extends string> {
  legend: string;
  options: readonly { value: T; title: string; text: string }[];
  value: T;
  onChange: (value: T) => void;
}

function RadioCards<T extends string>({ legend, options, value, onChange }: RadioCardsProps<T>) {
  const name = useId();
  return (
    <fieldset className="fieldset">
      <legend className="field__label">{legend}</legend>
      <div className="radio-cards">
        {options.map((option) => (
          <label key={option.value} className="radio-card">
            <input type="radio" name={name} value={option.value} checked={option.value === value} onChange={() => onChange(option.value)} />
            <span className="radio-card__title">{option.title}</span>
            <span className="radio-card__text">{option.text}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

interface SeasonsProps {
  seasons: readonly ReplayReadiness[];
  prepare: ReplayPrepareStatus | null;
  isStarting: boolean;
  /** An import was started from this form, so its clean finish is worth saying. */
  wasStartedHere: boolean;
  onPrepare: (season: number) => void;
}

/** Seasons that can't be played yet, each with its Prepare button, and the import's progress. */
function UnpreparedSeasons({ seasons, prepare, isStarting, wasStartedHere, onPrepare }: SeasonsProps) {
  const isRunning = prepare?.status === 'running';
  const waiting = seasons.filter((season) => !season.ready);
  // Prepared before injury reports existed: playable, but without tags or the IR slot.
  const untagged = seasons.filter((season) => season.ready && season.injury_weeks === 0);
  const isDisabled = isRunning || isStarting;
  // The server keeps the last import's status for good: an old clean "done" is not news.
  const isNews = prepare !== null && (prepare.status !== 'done' || prepare.error !== null || wasStartedHere);
  return (
    <div className="replay-prepare">
      {isNews && <p className="hint" role="status">{prepareText(prepare)}</p>}
      {waiting.length > 0 && (
        <>
          <p className="hint">
            Not ready yet. Preparing a season downloads its results, ADP and projections, which takes about a minute.
          </p>
          <ul className="replay-prepare__list">
            {waiting.map(({ season }) => (
              <li key={season}>
                <button type="button" className="btn btn--ghost btn--sm" disabled={isDisabled} onClick={() => onPrepare(season)}>
                  Prepare {season} data
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {untagged.length > 0 && (
        <>
          <p className="hint">Ready to play, but without injury reports. Adding them takes a few seconds.</p>
          <ul className="replay-prepare__list">
            {untagged.map(({ season }) => (
              <li key={season}>
                <button type="button" className="btn btn--ghost btn--sm" disabled={isDisabled} onClick={() => onPrepare(season)}>
                  Add {season} injury reports
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** The new-league form. Seasons come from GET /seasons; a running import is polled until it ends. */
export function SetupForm({ onCreated }: { onCreated: (game: ReplaySummary) => void }) {
  const nameId = useId();
  const seasons = useAsync((signal) => getReplaySeasons(signal), []);
  const prepare = useAction(prepareReplaySeason);
  const create = useAction(createReplayGame);
  const [season, setSeason] = useState('');
  const [name, setName] = useState('');
  const [teams, setTeams] = useState(12);
  const [scoring, setScoring] = useState<Scoring>('half');
  const [slot, setSlot] = useState(RANDOM);
  const [waiverMode, setWaiverMode] = useState<ReplayWaiverMode>('wednesday');
  const [difficulty, setDifficulty] = useState<ReplayDifficulty>('normal');

  const { data: loaded, reload } = seasons;
  // Every settled answer (or failed poll) while an import runs books the next poll. Not while one is in
  // flight: re-booking then would abort a poll slower than POLL_MS, again and again.
  useEffect(() => {
    if (seasons.status === 'loading' || loaded?.prepare?.status !== 'running') return undefined;
    const timer = window.setTimeout(reload, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [loaded, seasons.status, reload]);
  // A failed progress check keeps the form (with the last answer) and polls again.
  const isPollFailure = seasons.status === 'error' && loaded?.prepare?.status === 'running';
  const shown: AsyncResult<ReplaySeasons> = isPollFailure && loaded ? { status: 'success', data: loaded, reload } : seasons;

  const startPrepare = async (wanted: number) => {
    if (await prepare.run(wanted)) reload();
  };

  return (
    <AsyncView state={shown} loading={<Skeleton lines={4} label="Loading seasons…" />} errorTitle="Couldn't load the seasons">
      {(data) => {
        const ready = data.results.filter((row) => row.ready).map((row) => String(row.season));
        // Until the user picks one, the newest ready season.
        const chosen = season === RANDOM || ready.includes(season) ? season : (ready[ready.length - 1] ?? '');
        const chosenSlot = slot !== RANDOM && Number(slot) > teams ? RANDOM : slot;
        const submit = async (event: FormEvent) => {
          event.preventDefault();
          const result = await create.run({
            season: chosen === RANDOM ? RANDOM : Number(chosen),
            name: name.trim() || undefined,
            teams,
            scoring,
            draft_slot: chosenSlot === RANDOM ? undefined : Number(chosenSlot),
            waiver_mode: waiverMode,
            difficulty,
          });
          if (result) onCreated(result.game);
        };
        return (
          <form className="settings-form" onSubmit={submit}>
            {ready.length === 0 && (
              <p className="notice" role="status">No season is ready to play yet. Prepare one with a button below, then set up your league.</p>
            )}
            <div className="replay-setup__grid">
              <SelectField label="Season" value={chosen} disabled={ready.length === 0} onChange={setSeason}
                options={[...ready.map((year) => ({ value: year, label: year })), { value: RANDOM, label: 'Random season' }]} />
              <div className="field">
                <label className="field__label" htmlFor={nameId}>League name</label>
                <input id={nameId} className="input" value={name} maxLength={MAX_NAME_LENGTH} placeholder="e.g. Sunday Replay"
                  onChange={(event) => setName(event.target.value)} />
              </div>
              <SelectField label="Teams" value={teams} onChange={(value) => setTeams(Number(value))}
                options={TEAM_COUNTS.map((count) => ({ value: count, label: `${count} teams` }))} />
              <SelectField label="Scoring" value={scoring} onChange={(value) => setScoring(value as Scoring)} options={SCORING_OPTIONS} />
              <SelectField label="Draft slot" value={chosenSlot} onChange={setSlot}
                options={[{ value: RANDOM, label: 'Random' }, ...Array.from({ length: teams }, (_, index) => ({ value: String(index + 1), label: `Pick ${index + 1}` }))]} />
            </div>
            <UnpreparedSeasons seasons={data.results} prepare={data.prepare} isStarting={prepare.status === 'pending'}
              wasStartedHere={prepare.status === 'success'} onPrepare={startPrepare} />
            {isPollFailure && <p className="hint" role="status">Couldn't check the progress just now. Trying again…</p>}
            {prepare.status === 'error' && <p className="error-text" role="alert">{errorMessage(prepare.error)}</p>}
            <RadioCards legend="Waivers" options={WAIVER_MODES} value={waiverMode} onChange={setWaiverMode} />
            <RadioCards legend="Difficulty" options={DIFFICULTIES} value={difficulty} onChange={setDifficulty} />
            <div className="settings-form__footer">
              <button type="submit" className="btn btn--primary" disabled={ready.length === 0 || create.status === 'pending'}>
                {create.status === 'pending' ? 'Creating your league…' : 'Create league and draft'}
              </button>
              {create.status === 'error' && <p className="error-text" role="alert">{errorMessage(create.error)}</p>}
            </div>
            <p className="hint attribution">ADP: Fantasy Football Calculator</p>
          </form>
        );
      }}
    </AsyncView>
  );
}
