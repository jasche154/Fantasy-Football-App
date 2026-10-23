import { getScheduleStrength } from '../../api/data';
import type { DefenseStrengthRow, OffenseScheduleRow, Position, ScheduleStrengthResponse } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { factorLabel, factorTone } from '../../lib/matchup';
import { useLeagueSettings } from '../../state/SettingsContext';

function FactorPill({ factor }: { factor: number }) {
  return <span className={`sos-pill sos-pill--${factorTone(factor)}`}>{factorLabel(factor)}</span>;
}

function DefenseTable({ rows, position }: { rows: readonly DefenseStrengthRow[]; position: Position }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">{`Defenses ranked by opponent-adjusted fantasy points allowed to ${position}s`}</caption>
        <thead>
          <tr>
            <th scope="col" className="num">#</th>
            <th scope="col">Defense</th>
            <th scope="col" className="num" title="Points allowed vs what the offenses it faced usually score">Adjusted</th>
            <th scope="col" className="num" title={`Raw fantasy points allowed to ${position}s per game`}>Raw / gm</th>
            <th scope="col" className="num">GP</th>
            <th scope="col">This week</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.team}>
              <td className="num td--rank">{row.rank}</td>
              <td><strong>{row.team}</strong></td>
              <td className="num"><FactorPill factor={row.sos_factor} /></td>
              <td className="num">{formatNumber(row.allowed_per_game)}</td>
              <td className="num">{row.games}</td>
              <td>{row.this_week_opponent ? `vs ${row.this_week_opponent} offense` : 'Bye'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OffenseTable({ rows }: { rows: readonly OffenseScheduleRow[] }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">Each offense's upcoming opponents and rest-of-season schedule strength</caption>
        <thead>
          <tr>
            <th scope="col">Team</th>
            <th scope="col">Next opponents</th>
            <th scope="col" className="num" title="Average opponent-adjusted factor over the rest of the season">Rest of season</th>
            <th scope="col" className="num">Games</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.team}>
              <td><strong>{row.team}</strong></td>
              <td>
                <ul className="opponent-run">
                  {row.next_weeks.map((game, index) => (
                    <li key={`${game.opponent}-${index}`} className={`opponent-chip sos-pill--${factorTone(game.sos_factor)}`}
                      title={`${game.opponent}: ${factorLabel(game.sos_factor)}`}>
                      {game.opponent}
                    </li>
                  ))}
                </ul>
              </td>
              <td className="num"><FactorPill factor={row.rest_of_season} /></td>
              <td className="num">{row.games_left}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface ScheduleStrengthViewProps {
  position: Position;
  week: number | undefined;
}

export function ScheduleStrengthView({ position, week }: ScheduleStrengthViewProps) {
  const { settings } = useLeagueSettings();
  const strength = useAsync(
    (signal) => getScheduleStrength(settings, position, week, signal),
    [position, week, settings],
  );
  return (
    <AsyncView
      state={strength}
      loading={<TableSkeleton rows={10} columns={6} label="Loading schedule strength…" />}
      isEmpty={(data: ScheduleStrengthResponse) => data.defenses.length === 0}
      empty={<EmptyState title="No schedule loaded yet.">Run populate_stats to load the schedule and game lines.</EmptyState>}
      errorTitle="Couldn't load schedule strength"
    >
      {(data) => (
        <div className="sos-grid">
          <Card title={`Defenses vs ${position}`} eyebrow="Opponent-adjusted">
            <DefenseTable rows={data.defenses} position={position} />
          </Card>
          <Card title="Upcoming schedules" eyebrow={`From week ${data.week}`}>
            <OffenseTable rows={data.offenses} />
          </Card>
        </div>
      )}
    </AsyncView>
  );
}
