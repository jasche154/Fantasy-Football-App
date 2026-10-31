import { useState } from 'react';
import { getLeagueWaivers } from '../../api/data';
import type { LeagueWaiversResponse, MyLeague } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PositionFilter } from '../../components/PositionFilter';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { filterToPosition, type PositionFilterValue } from '../../lib/positions';
import { WaiverRow } from '../waivers/WaiversPage';

function WaiverSummary({ data }: { data: LeagueWaiversResponse }) {
  const kind = data.league_type;
  const text = kind === 'faab'
    ? `FAAB league: ${data.my_balance != null ? `$${data.my_balance} left` : 'balance unknown'}${data.faab_budget ? ` of $${data.faab_budget}` : ''}. Bids follow your league's winning bids (estimates until there are 8).`
    : kind === 'priority'
      ? `Waiver priority league: you're ${data.my_priority ? `#${data.my_priority}` : 'unranked'}. Spend a top-3 priority only on a real difference-maker.`
      : kind === 'free_agency' ? 'Free agency: add players right away.' : null;
  return (
    <p className="hint">
      Ranked by how many points each pickup adds to your best lineup over the rest of the season ({data.weeks_left ?? '?'} weeks,
      playoff weeks weighted by your playoff odds), after dropping your least useful player. {text}
    </p>
  );
}

/** Free agents in your league, scored by lineup gain, with advice for your league's waiver type. */
export function WaiversView({ league }: { league: MyLeague }) {
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const position = filterToPosition(filter);
  const waivers = useAsync((signal) => getLeagueWaivers(league.id, { position, limit: 20 }, signal), [league.id, position]);
  return (
    <>
      <div className="weekly-controls"><PositionFilter value={filter} onChange={setFilter} /></div>
      <AsyncView state={waivers} loading={<TableSkeleton rows={6} columns={3} label="Finding free agents…" />}
        isEmpty={(d) => d.results.length === 0}
        empty={<EmptyState title="No free agents worth adding right now." />}>
        {(data) => (
          <>
          <WaiverSummary data={data} />
          <ol className="waiver-list" aria-label={`Free agents in ${data.league}`}>
            {data.results.map((row) => <WaiverRow key={row.player.id} row={row} />)}
          </ol>
          </>
        )}
      </AsyncView>
    </>
  );
}
