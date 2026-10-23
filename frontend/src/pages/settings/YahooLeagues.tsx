import { getYahooLeagues, importYahooLeague } from '../../api/actions';
import { errorMessage } from '../../api/client';
import type { YahooImportResult, YahooLeague } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAction } from '../../hooks/useAction';
import { useAsync } from '../../hooks/useAsync';
import { pluralize } from '../../lib/format';

export function importSummary(result: YahooImportResult): string {
  const base = `Imported as league #${result.league_id}: ${pluralize(result.teams, 'team')}, ${pluralize(result.roster_spots, 'roster spot')}.`;
  if (result.unmatched_players.length === 0) return `${base} Every player was matched.`;
  return `${base} ${pluralize(result.unmatched_players.length, 'player')} could not be matched: ${result.unmatched_players.join(', ')}.`;
}

function LeagueRow({ league }: { league: YahooLeague }) {
  const importer = useAction(importYahooLeague);
  const isPending = importer.status === 'pending';
  return (
    <li className="league-row">
      <div className="league-row__info">
        <p className="league-row__name">{league.name}</p>
        <p className="muted">
          {league.season} · {pluralize(league.num_teams, 'team')} · {league.scoring_type}
        </p>
      </div>
      <button
        type="button"
        className="btn btn--primary btn--sm"
        onClick={() => void importer.run(league.league_key)}
        disabled={isPending}
        aria-label={`Import ${league.name}`}
      >
        {isPending ? 'Importing…' : importer.status === 'success' ? 'Re-import' : 'Import'}
      </button>
      <div className="league-row__result" aria-live="polite">
        {importer.status === 'success' && <p className="success-text">{importSummary(importer.data)}</p>}
        {importer.status === 'error' && <p className="error-text" role="alert">Import failed: {errorMessage(importer.error)}</p>}
      </div>
    </li>
  );
}

export function YahooLeagues() {
  const leagues = useAsync((signal) => getYahooLeagues(signal), []);
  return (
    <div className="yahoo-leagues">
      <h3 className="subheading">Your Yahoo leagues</h3>
      <AsyncView
        state={leagues}
        loading={<Skeleton lines={3} label="Loading Yahoo leagues…" />}
        errorTitle="Couldn't load your Yahoo leagues"
        isEmpty={(data) => data.leagues.length === 0}
        empty={<EmptyState title="No Yahoo football leagues found on this account." />}
      >
        {(data) => (
          <ul className="league-list">
            {data.leagues.map((league) => <LeagueRow key={league.league_key} league={league} />)}
          </ul>
        )}
      </AsyncView>
    </div>
  );
}
