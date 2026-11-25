/**
 * Replay League: what every screen shares. Screens import from here (and PlayerDrawer is opened through the hub).
 *
 * Every view (DraftView, TeamView, MatchupView, PlayersView, TradesView, LeagueView) receives ReplayViewProps:
 *   game        the latest Summary; replaced after every successful `act`
 *   version     bumps whenever the game changed: put it in your useAsync deps so the screen reloads
 *   act(run)    the ONLY way to send a POST that answers {game, events}. It swaps in the new summary, bumps
 *               `version`, shows the events in the hub's "What just happened" panel and resolves with the full
 *               response (so extras like `verdict` are yours). On failure it resolves undefined and the hub
 *               shows the message in its role="alert"; a 409 also reloads the summary. One act at a time:
 *               a call made while another is in flight is ignored (resolves undefined).
 *   isBusy      an act is in flight, or has just landed and `version` is new in this very render: disable your
 *               action buttons. Your own reload starts right after, so also disable them while your useAsync
 *               is 'loading': until it answers, the rows on screen are from before the action.
 *   actError    the last failed act's message (null once the next act starts), for showing next to a control
 *               the hub's alert may be far from, e.g. inside a dialog. Don't repeat it otherwise.
 *   openPlayer  opens the hub's player drawer for a player id
 * POSTs that answer something else (evaluateReplayTrade) are called directly, not through `act`.
 *
 * Pieces: ReplayPlayerCell, ReplayHypeStar (+ hypeLabel), ReplayBuzzChip (+ buzzLabel), ReplayInjuryBadge (+ isHardTag, canGoOnIr, injuryLabel), ReplayIrBlockedNotice, ReplayPositionChip, PositionTabs (+ positionParam), TeamSelect, ConfirmButton,
 * useKeyedAct (act with a pending state and a refusal per button), ReplayDrawer (the dialog frame, in ReplayDrawer.tsx),
 * EventList (+ groupEvents), formatPoints, formatRecord, ordinal, viewHash, REPLAY_VIEWS.
 */
import { useState } from 'react';
import { errorMessage } from '../../api/client';
import { Icon } from '../../components/Icon';
import type {
  ReplayActionResult,
  ReplayEvent,
  ReplayEventKind,
  ReplayInjury,
  ReplayInjuryTag,
  ReplayPlayerRef,
  ReplayPlayerRow,
  ReplayPosition,
  ReplayPositionFilter,
  ReplayPractice,
  ReplayRecord,
  ReplaySummary,
} from '../../api/types';
import { SelectField } from '../../components/SelectField';
import { formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';

export type ReplayAct = <T extends ReplayActionResult>(run: () => Promise<T>) => Promise<T | undefined>;

export interface ReplayViewProps {
  game: ReplaySummary;
  version: number;
  act: ReplayAct;
  isBusy: boolean;
  actError: string | null;
  openPlayer: (playerId: number) => void;
}

// ---------------- tabs and the URL ----------------

export type ReplayView = 'team' | 'matchup' | 'players' | 'trades' | 'league';

export const REPLAY_VIEWS: readonly { value: ReplayView; label: string }[] = [
  { value: 'team', label: 'My Team' },
  { value: 'matchup', label: 'Matchup' },
  { value: 'players', label: 'Players' },
  { value: 'trades', label: 'Trades' },
  { value: 'league', label: 'League' },
];

/** The game screen's URL for a tab (#/play?game=ID&view=...). */
export function viewHash(gameId: number, view: ReplayView): string {
  return buildHash(paths.play, { game: gameId, view });
}

// ---------------- formatting ----------------

/** Fantasy points to two decimals (scores are decided by hundredths); "—" for null. */
export function formatPoints(value: number | null | undefined): string {
  return formatNumber(value, 2);
}

/** "3-1" or "3-1-1". */
export function formatRecord(record: Pick<ReplayRecord, 'wins' | 'losses' | 'ties'>): string {
  return `${record.wins}-${record.losses}${record.ties ? `-${record.ties}` : ''}`;
}

/** 1 -> "1st", 12 -> "12th". */
export function ordinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? 'th' : (['th', 'st', 'nd', 'rd'][n % 10] ?? 'th');
  return `${n}${suffix}`;
}

// ---------------- players ----------------

/** The house position chip, with tones for K and DST as well. */
export function ReplayPositionChip({ position }: { position: ReplayPosition }) {
  return <span className={`pos-chip pos-chip--${position.toLowerCase()} pos-chip--sm`}>{position}</span>;
}

/** The week facts shown next to a name; any PlayerRow (roster, wire, claim) can be passed as is. */
export type ReplayPlayerInfo = Partial<Pick<ReplayPlayerRow, 'opponent' | 'bye' | 'out' | 'trade_locked' | 'buzz'>>;

interface ReplayPlayerCellProps {
  player: ReplayPlayerRef;
  info?: ReplayPlayerInfo;
  /** Pass the view's `openPlayer` to make the name a button; without it the name is plain text. */
  onOpen?: (playerId: number) => void;
}

const TAG_WORDS: Record<ReplayInjuryTag, string> = {
  Q: 'Questionable', D: 'Doubtful', O: 'Out', IR: 'Injured reserve', PUP: 'Physically unable to perform', SUS: 'Suspended',
  EXE: "Commissioner's exempt list",
};
const PRACTICE_WORDS: Record<ReplayPractice, string> = { DNP: 'did not practice', LP: 'limited practice', FP: 'full practice' };
const HARD_TAGS: readonly ReplayInjuryTag[] = ['O', 'IR', 'PUP', 'SUS', 'EXE'];
const IR_TAGS: readonly ReplayInjuryTag[] = ['O', 'IR', 'PUP'];

/** O, IR, PUP, SUS, EXE: he does not play this week. */
export function isHardTag(injury: ReplayInjury | null | undefined): boolean {
  return !!injury && HARD_TAGS.includes(injury.tag);
}

/** O, IR, PUP: he may go on IR (a suspension is not an injury). */
export function canGoOnIr(injury: ReplayInjury | null | undefined): boolean {
  return !!injury && IR_TAGS.includes(injury.tag);
}

/** "Questionable: Hamstring (limited practice)", "Injured reserve: Groin", "Suspended". */
export function injuryLabel({ tag, practice, injury }: ReplayInjury): string {
  return `${TAG_WORDS[tag]}${injury ? `: ${injury}` : ''}${practice ? ` (${PRACTICE_WORDS[practice]})` : ''}`;
}

/** Yahoo-style tag: the short code on screen, the full label for hover and screen readers. */
export function ReplayInjuryBadge({ injury }: { injury: ReplayInjury }) {
  const label = injuryLabel(injury);
  return (
    <span className={`status-badge status-badge--${isHardTag(injury) ? 'out' : 'missing'}`} title={label}>
      <span aria-hidden="true">{injury.tag}</span><span className="sr-only">{label}</span>
    </span>
  );
}

/** "Hype: role jumped after he won the backfield". */
export function hypeLabel(player: ReplayPlayerRef): string | null {
  return player.hype ? `Hype: role jumped after ${player.hype.reason}` : null;
}

/** A small star after the name of a player the trade market is hyping; the reason on hover and for screen readers. */
export function ReplayHypeStar({ player }: { player: ReplayPlayerRef }) {
  const label = hypeLabel(player);
  if (!label) return null;
  return (
    <span className="replay-hype" title={label}>
      <span aria-hidden="true">★</span><span className="sr-only">{label}</span>
    </span>
  );
}

/** "Buzz: Beat reporters say his role is growing; expect more work." The name starts with the chip's visible
 * text (WCAG 2.5.3, so "click Buzz" reaches it); the tooltip says it is a rumor. */
export function buzzLabel(text: string): string {
  return `Buzz: ${text}`;
}

/** Where a league's draft ADP comes from: real drafts for a past or full live season, the simulated market for
 * a league that drafts mid-season or plays a future season. */
export function adpSource(kind: ReplaySummary['kind'], startsNow: boolean): string {
  if (kind === 'future' || startsNow) return 'ADP: the simulated market for this league';
  return kind === 'live' ? 'ADP: MyFantasyLeague / Fantasy Football Calculator' : 'ADP: Fantasy Football Calculator';
}

/** A small "Buzz" chip for a simulated season's news line about his role: a button that opens his card, whose News
 * block shows the full line (the line is also its name and tooltip). Quiet on purpose: a rumor, not an injury or a fact. */
export function ReplayBuzzChip({ text, onOpen }: { text: string | null | undefined; onOpen?: () => void }) {
  if (!text) return null;
  const label = buzzLabel(text);
  const tooltip = `Buzz (a rumor): ${text}`;
  const content = <><Icon name="news" size={12} /><span aria-hidden="true">Buzz</span></>;
  if (!onOpen) return <span className="replay-buzz" role="img" aria-label={label} title={tooltip}>{content}</span>;
  return (
    <button type="button" className="replay-buzz" aria-label={label} aria-haspopup="dialog" title={tooltip} onClick={onOpen}>{content}</button>
  );
}

/** The plain Out badge, unless a hard tag already says so. */
export function showsOut(out: boolean | undefined, injury: ReplayInjury | null | undefined): boolean {
  return !!out && !isHardTag(injury);
}

/** Why "Out" can sit next to a Q or D tag: the tag is the week's injury report, Out is the pre-game projection. */
export const OUT_REASON = 'Out: no projection for this game, so he is not expected to play';

/** The plain Out badge (see showsOut), its reason on hover. */
export function ReplayOutBadge({ className = '' }: { className?: string }) {
  return <span className={`status-badge status-badge--out${className ? ` ${className}` : ''}`} title={OUT_REASON}>Out</span>;
}

/** The user's IR players who lost their tag block adds, claims and trades; null when nothing is blocked. */
export function irBlockedText(game: ReplaySummary): string | null {
  if (game.ir_blocked.length === 0 || !EDIT_PHASES.includes(game.phase)) return null;
  return `${game.ir_blocked.map((player) => player.name).join(' and ')} is on IR but no longer has an O, IR or PUP tag. `
    + 'Activate or drop him before you add, claim or trade for players.';
}

export function ReplayIrBlockedNotice({ game }: { game: ReplaySummary }) {
  const text = irBlockedText(game);
  return text ? <p className="notice" role="status">{text}</p> : null;
}

/** The backend takes roster moves in these phases only (replay_waivers.ROSTER_PHASES, replay_ir.IR_PHASES). */
export const EDIT_PHASES: readonly string[] = ['open', 'waivers'];

/** Name, hype star, position chip, NFL team, opponent or Bye, injury tag, and Out / Trade-locked badges. */
export function ReplayPlayerCell({ player, info = {}, onOpen }: ReplayPlayerCellProps) {
  return (
    <span className="player-cell">
      {onOpen ? (
        <button type="button" className="replay-player__name" onClick={() => onOpen(player.id)}>{player.name}</button>
      ) : (
        <span className="replay-player__name">{player.name}</span>
      )}
      <ReplayHypeStar player={player} />
      <span className="player-cell__meta">
        <ReplayPositionChip position={player.position} />
        {player.team && <span className="player-cell__team">{player.team}</span>}
        {info.opponent && !info.bye && <span className="player-cell__team">vs {info.opponent}</span>}
        {info.bye && <span className="status-badge status-badge--missing">Bye</span>}
        {player.injury && <ReplayInjuryBadge injury={player.injury} />}
        {showsOut(info.out, player.injury) && <ReplayOutBadge />}
        <ReplayBuzzChip text={info.buzz} onOpen={onOpen && (() => onOpen(player.id))} />
        {info.trade_locked && (
          <span className="status-badge status-badge--missing" title="Traded in the last two weeks, so he can't be traded yet">Trade-locked</span>
        )}
      </span>
    </span>
  );
}

export type ReplayPositionTab = 'ALL' | ReplayPositionFilter;

const POSITION_TABS: readonly ReplayPositionTab[] = ['ALL', 'QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST'];

/** A tab as the `position` query param (ALL -> undefined). */
export function positionParam(tab: ReplayPositionTab): ReplayPositionFilter | undefined {
  return tab === 'ALL' ? undefined : tab;
}

interface PositionTabsProps {
  value: ReplayPositionTab;
  onChange: (value: ReplayPositionTab) => void;
  /** Adds FLEX (RB/WR/TE). */
  withFlex?: boolean;
  label?: string;
}

/** Segmented position filter: All / QB / RB / WR / TE / (FLEX) / K / DST. */
export function PositionTabs({ value, onChange, withFlex = false, label = 'Filter by position' }: PositionTabsProps) {
  return (
    <div className="segmented replay-segmented" role="group" aria-label={label}>
      {POSITION_TABS.filter((tab) => withFlex || tab !== 'FLEX').map((tab) => (
        <button key={tab} type="button" aria-pressed={tab === value} onClick={() => onChange(tab)}
          className={`segmented__option segmented__option--${tab.toLowerCase()}${tab === value ? ' is-active' : ''}`}>
          {tab === 'ALL' ? 'All' : tab}
        </button>
      ))}
    </div>
  );
}

// ---------------- controls ----------------

interface TeamSelectProps {
  game: ReplaySummary;
  value: number;
  onChange: (teamId: number) => void;
  label?: string;
  /** Leave the user's team out (e.g. picking a trade partner). */
  excludeUser?: boolean;
  disabled?: boolean;
}

/** The league's teams, the user's first. */
export function TeamSelect({ game, value, onChange, label = 'Team', excludeUser = false, disabled }: TeamSelectProps) {
  const teams = [...game.teams.filter((team) => team.is_user && !excludeUser), ...game.teams.filter((team) => !team.is_user)];
  const options = teams.map((team) => ({ value: team.team_id, label: team.is_user ? `${team.name} (you)` : team.name }));
  return <SelectField label={label} isInline value={value} options={options} disabled={disabled} onChange={(id) => onChange(Number(id))} />;
}

interface ConfirmButtonProps {
  label: string;
  /** The text once armed, e.g. "Yes, delete". */
  confirmLabel: string;
  onConfirm: () => void;
  disabled?: boolean;
  className?: string;
  /** What it acts on (a player, a league), read after the text by screen readers only, so each row's button has its own name. */
  subject?: string;
}

/** A two-step button for moves that can't be undone: the first press arms it, the second confirms. */
export function ConfirmButton({ label, confirmLabel, onConfirm, disabled, className = 'btn btn--ghost btn--sm', subject }: ConfirmButtonProps) {
  const [isArmed, setIsArmed] = useState(false);
  const press = () => {
    setIsArmed(!isArmed);
    if (isArmed) onConfirm();
  };
  return (
    <span className="replay-confirm">
      <button type="button" className={className} disabled={disabled} onClick={press}>
        {isArmed ? confirmLabel : label}
        {subject && <>{' '}<span className="sr-only">{subject}</span></>}
      </button>
      {isArmed && <button type="button" className="btn btn--ghost btn--sm" onClick={() => setIsArmed(false)}>Cancel</button>}
    </span>
  );
}

// ---------------- actions and dialogs ----------------

/** `act` with a name for each button, so the one pressed shows its own pending text and its own refusal. */
export interface KeyedAct {
  send: <T extends ReplayActionResult>(key: string, run: () => Promise<T>) => Promise<T | undefined>;
  /** An act is in flight, here or anywhere in the hub: disable every action button. */
  isBusy: boolean;
  /** The key of this screen's action in flight. */
  pendingKey: string | null;
  /** The last action sent from here, when the server refused it (null once the next one starts). */
  failure: { key: string; message: string } | null;
  errorFor: (key: string) => string | null;
}

/** The hub's alert still announces a refusal; `failure` is for showing it next to the button, without a role. */
export function useKeyedAct(act: ReplayAct, isBusy: boolean): KeyedAct {
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [failure, setFailure] = useState<KeyedAct['failure']>(null);
  const send: KeyedAct['send'] = async (key, run) => {
    setPendingKey(key);
    setFailure(null);
    const result = await act(() => run().catch((error: unknown) => {
      setFailure({ key, message: errorMessage(error) });
      throw error;
    }));
    setPendingKey(null);
    return result;
  };
  return { send, pendingKey, failure, isBusy: isBusy || pendingKey !== null, errorFor: (key) => (failure?.key === key ? failure.message : null) };
}

export { ReplayDrawer } from './ReplayDrawer';

// ---------------- events ----------------

const EVENT_GROUPS: readonly { title: string; kinds: readonly ReplayEventKind[] }[] = [
  { title: 'Results', kinds: ['result', 'playoffs', 'champion'] },
  { title: 'Waiver results', kinds: ['claim'] },
  { title: 'Trades', kinds: ['trade'] },
  { title: 'Adds and drops', kinds: ['add', 'drop', 'ir'] },
  { title: 'Draft picks', kinds: ['draft'] },
];

export interface ReplayEventGroup {
  title: string;
  events: ReplayEvent[];
}

/** Events by kind, results first; empty groups are left out and the order inside a group is kept. */
export function groupEvents(events: readonly ReplayEvent[]): ReplayEventGroup[] {
  return EVENT_GROUPS.map((group) => ({ title: group.title, events: events.filter((event) => group.kinds.includes(event.kind)) }))
    .filter((group) => group.events.length > 0);
}

interface EventListProps {
  events: readonly ReplayEvent[];
  /** Prefix each line with its week (for logs that span weeks). */
  showWeek?: boolean;
}

/** A plain list of event lines (result lines carry the score); the user's own are marked. */
export function EventList({ events, showWeek = false }: EventListProps) {
  return (
    <ul className="replay-events">
      {events.map((event) => (
        <li key={event.id} className={event.team?.is_user ? 'replay-events__item replay-events__item--mine' : 'replay-events__item'}>
          {showWeek && <span className="replay-events__week">{event.week > 0 ? `Wk ${event.week}` : 'Draft'}</span>}
          {event.text}
        </li>
      ))}
    </ul>
  );
}
