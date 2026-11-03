import { getOdds } from '../../api/data';
import type { OddsResponse } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { paths, parsePositiveInt } from '../../router/routes';
import { navigate } from '../../router/useHashLocation';
import { OddsGamesTable } from './OddsGamesTable';

const LAST_WEEK = 18;
const WEEK_OPTIONS = Array.from({ length: LAST_WEEK }, (_, index) => ({ value: index + 1, label: `Week ${index + 1}` }));

/** A week from the hash, or undefined (the backend picks the current week). */
export function weekFromQuery(value: string | undefined): number | undefined {
  const week = parsePositiveInt(value);
  return week !== null && week <= LAST_WEEK ? week : undefined;
}

function OddsBody({ data }: { data: OddsResponse }) {
  if (data.games.length === 0) {
    return <EmptyState title={`No games on the ${data.season} schedule for week ${data.week}.`} />;
  }
  return (
    <>
      {data.note && <p className="note odds-note">{data.note}</p>}
      <OddsGamesTable key={data.week} games={data.games} />
    </>
  );
}

export function OddsPage({ query }: { query: Readonly<Record<string, string>> }) {
  useDocumentTitle('Odds & Props');
  const week = weekFromQuery(query.week);
  const odds = useAsync((signal) => getOdds(undefined, week, signal), [week]);
  const shownWeek = week ?? odds.data?.week;
  return (
    <>
      <PageHeader
        title="Odds & Props"
        subtitle="Closing spreads and totals from the nflverse schedule, implied team totals, and the player props already stored. Nothing on this page buys new odds."
        actions={
          <SelectField
            label="Week"
            isInline
            value={shownWeek ?? ''}
            options={shownWeek ? WEEK_OPTIONS : [{ value: '', label: 'Current week' }, ...WEEK_OPTIONS]}
            onChange={(value) => navigate(paths.odds, { week: value }, { replace: true })}
          />
        }
      />
      <Card title={shownWeek ? `Week ${shownWeek} games` : 'Games'} eyebrow="Vegas lines · stored props">
        <AsyncView state={odds} loading={<TableSkeleton rows={8} columns={8} label="Loading odds…" />}>
          {(data) => <OddsBody data={data} />}
        </AsyncView>
        <p className="note">
          Implied team total = (total ± spread) / 2. Props are bought once per game within 2 hours of kickoff to save
          API credits; each player's numbers are the expected stats implied by the betting lines.
        </p>
      </Card>
    </>
  );
}
