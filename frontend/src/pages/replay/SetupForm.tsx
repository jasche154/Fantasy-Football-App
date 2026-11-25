import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { errorMessage } from '../../api/client';
import { createReplayGame, getReplaySeasons, prepareReplaySeason } from '../../api/replay';
import type {
  ReplayDifficulty, ReplayPlayoffEndWeek, ReplayPrepareStatus, ReplayReadiness, ReplayScoringRules, ReplaySeasons, ReplayStart,
  ReplaySummary, ReplayWaiverMode, Scoring,
} from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { Skeleton } from '../../components/StatusViews';
import { useAction } from '../../hooks/useAction';
import { useAsync, type AsyncResult } from '../../hooks/useAsync';
import { DEFAULT_BENCH, LeagueFormatFields, presetRules, sameStarters, ScoringFields, STANDARD_STARTERS, startersBody } from './LeagueRulesFields';
import { NOW_PLAYOFF_TEAMS, nowSummary, PAST, RadioCards, SeasonKind, StartChoice } from './SeasonChoice';
import { adpSource } from './shared';

const POLL_MS = 3000;
const RANDOM = 'random';
const TEAM_COUNTS = [4, 6, 8, 10, 12, 14, 16];
/** Championship weeks on offer; week 18 needs a season with games in week 18 (a random season never gets it). */
const PLAYOFF_END_WEEKS: readonly ReplayPlayoffEndWeek[] = [16, 17, 18];
const DEFAULT_PLAYOFF_END: ReplayPlayoffEndWeek = 17;
const MIN_PLAYOFF_TEAMS = 4;
const MAX_PLAYOFF_TEAMS = 8;
const MAX_NAME_LENGTH = 60;
const STEP_LABELS: Record<string, string> = { starting: 'starting', game_results: 'game results', adp: 'ADP', projections: 'projections',
  injuries: 'injury reports', snaps: 'snap counts' };

const WAIVER_MODES: readonly { value: ReplayWaiverMode; title: string; text: string }[] = [
  { value: 'wednesday', title: 'Wednesday waivers', text: 'After each week, unrostered players sit on waivers until Wednesday, when claims settle in priority order.' },
  { value: 'faab', title: 'FAAB bidding', text: 'Every team gets $100 for the season. Bids are blind; the highest bid wins (ties: waiver priority). Claims settle on Wednesday.' },
  { value: 'none', title: 'Free agency only', text: 'No weekly waiver run: unrostered players are free agents you can add right away (a dropped player frees up the next week).' },
];
const DIFFICULTIES: readonly { value: ReplayDifficulty; title: string; text: string }[] = [
  { value: 'normal', title: 'Normal', text: 'CPU managers reach and slide around ADP in the draft, and trade with you at even value.' },
  { value: 'hard', title: 'Hard', text: 'CPU managers stick closer to ADP (half the randomness) and want about 8% more value back in a trade with you.' },
];

/** The backend's default: 4 below 10 teams, 6 below 14, else 8. */
function defaultPlayoffTeams(teams: number): number {
  if (teams < 10) return 4;
  return teams < 14 ? 6 : 8;
}

/** "Weeks 16-17 (final week 17)": a 4-team bracket plays 2 rounds, a bigger one 3, ending in the championship week. */
function playoffWindowLabel(endWeek: number, playoffTeams: number): string {
  const rounds = playoffTeams <= 4 ? 2 : 3;
  return `Weeks ${endWeek - rounds + 1}-${endWeek} (final week ${endWeek})`;
}

/** "6 teams (top 2 get a bye)": byes fill the bracket up to 4 or 8. */
function playoffTeamsLabel(count: number): string {
  const byes = (count <= 4 ? 4 : 8) - count;
  return byes === 0 ? `${count} teams` : `${count} teams (top ${byes} get${byes === 1 ? 's' : ''} a bye)`;
}

function prepareText(prepare: ReplayPrepareStatus): string {
  if (prepare.status === 'running') {
    const step = prepare.step ? `: ${STEP_LABELS[prepare.step] ?? prepare.step}` : '';
    return `Preparing ${prepare.season}${step}…`;
  }
  if (prepare.status === 'failed') return `Preparing ${prepare.season} failed (${prepare.error ?? 'unknown error'}). Try again.`;
  return prepare.error ? `${prepare.season} was prepared, with problems: ${prepare.error}.` : `${prepare.season} is prepared.`;
}

interface SeasonsProps {
  seasons: readonly ReplayReadiness[];
  /** The live season, when it needs (re)importing: real weeks finished since its last prepare. */
  live: number | null;
  prepare: ReplayPrepareStatus | null;
  isStarting: boolean;
  /** An import was started from this form, so its clean finish is worth saying. */
  wasStartedHere: boolean;
  onPrepare: (season: number) => void;
}

/** Seasons that can't be played yet, each with its Prepare button, and the import's progress. */
function UnpreparedSeasons({ seasons, live, prepare, isStarting, wasStartedHere, onPrepare }: SeasonsProps) {
  const isRunning = prepare?.status === 'running';
  const waiting = [...seasons.filter((season) => !season.ready).map(({ season }) => season), ...(live === null ? [] : [live])];
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
            {waiting.map((season) => (
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
  const [kind, setKind] = useState(PAST);                  // PAST, or a simulated season's number
  const [start, setStart] = useState<ReplayStart>('full');
  const isSubmitting = useRef(false);                      // a second press before the first re-render sends nothing
  const submitRef = useRef<HTMLButtonElement>(null);
  const [name, setName] = useState('');
  const [teams, setTeams] = useState(12);
  const [playoffEnd, setPlayoffEnd] = useState<ReplayPlayoffEndWeek>(DEFAULT_PLAYOFF_END);
  const [playoffTeams, setPlayoffTeams] = useState<number | null>(null);         // null: the league size's default
  const [scoring, setScoring] = useState<Scoring>('half');
  const [slot, setSlot] = useState(RANDOM);
  const [waiverMode, setWaiverMode] = useState<ReplayWaiverMode>('wednesday');
  const [difficulty, setDifficulty] = useState<ReplayDifficulty>('normal');
  const [starters, setStarters] = useState(STANDARD_STARTERS);
  const [bench, setBench] = useState(DEFAULT_BENCH);
  const [rules, setRules] = useState<ReplayScoringRules>(presetRules('half'));

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
        // A simulated season counts only while it is ready (an import can make it stale again).
        const sim = data.simulated.find((row) => String(row.season) === kind && row.ready) ?? null;
        const isLive = sim?.kind === 'live';
        const isNow = isLive && start === 'now' && sim.can_start_now;
        const liveWaiting = data.simulated.find((row) => row.kind === 'live' && !row.ready)?.season ?? null;
        // Until the user picks one, the newest ready season.
        const chosen = season === RANDOM || ready.includes(season) ? season : (ready[ready.length - 1] ?? '');
        const chosenSlot = slot !== RANDOM && Number(slot) > teams ? RANDOM : slot;
        const lastWeek = data.results.find((row) => String(row.season) === chosen)?.last_week ?? 0;
        // Every season has a week 17, so a random one can end there at the latest; simulated seasons run 18 weeks.
        const endWeeks = PLAYOFF_END_WEEKS.filter((end) => end < 18 || sim !== null || (chosen !== RANDOM && lastWeek >= 18));
        const chosenEnd = endWeeks.includes(playoffEnd) ? playoffEnd : DEFAULT_PLAYOFF_END;
        const chosenPlayoffTeams = playoffTeams ?? (isNow ? NOW_PLAYOFF_TEAMS : defaultPlayoffTeams(teams));
        const canCreate = sim !== null || ready.length > 0;
        const isCreating = create.status === 'pending';
        const playoffCounts = Array.from({ length: Math.min(MAX_PLAYOFF_TEAMS, teams) - MIN_PLAYOFF_TEAMS + 1 }, (_, index) => MIN_PLAYOFF_TEAMS + index);
        const preset = presetRules(scoring);
        const isCustom = (Object.keys(preset) as (keyof ReplayScoringRules)[]).some((key) => preset[key] !== rules[key]);
        const submit = async (event: FormEvent) => {
          event.preventDefault();
          if (isSubmitting.current) return;
          isSubmitting.current = true;
          // The fields are disabled while the league is built: keep focus on the (still focusable) button, not <body>.
          submitRef.current?.focus();
          const result = await create.run({
            season: sim ? sim.season : chosen === RANDOM ? RANDOM : Number(chosen),
            name: name.trim() || undefined,
            teams,
            scoring,
            draft_slot: chosenSlot === RANDOM ? undefined : Number(chosenSlot),
            waiver_mode: waiverMode,
            difficulty,
            playoff_end_week: chosenEnd,
            playoff_teams: playoffTeams ?? undefined,       // untouched: the server's default for the league size
            // Left out when standard / equal to the preset: the server's defaults.
            starters: sameStarters(starters, STANDARD_STARTERS) ? undefined : startersBody(starters),
            bench: bench === DEFAULT_BENCH ? undefined : bench,
            scoring_rules: isCustom ? rules : undefined,
            start: isLive ? (isNow ? 'now' : 'full') : undefined,
          });
          isSubmitting.current = false;
          if (result) onCreated(result.game);
        };
        return (
          <form className="settings-form" onSubmit={submit} aria-busy={isCreating}>
            {/* Everything is off while the league is built (a simulated one takes about 10 seconds). */}
            <fieldset className="replay-setup__all" disabled={isCreating}>
              {!canCreate && (
                <p className="notice" role="status">No season is ready to play yet. Prepare one with a button below, then set up your league.</p>
              )}
              {data.simulated.length > 0 && <SeasonKind simulated={data.simulated} value={sim ? kind : PAST} onChange={setKind} />}
              {isLive && (
                <div>
                  <StartChoice live={sim} value={start} onChange={setStart} />
                  {/* Mounted empty with the choice, then filled: a live region that arrives already filled is often skipped. */}
                  <div role="status">
                    {isNow && <p className="hint replay-setup__now">{nowSummary(sim.through, chosenEnd, chosenPlayoffTeams)}</p>}
                  </div>
                </div>
              )}
              <div className="replay-setup__grid">
                {!sim && (
                  <SelectField label="Season" value={chosen} disabled={ready.length === 0} onChange={setSeason}
                    options={[...ready.map((year) => ({ value: year, label: year })), { value: RANDOM, label: 'Random season' }]} />
                )}
                <div className="field">
                  <label className="field__label" htmlFor={nameId}>League name</label>
                  <input id={nameId} className="input" value={name} maxLength={MAX_NAME_LENGTH} placeholder="e.g. Sunday Replay"
                    onChange={(event) => setName(event.target.value)} />
                </div>
                <SelectField label="Teams" value={teams} onChange={(value) => { setTeams(Number(value)); setPlayoffTeams(null); }}
                  options={TEAM_COUNTS.map((count) => ({ value: count, label: `${count} teams` }))} />
                <SelectField label="Draft slot" value={chosenSlot} onChange={setSlot}
                  options={[{ value: RANDOM, label: 'Random' }, ...Array.from({ length: teams }, (_, index) => ({ value: String(index + 1), label: `Pick ${index + 1}` }))]} />
                <SelectField label="Playoffs" value={chosenEnd} onChange={(value) => setPlayoffEnd(Number(value) as ReplayPlayoffEndWeek)}
                  options={endWeeks.map((end) => ({ value: end, label: playoffWindowLabel(end, chosenPlayoffTeams) }))} />
                <SelectField label="Playoff teams" value={chosenPlayoffTeams} onChange={(value) => setPlayoffTeams(Number(value))}
                  options={playoffCounts.map((count) => ({ value: count, label: playoffTeamsLabel(count) }))} />
              </div>
              <UnpreparedSeasons seasons={data.results} live={liveWaiting} prepare={data.prepare} isStarting={prepare.status === 'pending'}
                wasStartedHere={prepare.status === 'success'} onPrepare={startPrepare} />
              {isPollFailure && <p className="hint" role="status">Couldn't check the progress just now. Trying again…</p>}
              {prepare.status === 'error' && <p className="error-text" role="alert">{errorMessage(prepare.error)}</p>}
              <LeagueFormatFields starters={starters} bench={bench} onChange={setStarters} onBench={setBench} />
              <ScoringFields scoring={scoring} rules={rules} onScoring={setScoring} onRules={setRules} />
              <RadioCards legend="Waivers" options={WAIVER_MODES} value={waiverMode} onChange={setWaiverMode} />
              <RadioCards legend="Difficulty" options={DIFFICULTIES} value={difficulty} onChange={setDifficulty} />
            </fieldset>
            <div className="settings-form__footer">
              {/* Busy: aria-disabled, not disabled, so it keeps focus; the isSubmitting guard makes it inert. */}
              <button ref={submitRef} type="submit" className="btn btn--primary" disabled={!canCreate} aria-disabled={isCreating}>
                {isCreating ? (sim ? 'Simulating the season…' : 'Creating your league…') : 'Create league and draft'}
              </button>
              <div role="status">
                {isCreating && sim && (
                  <p className="hint">Simulating the {sim.season} season for your league. This takes about 10 seconds.</p>
                )}
              </div>
              {create.status === 'error' && <p className="error-text" role="alert">{errorMessage(create.error)}</p>}
            </div>
            <p className="hint attribution">{adpSource(sim?.kind ?? 'replay', isNow)}</p>
          </form>
        );
      }}
    </AsyncView>
  );
}
