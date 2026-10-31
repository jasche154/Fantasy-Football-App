import { TradeFinder } from './TradeFinder';
import { useRef } from 'react';
import { getMyLeagues } from '../../api/data';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { useAsync } from '../../hooks/useAsync';
import { Card, PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { LiveStatus } from '../../components/LiveStatus';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { describeSettings } from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { TradeResultView, VERDICT_COPY } from './TradeResultView';
import { TradeSidePanel } from './TradeSidePanel';
import { useTradeState, type TradeState } from './useTradeState';

function TradeToolbar({ trade, onCleared }: { trade: TradeState; onCleared: () => void }) {
  const isEmpty = trade.ids.give.length === 0 && trade.ids.get.length === 0;
  return (
    <div className="toolbar" role="group" aria-label="Trade actions">
      <button type="button" className="btn btn--ghost" onClick={trade.swap} disabled={isEmpty}>
        <Icon name="swap" size={16} /> Swap sides
      </button>
      <button
        type="button"
        className="btn btn--ghost"
        disabled={isEmpty}
        onClick={() => {
          trade.clear();
          onCleared();
        }}
      >
        <Icon name="close" size={16} /> Clear
      </button>
    </div>
  );
}

function TradeEvaluation({ trade }: { trade: TradeState }) {
  if (!trade.isReady) {
    return (
      <EmptyState title="Add at least one player to each side.">
        The trade is evaluated automatically as you build it, and the page link updates so you can share it.
      </EmptyState>
    );
  }
  return (
    <>
      <LiveStatus
        state={trade.evaluation}
        loadingText="Evaluating trade…"
        describe={({ result }) => `${VERDICT_COPY[result.verdict].label}. ${result.summary}`}
      />
      <AsyncView
        state={trade.evaluation}
        loading={<Skeleton lines={4} label="Evaluating trade…" isAnnounced={false} />}
        errorTitle="Couldn't evaluate this trade"
      >
        {(keyed) => <TradeResultView result={keyed.result} onAdd={trade.add} />}
      </AsyncView>
    </>
  );
}

/** Imported Yahoo leagues to score the trade on; hidden when there are none (or they fail to load). */
function LeaguePicker({ trade }: { trade: TradeState }) {
  const leagues = useAsync((signal) => getMyLeagues(signal).catch(() => ({ leagues: [] })), []);
  const imported = (leagues.data?.leagues ?? []).map((l) => ({ value: String(l.id), label: l.name }));
  const selected = trade.ids.league === undefined ? '' : String(trade.ids.league);
  // A league from an old or shared link that isn't imported here stays visible so it can be cleared.
  const unknown = selected !== '' && !imported.some((o) => o.value === selected)
    ? [{ value: selected, label: `League #${selected} (not imported)` }]
    : [];
  if (imported.length === 0 && selected === '') return null;
  return (
    <div className="inline-form">
      <SelectField
        label="Score on"
        isInline
        value={selected}
        options={[{ value: '', label: 'Any league (no rosters)' }, ...imported, ...unknown]}
        onChange={(value) => trade.setLeague(value === '' ? undefined : Number(value))}
      />
    </div>
  );
}

export function TradePage({ query }: { query: Readonly<Record<string, string>> }) {
  useDocumentTitle('Trade Calculator');
  const { settings } = useLeagueSettings();
  const trade = useTradeState(query, settings);
  const giveSearchRef = useRef<HTMLInputElement>(null);
  const allIds = [...trade.ids.give, ...trade.ids.get];
  const result = trade.currentResult;
  const subtitle = trade.ids.league === undefined
    ? `Rest-of-season values for ${describeSettings(settings)}.`
    : 'Rest-of-season values and lineup gains on your imported league’s rosters and settings.';
  return (
    <>
      <PageHeader
        title="Trade calculator"
        subtitle={subtitle}
        actions={<TradeToolbar trade={trade} onCleared={() => giveSearchRef.current?.focus()} />}
      />
      <LeaguePicker trade={trade} />
      <div className="trade-grid">
        <TradeSidePanel
          side="give"
          ids={trade.ids.give}
          allIds={allIds}
          known={trade.known}
          result={result?.give}
          searchRef={giveSearchRef}
          onAdd={(player) => trade.add('give', player)}
          onRemove={(id) => trade.remove('give', id)}
        />
        <TradeSidePanel
          side="get"
          ids={trade.ids.get}
          allIds={allIds}
          known={trade.known}
          result={result?.get}
          onAdd={(player) => trade.add('get', player)}
          onRemove={(id) => trade.remove('get', id)}
        />
      </div>
      <Card title="Verdict" eyebrow="Projection (model)" className="card--verdict">
        <TradeEvaluation trade={trade} />
      </Card>
      <TradeFinder giveIds={trade.ids.give} league={trade.ids.league} />
    </>
  );
}
