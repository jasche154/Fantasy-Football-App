import { useId } from 'react';
import type { ReplayBoxLine, ReplayPlayerWeek, ReplayPosition, ReplayUsage } from '../../api/types';
import { EMPTY_VALUE, formatFraction, formatInteger, formatNumber, formatPercentValue } from '../../lib/format';
import { gamesPlayed, rate, RECENT_GAMES, seasonSummary, trend, type SeasonSummary, type Trend } from './advancedStats';
import { opponentText } from './PlayerGameLog';
import { passingText } from './playerColumns';

/** Positions the Advanced tab is for: K and DST have no snaps, targets or carries. */
export const ADVANCED_POSITIONS: readonly ReplayPosition[] = ['QB', 'RB', 'WR', 'TE'];

/** Snap % moves less than this many points (or target share less than 2 points) reads as steady. */
const SNAP_FLAT = 3;
const SHARE_FLAT = 0.02;

interface Game {
  usage: ReplayUsage;
  box: ReplayBoxLine | null;
}

interface Column {
  key: string;
  label: string;
  title: string;
  value: (game: Game) => string;
}

const COLUMNS: Record<string, Column> = {
  snap: { key: 'snap', label: 'Snap', title: 'Share of the offense\'s snaps', value: (g) => formatPercentValue(g.usage.snap_pct, 0) },
  tgt: { key: 'tgt', label: 'Tgt', title: 'Targets', value: (g) => formatInteger(g.box?.targets) },
  share: { key: 'share', label: 'Share', title: 'Target share: his share of the team\'s targets', value: (g) => formatFraction(g.usage.target_share, 0) },
  rec: { key: 'rec', label: 'Rec', title: 'Receptions', value: (g) => formatInteger(g.box?.rec) },
  catch: { key: 'catch', label: 'Ctch%', title: 'Catch rate: receptions per target', value: (g) => formatFraction(g.box && rate(g.box.rec, g.box.targets), 0) },
  ypt: { key: 'ypt', label: 'Y/Tgt', title: 'Receiving yards per target', value: (g) => formatNumber(g.box && rate(g.box.rec_yds, g.box.targets)) },
  ypr: { key: 'ypr', label: 'Y/Rec', title: 'Receiving yards per reception', value: (g) => formatNumber(g.box && rate(g.box.rec_yds, g.box.rec)) },
  car: { key: 'car', label: 'Car', title: 'Carries', value: (g) => formatInteger(g.box?.carries) },
  ypc: { key: 'ypc', label: 'Y/Car', title: 'Rushing yards per carry', value: (g) => formatNumber(g.box && rate(g.box.rush_yds, g.box.carries)) },
  touches: { key: 'touches', label: 'Tch', title: 'Touches: carries plus receptions', value: (g) => formatInteger(g.box && g.box.carries + g.box.rec) },
  cmpAtt: { key: 'cmpAtt', label: 'C/ATT', title: 'Completions / attempts', value: (g) => passingText(g.box) },
  passYds: { key: 'passYds', label: 'PaYd', title: 'Passing yards', value: (g) => formatInteger(g.box?.pass_yds) },
  tdInt: { key: 'tdInt', label: 'TD:INT', title: 'Passing touchdowns to interceptions', value: (g) => (g.box ? `${g.box.pass_td}:${g.box.pass_int}` : EMPTY_VALUE) },
  rushYds: { key: 'rushYds', label: 'RuYd', title: 'Rushing yards', value: (g) => formatInteger(g.box?.rush_yds) },
};

const RECEIVER = ['snap', 'tgt', 'share', 'rec', 'catch', 'ypt', 'ypr'];
const COLUMN_KEYS: Partial<Record<ReplayPosition, string[]>> = {
  QB: ['snap', 'cmpAtt', 'passYds', 'tdInt', 'car', 'rushYds', 'ypc'],
  RB: ['snap', 'car', 'ypc', 'touches', ...RECEIVER.slice(1)],
  WR: RECEIVER,
  TE: RECEIVER,
};

interface Tile {
  label: string;
  value: (s: SeasonSummary) => string;
}

const TILES: Record<string, Tile> = {
  games: { label: 'Games', value: (s) => String(s.games) },
  snap: { label: 'Snap %', value: (s) => formatPercentValue(s.snapPct, 0) },
  share: { label: 'Target share', value: (s) => formatFraction(s.targetShare, 0) },
  tgt: { label: 'Targets / game', value: (s) => formatNumber(s.targetsPerGame) },
  car: { label: 'Carries / game', value: (s) => formatNumber(s.carriesPerGame) },
  catch: { label: 'Catch %', value: (s) => formatFraction(s.catchPct, 0) },
  ypt: { label: 'Yds / target', value: (s) => formatNumber(s.ydsPerTarget) },
  ypc: { label: 'Yds / carry', value: (s) => formatNumber(s.ydsPerCarry) },
  cmp: { label: 'Cmp %', value: (s) => formatFraction(s.cmpPct, 0) },
};

const TILE_KEYS: Partial<Record<ReplayPosition, string[]>> = {
  QB: ['games', 'snap', 'cmp', 'car', 'ypc'],
  RB: ['games', 'snap', 'car', 'ypc', 'share', 'tgt', 'catch', 'ypt'],
  WR: ['games', 'snap', 'share', 'tgt', 'catch', 'ypt'],
  TE: ['games', 'snap', 'share', 'tgt', 'catch', 'ypt'],
};

const ARROWS = { up: '▲', down: '▼', flat: '▶' } as const;
const WORDS = { up: 'Up', down: 'Down', flat: 'Steady' } as const;

function TrendItem({ label, value, format }: { label: string; value: Trend | null; format: (n: number) => string }) {
  if (!value) return null;
  return (
    <li className={`replay-adv__trend replay-adv__trend--${value.direction}`}>
      <span aria-hidden="true">{ARROWS[value.direction]}</span> {label}: {WORDS[value.direction]}, {format(value.recent)} last {RECENT_GAMES} vs {format(value.season)} season
    </li>
  );
}

function Summary({ weeks, position }: { weeks: readonly ReplayPlayerWeek[]; position: ReplayPosition }) {
  const summary = seasonSummary(weeks);
  const games = gamesPlayed(weeks);
  const snap = trend(games.map((w) => w.usage?.snap_pct ?? null), SNAP_FLAT);
  const share = position === 'QB' ? null : trend(games.map((w) => w.usage?.target_share ?? null), SHARE_FLAT);
  return (
    <>
      <dl className="replay-card__tiles">
        {(TILE_KEYS[position] ?? []).map((key) => (
          <div key={key} className="replay-card__tile"><dt>{TILES[key].label}</dt><dd>{TILES[key].value(summary)}</dd></div>
        ))}
      </dl>
      {snap || share
        ? (
          <ul className="replay-adv__trends" aria-label="Trends">
            <TrendItem label="Snap %" value={snap} format={(n) => formatPercentValue(n, 0)} />
            <TrendItem label="Target share" value={share} format={(n) => formatFraction(n, 0)} />
          </ul>
        )
        : games.length <= RECENT_GAMES && <p className="hint">Trends show once he has played more than {RECENT_GAMES} games.</p>}
    </>
  );
}

interface PlayerAdvancedProps {
  name: string;
  position: ReplayPosition;
  weeks: readonly ReplayPlayerWeek[];
}

/** Usage and efficiency: season averages over the games he played, a last-3 trend, and the same numbers week by week. */
export function PlayerAdvanced({ name, position, weeks }: PlayerAdvancedProps) {
  const headingId = useId();
  const columns = (COLUMN_KEYS[position] ?? []).map((key) => COLUMNS[key]);
  return (
    <section className="replay-card__section replay-adv" aria-labelledby={headingId}>
      <h3 id={headingId} className="sr-only">Advanced stats</h3>
      <Summary weeks={weeks} position={position} />
      <div className="table-wrap">
        <table className="data-table data-table--compact replay-card__table">
          <caption className="sr-only">{name} usage by week</caption>
          <thead>
            <tr>
              <th scope="col" className="num">Wk</th>
              <th scope="col">Opp</th>
              {columns.map(({ key, label, title }) => <th key={key} scope="col" className="num"><abbr title={title}>{label}</abbr></th>)}
            </tr>
          </thead>
          <tbody>
            {weeks.map((week) => {
              // Byes, weeks still to play and games he missed have no usage: every cell is a dash.
              const game = week.played && week.usage ? { usage: week.usage, box: week.box } : null;
              return (
                <tr key={week.week}>
                  <th scope="row" className="num">{week.week}</th>
                  <td>{opponentText(week.opponent, week.away, week.bye)}</td>
                  {columns.map(({ key, value }) => <td key={key} className="num">{game ? value(game) : EMPTY_VALUE}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">Averages cover the games he played. Snap % and target share are his share of the offense; a dash means he did not play.</p>
    </section>
  );
}
