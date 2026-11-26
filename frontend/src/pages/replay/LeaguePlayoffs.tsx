import { useId } from 'react';
import { getReplayPlayoffs } from '../../api/replay';
import type { ReplayBracketGame, ReplayBracketPool, ReplayBracketRound, ReplayBracketSide, ReplayPlayoffs, ReplaySeed } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { TeamBadge } from './gameUi';
import { formatPoints, ordinal, viewHash, type ReplayViewProps } from './shared';

function isMine(game: ReplayBracketGame): boolean {
  return Boolean(game.home?.is_user || game.away?.is_user);
}

function Score({ side, game }: { side: ReplayBracketSide; game: ReplayBracketGame }) {
  if (side.points !== null) return <span className="bracket-game__score">{formatPoints(side.points)}</span>;
  if (side.projected !== null) return <span className="bracket-game__score bracket-game__score--proj"><span className="sr-only">projected </span>{formatPoints(side.projected)}</span>;
  return game.winner === null ? null : <span className="bracket-game__score" />;
}

function Line({ side, game }: { side: ReplayBracketSide | null; game: ReplayBracketGame }) {
  if (!side) {
    return (
      <div className="bracket-game__line bracket-game__line--tbd">
        <span className="bracket-game__seed" aria-hidden="true">–</span>
        <span>To be decided</span>
      </div>
    );
  }
  const won = game.winner === side.team_id;
  const lost = game.winner !== null && !won;
  return (
    <div className={`bracket-game__line${won ? ' is-winner' : ''}${lost ? ' is-loser' : ''}`}>
      <span className="bracket-game__seed"><span className="sr-only">Seed </span>{side.seed}</span>
      <TeamBadge team={side} />
      <Score side={side} game={game} />
      {won && <span className="sr-only">won</span>}
    </div>
  );
}

/** Where a bracket game's link goes: the league's game list knows weeks up to the current one (`now`). */
interface Link {
  gameId: number;
  now: number;
}

/** The Matchup tab on this game; null while its teams aren't set or its week hasn't come (no matchup exists yet). */
function matchupHref(game: ReplayBracketGame, week: number, link: Link, projected: boolean): string | null {
  if (!game.home || !game.away || projected || week > link.now) return null;
  return viewHash(link.gameId, 'matchup', { week, teamId: game.home.team_id });
}

interface GameCardProps {
  game: ReplayBracketGame;
  week: number;
  link: Link;
  emphasis?: boolean;
  projected?: boolean;
}

function GameCard({ game, week, link, emphasis = false, projected = false }: GameCardProps) {
  const href = matchupHref(game, week, link, projected);
  const classes = ['bracket-game', isMine(game) ? 'bracket-game--mine' : '', emphasis ? 'bracket-game--final' : '',
    !game.home ? 'bracket-game--tbd' : '', href ? 'bracket-game--link' : ''].filter(Boolean).join(' ');
  const body = (
    <>
      {emphasis && <p className="bracket-game__label"><Icon name="trophy" size={14} /> {game.label}</p>}
      {!emphasis && game.label === 'Third place' && <p className="bracket-game__label">{game.label}</p>}
      <Line side={game.home} game={game} />
      <Line side={game.away} game={game} />
    </>
  );
  return (
    <li className={classes}>
      {href
        ? <a className="bracket-game__link" href={href}>{body}<span className="sr-only">: open the week {week} matchup</span></a>
        : body}
    </li>
  );
}

function ByeCard({ team }: { team: ReplaySeed }) {
  return (
    <li className={`bracket-game bracket-game--bye${team.is_user ? ' bracket-game--mine' : ''}`}>
      <div className="bracket-game__line">
        <span className="bracket-game__seed"><span className="sr-only">Seed </span>{team.seed}</span>
        <TeamBadge team={team} />
        <span className="bracket-game__bye">Bye</span>
      </div>
    </li>
  );
}

function Round({ round, isLast, link }: { round: ReplayBracketRound; isLast: boolean; link: Link }) {
  return (
    <li className="bracket__round">
      <h3 className="bracket__head">
        {round.name}
        <span className="bracket__week">Week {round.week}</span>
        {round.projected && <span className="status-badge bracket__projected">Projected</span>}
      </h3>
      <ol className={isLast ? 'bracket__games bracket__games--final' : 'bracket__games'}>
        {round.byes.map((team) => <ByeCard key={team.team_id} team={team} />)}
        {round.games.map((game, index) => (
          <GameCard key={`${round.week}-${index}`} game={game} week={round.week} link={link} emphasis={isLast && index === 0} projected={round.projected} />
        ))}
      </ol>
    </li>
  );
}

function placesText([first, last]: [number, number]): string {
  return first === last ? `${ordinal(first)} place` : `${ordinal(first)}–${ordinal(last)} place`;
}

/** Three week columns can overflow the page (two always fit), so only then can the keyboard focus it to scroll. */
const scrollable = (columns: number) => (columns >= 3 ? 0 : undefined);

function Pool({ pool, link }: { pool: ReplayBracketPool; link: Link }) {
  const headingId = useId();
  return (
    <section className="bracket-pool" aria-labelledby={headingId}>
      <h3 id={headingId} className="bracket-pool__head">{placesText(pool.places)} <span className="bracket__week">{pool.name}</span></h3>
      <p className="hint">
        {pool.teams
          ? <>Teams: {pool.teams.map((team, index) => <span key={team.team_id}>{index > 0 && ', '}{team.is_user ? `${team.name} (you)` : team.name}</span>)}</>
          : `The ${pool.size} teams that lose in round one play on here from week ${pool.first_week}.`}
      </p>
      <ol className="bracket" aria-label={`${pool.name} weeks`} tabIndex={scrollable(pool.weeks.length)}>
        {pool.weeks.map((week, index) => (
          <li key={week.week} className="bracket__round">
            <h4 className="bracket__head">Week {week.week}</h4>
            {week.known
              ? <ol className="bracket__games">{week.games.map((game, gameIndex) => <GameCard key={gameIndex} game={game} week={week.week} link={link} />)}</ol>
              : <p className="bracket-pool__wait">{index === 0 ? 'Paired once the teams are known.' : `Paired from the pool’s results after week ${week.week - 1}.`}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

function statusText(data: ReplayPlayoffs, regularWeeks: number): string {
  if (data.champion) return '';
  if (!data.seeds_final) return `The seeds lock after week ${regularWeeks}. Round one below uses today’s standings.`;
  return 'Each round is reseeded: the best seed left plays the lowest. A tie goes to the higher seed.';
}

/** The playoffs as a bracket: the title rounds week by week (open slots until decided), then the consolation pools. */
export function LeaguePlayoffs({ game, version }: ReplayViewProps) {
  const playoffs = useAsync((signal) => getReplayPlayoffs(game.id, signal), [game.id, version]);
  const link = { gameId: game.id, now: game.week };
  return (
    <AsyncView state={playoffs} loading={<Skeleton lines={8} label="Loading the playoff bracket…" />} errorTitle="Couldn't load the playoff bracket">
      {(data) => {
        const status = statusText(data, game.settings.regular_season_weeks);
        return (
          <div className="replay-playoffs">
            <section aria-labelledby="playoffs-title">
              <h2 id="playoffs-title" className="section-title">Playoffs <span className="bracket__week">Weeks {data.weeks[0]}–{data.weeks.at(-1)}</span></h2>
              {data.champion && (
                <p className={`replay-playoffs__champion${data.champion.is_user ? ' replay-playoffs__champion--mine' : ''}`}><Icon name="trophy" size={18} /> Champion: <strong>{data.champion.is_user ? `${data.champion.name} (you)` : data.champion.name}</strong></p>
              )}
              {status && <p className="hint">{status}</p>}
              <p className="hint">Select a game to open its matchup. A game opens once its teams are set and its week has come.</p>
              <ol className="bracket bracket--title" aria-label="Title bracket" tabIndex={scrollable(data.title.length)}>
                {data.title.map((round, index) => <Round key={round.week} round={round} isLast={index === data.title.length - 1} link={link} />)}
              </ol>
            </section>
            {data.pools.length > 0 && (
              <section aria-labelledby="consolation-title">
                <h2 id="consolation-title" className="section-title">Consolation</h2>
                <p className="hint">Everyone else plays on for the places below the title games: each week a pool is ranked by its own wins, then points, and paired 1 v 2, 3 v 4. Final places in a pool go by that table after its last week (pool wins, then points in pool weeks, then seed), not by who beat whom: a team can win a consolation game and still finish behind the team it beat.</p>
                {data.pools.map((pool) => <Pool key={pool.name} pool={pool} link={link} />)}
              </section>
            )}
          </div>
        );
      }}
    </AsyncView>
  );
}
